"""Managed long jobs bound to Worker tasks: stable ids, fenced receipts, immutable cancellation."""

from __future__ import annotations

import datetime as dt
from typing import cast

from sqlalchemy import func, select, update

from ...application.ports import Record, UnitOfWork
from ...domain.contracts import BusinessError
from ...domain.omniweave import JOB_LIVE_STATES, JOB_TERMINAL_STATES, checked_job, goal_fingerprint
from ...domain.scheduling import instant, timestamp
from . import schema as s
from .omniweave_attempts import latest_run_id
from .omniweave_authority import evidence_authorized, mandate
from .repository_base import RepositoryBase

_jobs = s.owa_jobs
_RUN_FIELDS = (
    "session_id",
    "input_message_id",
    "config_revision",
    "settings_revision_id",
    "model_revision_id",
    "model_permission_epoch",
    "policy_epoch",
    "skill_capture_epoch",
    "extension_capture_epoch",
)


class OmniWeaveJobRepositories(RepositoryBase):
    def _job_by_run(self, agent_id: str, run_id: str) -> Record | None:
        row = (
            self.connection.execute(select(_jobs).where(_jobs.c.agent_id == agent_id, _jobs.c.run_id == run_id))
            .mappings()
            .first()
        )
        return cast(Record, dict(row)) if row else None

    def _job_claim_held(self, agent_id: str, run_id: str, token: str, fence: int) -> Record:
        work = (
            self.connection.execute(
                select(s.work_items).where(
                    s.work_items.c.agent_id == agent_id,
                    s.work_items.c.run_id == run_id,
                    s.work_items.c.kind == "turn",
                )
            )
            .mappings()
            .one()
        )
        payload = work["payload"]
        if not isinstance(payload, dict) or cast(Record, payload).get("owa_job") is None:
            raise BusinessError("owa_job_missing", "This Run carries no managed job", status=404)
        if (
            work["state"] not in ("claimed", "running")
            or work["claim_token"] != token
            or work["lease_fence"] != fence
            or work["claimed_by"] != self.boot_id
        ):
            raise BusinessError(
                "owa_job_fence_stale", "The executing claim expired; a stale executor cannot settle this job"
            )
        return cast(Record, dict(work))

    def _job_terminal(self, job: Record, state: str, receipt: Record, error_code: str) -> Record:
        changed = self.connection.execute(
            update(_jobs)
            .where(
                _jobs.c.agent_id == job["agent_id"],
                _jobs.c.id == job["id"],
                _jobs.c.state.not_in(JOB_TERMINAL_STATES),
            )
            .values(
                state=state,
                receipt=receipt,
                receipt_sha256=goal_fingerprint(receipt),
                ended_at=self.clock.now(),
                updated_at=self.clock.now(),
                error_code=error_code,
            )
        ).rowcount
        if changed != 1:
            raise BusinessError("owa_job_settled", "Another receipt already settled this job")
        return self._one(s.owa_jobs, str(job["agent_id"]), str(job["id"]))

    def _job_view(self, job: Record) -> Record:
        return {
            "job_id": job["id"],
            "ticket_id": job["ticket_id"],
            "task_id": job["task_id"],
            "goal_version": job["goal_version"],
            "key": job["request_key"],
            "tool": job["tool"],
            "arguments": job["arguments"],
            "state": job["state"],
            "action_id": job["action_id"],
            "cancel_requested": job["cancel_requested_at"] is not None,
            "deadline_at": job["deadline_at"],
            "resumes": job["resumes"],
            "receipt": job["receipt"],
            "error_code": job["error_code"],
            "created_at": job["created_at"],
            "ended_at": job["ended_at"],
        }

    def _job_current_goal_version(self, agent_id: str, ticket_id: str) -> int:
        root_id = self.connection.execute(
            select(func.coalesce(s.owa_tickets.c.root_ticket_id, s.owa_tickets.c.id)).where(
                s.owa_tickets.c.id == ticket_id, s.owa_tickets.c.agent_id == agent_id
            )
        ).scalar_one_or_none()
        if root_id is None:
            return 0
        tickets = s.owa_tickets
        return int(
            self.connection.execute(
                select(func.max(tickets.c.goal_version)).where(
                    tickets.c.agent_id == agent_id,
                    (tickets.c.id == root_id) | (tickets.c.root_ticket_id == root_id),
                )
            ).scalar_one()
        )

    def _job_phase(self, job: Record) -> Record:
        """Public detail phase: distinguishes waiting, executing, cleanup, cancel and review."""
        state = str(job["state"])
        phase = {
            "queued": "waiting",
            "running": "executing",
            "cancelling": "cleaning_up",
            "cancelled": "cancelled",
        }.get(state, state)
        review: Record | None = None
        if state not in ("cancelled", "queued") and job["action_id"] is not None:
            action = self._one(s.actions, str(job["agent_id"]), str(job["action_id"]))
            if str(action["state"]) == "unknown":
                # The dispatched external write has no outcome yet; only the owner
                # review flow may resolve it, and nothing may be replayed before.
                phase = "needs_review"
                review = {"action_id": str(job["action_id"]), "tool": str(action["tool"])}
        return {"phase": phase, "review": review}

    def omniweave_job_public(self, agent_id: str, job_id: str) -> Record:
        """Owner-visible job detail keyed by the stable public identity only."""
        job = self._one(_jobs, agent_id, job_id)
        view = self._job_view(job)
        view.update(self._job_phase(job))
        current = self._job_current_goal_version(agent_id, str(job["ticket_id"]))
        view["current_goal_version"] = current
        view["goal_superseded"] = int(str(job["goal_version"])) != current
        return view

    def omniweave_job_run(self, agent_id: str, job_id: str) -> str:
        job = self._one(_jobs, agent_id, job_id)
        if job["run_id"] is None:
            raise BusinessError("owa_job_missing", "This managed job has no executing Run", status=404)
        return str(job["run_id"])

    def omniweave_job_start(self, agent_id: str, run_id: str, request: Record) -> Record:
        unit = cast(UnitOfWork, self)
        captured = mandate(self, agent_id, run_id)
        if captured is None or captured["task_id"] is None:
            raise BusinessError(
                "owa_job_denied", "Managed jobs are only available inside an authorized Worker task", status=403
            )
        if str(captured["expires_at"]) <= self.clock.now():
            raise BusinessError("owa_mandate_expired", "OmniWeave task authorization expired")
        task_id = str(captured["task_id"])
        grants = cast(dict[str, Record], captured["tools"])
        key, arguments, deadline = checked_job(dict(request), tuple(grants))
        tool = str(request["tool"])
        sha = goal_fingerprint({"key": key, "tool": tool, "arguments": arguments, "deadline": deadline})
        prior = (
            self.connection.execute(
                select(_jobs).where(
                    _jobs.c.agent_id == agent_id, _jobs.c.task_id == task_id, _jobs.c.request_key == key
                )
            )
            .mappings()
            .first()
        )
        if prior is not None:
            if prior["request_sha256"] != sha:
                raise BusinessError("idempotency_conflict", "This key already identifies another managed job request")
            return {**self._job_view(cast(Record, dict(prior))), "duplicate": True}
        if (
            self.connection.execute(
                select(_jobs.c.id).where(
                    _jobs.c.agent_id == agent_id, _jobs.c.task_id == task_id, _jobs.c.state.in_(JOB_LIVE_STATES)
                )
            ).first()
            is not None
        ):
            raise BusinessError("owa_job_busy", "This task already runs one managed job", status=409)
        source_id = self.connection.execute(select(latest_run_id(task_id))).scalar_one()
        source = self._one(s.runs, agent_id, str(source_id))
        now = self.clock.now()
        if deadline is None:
            deadline_at = str(captured["expires_at"])
        else:
            deadline_at = min(
                timestamp(instant(now) + dt.timedelta(seconds=int(deadline))), str(captured["expires_at"])
            )
        unit.require_turn_capacity(1)
        run = self._record(
            s.runs,
            {
                **{name: source[name] for name in _RUN_FIELDS},
                "state": "queued",
                "execution_kind": "model",
                "last_seq": 0,
                "error_code": "",
            },
            agent_id,
        )
        job = self._record(
            _jobs,
            {
                "ticket_id": captured["ticket_id"],
                "task_id": task_id,
                "goal_version": captured["goal_version"],
                "request_key": key,
                "request_sha256": sha,
                "tool": tool,
                "arguments": arguments,
                "run_id": run["id"],
                "state": "queued",
                "deadline_at": deadline_at,
                "resumes": 0,
                "updated_at": now,
                "error_code": "",
            },
            agent_id,
        )
        self._record(
            s.work_items,
            {"run_id": run["id"], "kind": "turn", "state": "pending", "payload": {"owa_job": job["id"]}, "attempt": 0},
            agent_id,
        )
        unit.event(agent_id, str(run["id"]), "run.queued", {"purpose": "omniweave_job"})
        self.audit(agent_id, "owa_job.started", str(job["id"]), {"key": key, "tool": tool})
        return self._job_view(self._one(s.owa_jobs, agent_id, str(job["id"])))

    def omniweave_job_manifest(self, agent_id: str, run_id: str, token: str, fence: int) -> Record:
        self._job_claim_held(agent_id, run_id, token, fence)
        job = self._job_by_run(agent_id, run_id)
        if job is None:
            raise BusinessError("owa_job_missing", "This Run carries no managed job", status=404)
        if not evidence_authorized(self, agent_id, run_id):
            raise BusinessError("owa_mandate_revoked", "OmniWeave authority no longer matches the source")
        if job["state"] in JOB_TERMINAL_STATES:
            raise BusinessError("owa_job_settled", "This job was already settled by another executor")
        current = self._job_current_goal_version(agent_id, str(job["ticket_id"]))
        if int(str(job["goal_version"])) != current:
            raise BusinessError("owa_goal_superseded", "A newer goal revision superseded this managed job", status=409)
        if job["deadline_at"] is not None and str(job["deadline_at"]) <= self.clock.now():
            raise BusinessError("owa_job_deadline", "The managed job deadline passed before dispatch")
        return self._job_view(job)

    def omniweave_job_dispatch(self, agent_id: str, run_id: str, token: str, fence: int, job_id: str) -> None:
        unit = cast(UnitOfWork, self)
        unit.start_claimed_run(agent_id, run_id, token, fence)
        run = unit.get_run(agent_id, run_id)
        if run["state"] == "queued":
            unit.transition(agent_id, run_id, "running")
        job = self._one(s.owa_jobs, agent_id, job_id)
        if job["state"] == "queued":
            self.connection.execute(
                update(_jobs)
                .where(_jobs.c.agent_id == agent_id, _jobs.c.id == job_id)
                .values(state="running", updated_at=self.clock.now())
            )
            unit.event(agent_id, run_id, "job.dispatched", {"job_id": job_id})
            self.audit(agent_id, "owa_job.dispatched", job_id, {})

    def omniweave_job_bind(self, agent_id: str, run_id: str, action_id: str) -> None:
        job = self._job_by_run(agent_id, run_id)
        if job is None:
            raise BusinessError("owa_job_missing", "This Run carries no managed job", status=404)
        if job["state"] not in JOB_LIVE_STATES:
            raise BusinessError("owa_job_settled", "This job no longer accepts actions")
        self.connection.execute(
            update(_jobs)
            .where(_jobs.c.agent_id == agent_id, _jobs.c.id == job["id"])
            .values(action_id=action_id, updated_at=self.clock.now())
        )

    def omniweave_job_settle(
        self, agent_id: str, run_id: str, token: str, fence: int, outcome: str, error_code: str, receipt: Record
    ) -> Record:
        job = self._job_by_run(agent_id, run_id)
        if job is None:
            raise BusinessError("owa_job_missing", "This Run carries no managed job", status=404)
        if job["state"] in JOB_TERMINAL_STATES:
            # The receipt is immutable. A late executor only keeps its view; an
            # executor that still owns the claim releases it and converges the Run
            # to the recorded terminal state, never rewriting the receipt.
            try:
                self._job_claim_held(agent_id, run_id, token, fence)
            except BusinessError:
                return self._job_view(job)
            unit = cast(UnitOfWork, self)
            work_state = {"cancelled": "cancelled", "failed": "failed"}.get(str(job["state"]), "done")
            self._job_release(agent_id, run_id, token, fence, work_state)
            run_state = str(unit.get_run(agent_id, run_id)["state"])
            if str(job["state"]) == "succeeded":
                if run_state != "succeeded":
                    unit.finish(agent_id, run_id, f"Managed job {job['request_key']} · succeeded")
            elif run_state not in ("succeeded", "failed", "cancelled", "interrupted"):
                unit.transition(agent_id, run_id, str(job["state"]), str(job["error_code"]))
            return self._job_view(job)
        self._job_claim_held(agent_id, run_id, token, fence)
        unit = cast(UnitOfWork, self)
        run_state = unit.get_run(agent_id, run_id)["state"]
        state = "cancelled" if job["cancel_requested_at"] is not None or run_state == "cancelling" else outcome
        if state == "succeeded":
            if run_state == "waiting_approval":
                unit.transition(agent_id, run_id, "running")
        else:
            if unit.close_run_actions(agent_id, run_id) or error_code in (
                "action_outcome_unknown",
                "owa_job_outcome_unknown",
            ):
                state = "interrupted"
            elif state == "failed" and run_state in ("queued", "waiting_approval"):
                state = "interrupted"
            elif state == "cancelled" and not error_code:
                error_code = "owa_job_cancelled"

        body = {**receipt, "outcome": state, "error_code": error_code}
        settled = self._job_terminal(job, state, body, error_code)
        work_state = {"succeeded": "done", "interrupted": "failed"}.get(state, state)
        self._job_release(agent_id, run_id, token, fence, work_state)
        # Commit the job receipt, lease release and Run terminal state together.
        if state == "succeeded":
            unit.finish(agent_id, run_id, f"Managed job {job['request_key']} · {state}")
        else:
            unit.transition(agent_id, run_id, state, error_code)
        self.audit(agent_id, "owa_job.settled", str(job["id"]), {"state": state, "error_code": error_code})
        unit.event(agent_id, run_id, "job.settled", {"job_id": job["id"], "state": state, "error_code": error_code})
        return self._job_view(settled)

    def _job_release(self, agent_id: str, run_id: str, token: str, fence: int, work_state: str) -> None:
        unit = cast(UnitOfWork, self)
        run = unit.get_run(agent_id, run_id)
        changed = self.connection.execute(
            update(s.work_items)
            .where(
                s.work_items.c.run_id == run_id,
                s.work_items.c.agent_id == agent_id,
                s.work_items.c.kind == "turn",
                s.work_items.c.claim_token == token,
                s.work_items.c.lease_fence == fence,
                s.work_items.c.state.in_(("claimed", "running")),
            )
            .values(state=work_state, claimed_by=None, claim_token=None, lease_fence=None)
        ).rowcount
        if changed and run["session_id"]:
            unit.lease_release(agent_id, "session/" + str(run["session_id"]), token, fence)

    def omniweave_job_defer(self, agent_id: str, run_id: str, token: str, fence: int) -> None:
        job = self._job_by_run(agent_id, run_id)
        if job is None:
            return
        self._job_claim_held(agent_id, run_id, token, fence)
        if job["state"] in JOB_TERMINAL_STATES:
            # The receipt was settled elsewhere (revision, cancel or another
            # executor). This executor only releases its own claim; the lease and
            # the execution slot must not outlive the terminal receipt.
            self._job_release(
                agent_id,
                run_id,
                token,
                fence,
                {"cancelled": "cancelled", "failed": "failed"}.get(str(job["state"]), "failed"),
            )
            return
        unit = cast(UnitOfWork, self)
        run = unit.get_run(agent_id, run_id)
        if run["state"] == "cancelling" or job["cancel_requested_at"] is not None:
            self._job_release(agent_id, run_id, token, fence, "cancelled")
            if job["state"] != "cancelled":
                self._job_terminal(
                    job, "cancelled", {"outcome": "cancelled", "error_code": "owa_job_cancelled"}, "owa_job_cancelled"
                )
                self.audit(agent_id, "owa_job.settled", str(job["id"]), {"state": "cancelled"})
            if run["state"] == "cancelling":
                unit.transition(agent_id, run_id, "cancelled", "owa_job_cancelled")
            return
        self._job_release(agent_id, run_id, token, fence, "pending")
        if job["state"] == "running":
            self.connection.execute(
                update(_jobs)
                .where(_jobs.c.agent_id == agent_id, _jobs.c.id == job["id"])
                .values(state="queued", updated_at=self.clock.now())
            )

    def omniweave_job_cancel(self, agent_id: str, run_id: str) -> None:
        job = self._job_by_run(agent_id, run_id)
        if job is None or job["state"] in JOB_TERMINAL_STATES:
            return
        unit = cast(UnitOfWork, self)
        if job["state"] == "queued":
            # The enclosing Run cancellation releases any session claim.
            self._job_terminal(
                job, "cancelled", {"outcome": "cancelled", "error_code": "owa_job_cancelled"}, "owa_job_cancelled"
            )
            self.audit(agent_id, "owa_job.settled", str(job["id"]), {"state": "cancelled"})
            return
        if job["cancel_requested_at"] is None:
            self.connection.execute(
                update(_jobs)
                .where(_jobs.c.agent_id == agent_id, _jobs.c.id == job["id"])
                .values(state="cancelling", cancel_requested_at=self.clock.now(), updated_at=self.clock.now())
            )
            self.audit(agent_id, "owa_job.cancel_requested", str(job["id"]), {})
            unit.event(agent_id, run_id, "job.cancel_requested", {"job_id": job["id"]})

    def omniweave_job_recover(self) -> None:
        rows = (
            self.connection.execute(
                select(_jobs, s.runs.c.state.label("run_state"), s.runs.c.error_code.label("run_error"))
                .join(s.runs, s.runs.c.id == _jobs.c.run_id)
                .where(_jobs.c.state.in_(JOB_LIVE_STATES))
            )
            .mappings()
            .all()
        )
        for row in rows:
            job = cast(Record, dict(row))
            state, error = str(row["run_state"]), str(row["run_error"] or "process_interrupted")
            if state in ("cancelled", "failed"):
                self._job_terminal(job, state, {"outcome": state, "error_code": error}, error)
            elif state == "interrupted":
                self._job_terminal(
                    cast(Record, dict(row)),
                    "interrupted",
                    {"outcome": "interrupted", "error_code": error},
                    error,
                )
            elif state == "queued" and job["state"] != "queued":
                self.connection.execute(
                    update(_jobs)
                    .where(_jobs.c.id == job["id"], _jobs.c.agent_id == job["agent_id"])
                    .values(state="queued", updated_at=self.clock.now())
                )
