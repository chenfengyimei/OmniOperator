"""Use the turn queue's existing payload, instance lock and lease for safe restart."""

from __future__ import annotations

from typing import cast

from sqlalchemy import literal_column, select, update

from ...application.ports import Record, UnitOfWork
from ...domain.contracts import JSON, BusinessError, Invocation
from ...domain.run_recovery import MAX_AUTO_RESUMES, MAX_CHECKPOINT_ACTIONS, RECOVERABLE_TOOLS, arguments_digest
from . import schema as s
from .lease_repository import LeaseRepositories


class RunCheckpointRepositories(LeaseRepositories):
    def _checkpoint_actions(self, agent_id: str, run_id: str) -> list[Record]:
        rows = self.connection.execute(
            select(
                s.actions.c.id, s.actions.c.tool, s.actions.c.arguments_sha256, s.actions.c.state, s.actions.c.effect
            )
            .where(s.actions.c.agent_id == agent_id, s.actions.c.run_id == run_id)
            .order_by(literal_column("actions.rowid"))
            .limit(MAX_CHECKPOINT_ACTIONS + 1)
        ).mappings()
        return [dict(row) for row in rows]

    def _checkpoint_calls(self, agent_id: str, run_id: str) -> list[Record]:
        rows = self.connection.execute(
            select(s.model_calls.c.id, s.model_calls.c.state)
            .where(s.model_calls.c.agent_id == agent_id, s.model_calls.c.run_id == run_id)
            .order_by(literal_column("model_calls.rowid"))
            .limit(150)
        ).mappings()
        return [dict(row) for row in rows]

    def _checkpoint_store(self, work: Record, checkpoint: Record) -> None:
        self.connection.execute(update(s.work_items).where(s.work_items.c.id == work["id"]).values(payload=checkpoint))

    def save_run_checkpoint(
        self, agent_id: str, run_id: str, next_step: int, model_revision_id: str, calls: tuple[Invocation, ...] = ()
    ) -> None:
        unit = cast(UnitOfWork, self)
        unit.assert_run_authorized(agent_id, run_id)
        unit.assert_context(agent_id, run_id)
        run = self._one(s.runs, agent_id, run_id)
        work = self._work(agent_id, run_id)
        previous = cast(Record, work["payload"])
        actions, model_calls = self._checkpoint_actions(agent_id, run_id), self._checkpoint_calls(agent_id, run_id)
        session = self._one(s.sessions, agent_id, str(run["session_id"]))
        if (
            len(actions) + len(calls) > MAX_CHECKPOINT_ACTIONS
            or len(model_calls) >= 150
            or any(row["state"] not in {"settled", "not_sent"} for row in model_calls)
            or any(
                row["tool"] not in RECOVERABLE_TOOLS or row["state"] != "ok" or row["effect"] == "unknown"
                for row in actions
            )
            or any(call.name not in RECOVERABLE_TOOLS for call in calls)
        ):
            self._checkpoint_store(work, {**previous, "decision": "review", "reason": "uncertain_or_unsupported_step"})
            return
        planned: list[JSON] = [
            {"tool": call.name, "arguments_sha256": arguments_digest(call.arguments)} for call in calls
        ]
        self._checkpoint_store(
            work,
            {
                "schema": 1,
                "session_epoch": session["content_epoch"],
                "policy_epoch": run["policy_epoch"],
                "authority": unit.model_dispatch_authority(agent_id, run_id, model_revision_id),
                "model_calls": cast(list[JSON], model_calls),
                "confirmed": cast(list[JSON], actions),
                "planned": planned,
                "next_step": next_step,
                "resumes": previous.get("resumes", 0),
                "decision": "resumed" if previous.get("resumes") else "checkpoint",
                "reason": "",
            },
        )

    def _checkpoint_valid(self, run: Record, checkpoint: Record) -> tuple[str, list[Record]]:
        agent_id, run_id = str(run["agent_id"]), str(run["id"])
        unit = cast(UnitOfWork, self)
        if run["state"] == "waiting_approval":
            return "approval_must_be_reviewed_again", []
        if run["state"] not in {"running", "queued"} or checkpoint.get("decision") == "review":
            return "uncertain_or_unsupported_step", []
        session = self._one(s.sessions, agent_id, str(run["session_id"]))
        message = self._one(s.messages, agent_id, str(run["input_message_id"]))
        if session["deleted_at"] or message["deleted_at"] or checkpoint["session_epoch"] != session["content_epoch"]:
            return "source_changed", []
        try:
            policy = cast(Record, unit.route_policy(agent_id)["kernel"])
            current_epoch = unit.settings(agent_id)["policy_revision"]
        except BusinessError:
            return "owner_policy_changed", []
        if not policy["safe_resume"] or current_epoch != checkpoint["policy_epoch"]:
            return "owner_policy_changed", []
        try:
            authority = cast(Record, checkpoint["authority"])
            if unit.model_dispatch_authority(agent_id, run_id, str(authority["model_revision_id"])) != authority:
                return "model_authority_changed", []
        except BusinessError:
            return "model_authority_changed", []
        if self._checkpoint_calls(agent_id, run_id) != checkpoint["model_calls"]:
            return "model_boundary_unconfirmed", []
        if unit.delegation_children(agent_id, run_id) or any(
            self.connection.execute(
                select(table.c.id).where(table.c.run_id == run_id, table.c.agent_id == agent_id).limit(1)
            ).first()
            for table in (s.outbox, s.clarifications)
        ):
            return "other_operation_requires_review", []
        confirmed, planned = cast(list[Record], checkpoint["confirmed"]), cast(list[Record], checkpoint["planned"])
        actions = self._checkpoint_actions(agent_id, run_id)
        following = actions[len(confirmed) :]
        if (
            actions[: len(confirmed)] != confirmed
            or len(following) != len(planned)
            or any(
                row["state"] != "ok"
                or row["effect"] == "unknown"
                or row["tool"] != expected["tool"]
                or row["arguments_sha256"] != expected["arguments_sha256"]
                for row, expected in zip(following, planned, strict=True)
            )
        ):
            return "tool_boundary_unconfirmed", []
        return "", actions

    def requeue_checkpoint(self, run: Record) -> bool:
        """Called once at boot while the database home is exclusively owned."""
        agent_id, run_id = str(run["agent_id"]), str(run["id"])
        unit = cast(UnitOfWork, self)
        work = self._work(agent_id, run_id)
        checkpoint = cast(Record, work["payload"])
        job_id = checkpoint.get("owa_job")
        if job_id is not None:
            return self._requeue_job(agent_id, run_id, work, str(job_id))
        if checkpoint.get("schema") != 1:
            return False
        resumes = cast(int, checkpoint["resumes"])
        reason, actions = self._checkpoint_valid(run, checkpoint)
        if reason or resumes >= MAX_AUTO_RESUMES:
            self._checkpoint_store(work, {**checkpoint, "decision": "review", "reason": reason or "restart_limit"})
            return False
        self._requeue_turn(run_id, str(work["id"]))
        self._checkpoint_store(
            work,
            {
                **checkpoint,
                "confirmed": cast(list[JSON], actions),
                "planned": [],
                "resumes": resumes + 1,
                "decision": "requeued",
            },
        )
        unit.event(
            agent_id,
            run_id,
            "run.resume_queued",
            {"confirmed_actions": len(actions), "next_step": checkpoint["next_step"]},
        )
        return True

    def _requeue_job(self, agent_id: str, run_id: str, work: Record, job_id: str) -> bool:
        """A managed job resumes only from a proven boundary: no dispatched-but-unsettled action."""
        unit = cast(UnitOfWork, self)
        job = (
            self.connection.execute(
                select(s.owa_jobs).where(s.owa_jobs.c.agent_id == agent_id, s.owa_jobs.c.id == job_id)
            )
            .mappings()
            .first()
        )
        if job is None or job["state"] in ("succeeded", "failed", "cancelled", "interrupted"):
            return False
        action_state = ""
        if job["action_id"]:
            action_state = str(
                self.connection.execute(
                    select(s.actions.c.state).where(
                        s.actions.c.agent_id == agent_id, s.actions.c.id == job["action_id"]
                    )
                ).scalar_one_or_none()
                or ""
            )
        resumes = cast(int, job["resumes"])
        if action_state in ("unknown", "executing") or resumes >= MAX_AUTO_RESUMES:
            return False
        self._requeue_turn(run_id, str(work["id"]))
        self.connection.execute(
            update(s.owa_jobs)
            .where(s.owa_jobs.c.agent_id == agent_id, s.owa_jobs.c.id == job_id)
            .values(resumes=resumes + 1, state="queued", updated_at=self.clock.now())
        )
        unit.event(agent_id, run_id, "run.resume_queued", {"job_id": job_id, "action_state": action_state})
        return True

    def consume_run_checkpoint(self, agent_id: str, run_id: str) -> Record | None:
        work = self._work(agent_id, run_id)
        checkpoint = cast(Record, work["payload"])
        if checkpoint.get("decision") != "requeued":
            return None
        unit = cast(UnitOfWork, self)
        run = self._one(s.runs, agent_id, run_id)
        reason, confirmed = self._checkpoint_valid(run, checkpoint)
        if reason:
            self._checkpoint_store(work, {**checkpoint, "decision": "review", "reason": reason})
            return {"reason": reason}
        receipts: list[JSON] = []
        for row in confirmed[-16:]:
            action = unit.action(agent_id, str(row["id"]))
            result = action["result"]
            data = result.get("data") if isinstance(result, dict) else None
            receipt: Record = {"action_id": row["id"], "tool": row["tool"], "status": row["state"]}
            if isinstance(data, dict) and data.get("workspace_path"):
                receipt.update(path=data["workspace_path"], version=data.get("sha256"))
            receipts.append(receipt)
        self._checkpoint_store(work, {**checkpoint, "decision": "resumed"})
        unit.event(
            agent_id, run_id, "run.resumed", {"confirmed_actions": len(confirmed), "next_step": checkpoint["next_step"]}
        )
        return {
            "next_step": checkpoint["next_step"],
            "confirmed_actions": len(confirmed),
            "receipts": receipts,
            "model_revision_id": cast(Record, checkpoint["authority"])["model_revision_id"],
        }

    def run_continuation(self, agent_id: str, run_id: str) -> Record | None:
        raw = self.connection.execute(
            select(s.work_items.c.payload).where(
                s.work_items.c.run_id == run_id, s.work_items.c.agent_id == agent_id, s.work_items.c.kind == "turn"
            )
        ).scalar_one_or_none()
        if not isinstance(cast(object, raw), dict):
            return None
        checkpoint = cast(Record, raw)
        if not checkpoint.get("resumes") and checkpoint.get("decision") != "review":
            return None
        return {key: checkpoint[key] for key in ("decision", "reason", "resumes", "next_step") if key in checkpoint}
