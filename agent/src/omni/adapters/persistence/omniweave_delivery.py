"""Reduce durable Worker results into the original private Web conversation."""

from typing import cast

from sqlalchemy import func, select, update

from ...application.ports import Record, UnitOfWork
from ...domain.action_review import TERMINAL_RUNS
from ...domain.contracts import JSON, BusinessError
from ...domain.omniweave import goal_fingerprint
from ...domain.omniweave_artifacts import ARTIFACT_MEDIA_TYPE
from . import schema as s
from .omniweave_artifacts import artifact_view, current_artifact_version, owa_artifacts
from .omniweave_attempts import latest_run_id
from .omniweave_authority import authorize_mandate, evidence_authorized, mandate
from .omniweave_goals import current_goal, goal_blocker, goal_runs
from .omniweave_mailbox import OmniWeaveMailboxRepositories, waiting_for_peer

_deliveries = s.owa_deliveries


class OmniWeaveDeliveryRepositories(OmniWeaveMailboxRepositories):
    def omniweave_delivery_links(self, agent_id: str, session_id: str, messages: list[str]) -> dict[str, Record]:
        if not messages:
            return {}
        rows = self.connection.execute(
            select(
                _deliveries.c.message_id, _deliveries.c.outcome, _deliveries.c.evidence, s.owa_tickets.c.goal_version
            )
            .select_from(_deliveries)
            .join(s.owa_tickets, s.owa_tickets.c.id == _deliveries.c.ticket_id)
            .join(s.owa_threads, s.owa_threads.c.id == s.owa_tickets.c.thread_id)
            .join(s.instances, (s.instances.c.slot == 1) & (s.instances.c.owner_id == s.owa_threads.c.owner_id))
            .join(s.history_identity, s.history_identity.c.slot == 1)
            .join(s.sessions, s.sessions.c.id == s.owa_threads.c.main_session_id)
            .join(s.owa_mandates, s.owa_mandates.c.run_id == s.owa_tickets.c.run_id)
            .where(
                _deliveries.c.agent_id == agent_id,
                _deliveries.c.message_id.in_(messages),
                s.owa_threads.c.main_session_id == session_id,
                s.owa_mandates.c.history_epoch == s.history_identity.c.epoch,
                s.owa_mandates.c.session_epoch == s.sessions.c.content_epoch,
                s.sessions.c.deleted_at.is_(None),
                current_goal(),
            )
        ).all()
        return {
            message: {
                "outcome": outcome,
                "goal_version": version,
                "artifacts": cast(list[JSON], evidence["artifacts"])
                + cast(list[JSON], evidence.get("retained_artifacts", [])),
            }
            for message, outcome, evidence, version in rows
        }

    def omniweave_owner_artifact(self, agent_id: str, session_id: str, artifact_id: str) -> Record:
        link = self.connection.execute(
            select(s.owa_artifacts.c.ticket_id)
            .join(s.owa_tickets, s.owa_tickets.c.id == s.owa_artifacts.c.ticket_id)
            .join(s.owa_threads, s.owa_threads.c.id == s.owa_tickets.c.thread_id)
            .join(s.instances, (s.instances.c.slot == 1) & (s.instances.c.owner_id == s.owa_threads.c.owner_id))
            .where(
                s.owa_artifacts.c.agent_id == agent_id,
                s.owa_artifacts.c.id == artifact_id,
                s.owa_threads.c.main_session_id == session_id,
                current_goal(),
            )
        ).scalar_one_or_none()
        rows = self._owa_public_artifacts(agent_id, link, artifact_id) if link is not None else []
        if not rows:
            rows = self._owa_retained_owner_artifact(agent_id, session_id, artifact_id)
        if not rows:
            raise BusinessError("not_found", "Artifact is outside the current main conversation delivery", status=404)
        return {**cast(Record, rows[0]), "agent_id": agent_id}

    def _owa_retained_owner_artifact(self, agent_id: str, session_id: str, artifact_id: str) -> list[JSON]:
        reuse_ticket = s.owa_tickets.alias("retained_ticket")
        later = s.owa_tickets.alias("retained_goal_later")
        current = (
            ~select(later.c.id)
            .where(
                later.c.root_ticket_id == func.coalesce(reuse_ticket.c.root_ticket_id, reuse_ticket.c.id),
                later.c.goal_version > reuse_ticket.c.goal_version,
            )
            .exists()
        )
        producer = self.connection.execute(
            select(owa_artifacts.c.producer_id)
            .join(s.owa_tasks, s.owa_tasks.c.id == owa_artifacts.c.producer_id)
            .join(s.owa_task_reuse, s.owa_task_reuse.c.source_task_id == s.owa_tasks.c.id)
            .join(reuse_ticket, reuse_ticket.c.id == s.owa_task_reuse.c.ticket_id)
            .join(s.owa_threads, s.owa_threads.c.id == reuse_ticket.c.thread_id)
            .join(s.instances, (s.instances.c.slot == 1) & (s.instances.c.owner_id == s.owa_threads.c.owner_id))
            .where(
                owa_artifacts.c.agent_id == agent_id,
                owa_artifacts.c.id == artifact_id,
                s.owa_task_reuse.c.agent_id == agent_id,
                s.owa_threads.c.main_session_id == session_id,
                current,
            )
        ).scalar_one_or_none()
        if producer is None:
            return []
        return [item for item in self._owa_retained_artifacts(agent_id, producer) if item["artifact_id"] == artifact_id]

    def omniweave_delivery_tick(self, after: str) -> str:
        tickets = s.owa_tickets
        active = (
            select(s.owa_tasks.c.id)
            .select_from(s.owa_tasks)
            .join(s.runs, s.runs.c.id == latest_run_id(s.owa_tasks.c.id))
            .where(s.owa_tasks.c.ticket_id == tickets.c.id, s.runs.c.state.not_in(TERMINAL_RUNS))
            .correlate(tickets)
            .exists()
        )
        rows = self.connection.execute(
            select(tickets.c.id, tickets.c.agent_id, tickets.c.run_id)
            .select_from(tickets)
            .join(s.owa_executions, s.owa_executions.c.ticket_id == tickets.c.id)
            .join(s.runs, s.runs.c.id == tickets.c.run_id)
            .join(s.agents, s.agents.c.id == tickets.c.agent_id)
            .where(
                tickets.c.id > after,
                s.agents.c.enabled.is_(True),
                s.owa_executions.c.mandate_required.is_(True),
                s.owa_executions.c.plan_sha256.is_not(None),
                s.owa_executions.c.cancelled_at.is_(None),
                s.runs.c.state == "succeeded",
                current_goal(),
                ~active,
                ~select(_deliveries.c.id).where(_deliveries.c.ticket_id == tickets.c.id).exists(),
            )
            .order_by(tickets.c.id)
            .limit(16)
        ).all()
        unit = cast(UnitOfWork, self)
        for ticket_id, agent_id, source_id in rows:
            evidence = self._owa_delivery_evidence(agent_id, source_id)
            if evidence is None:
                continue
            try:
                unit.require_turn_capacity(1)
            except BusinessError as exc:
                if exc.code != "queue_full":
                    raise
                return after
            source = unit.get_run(agent_id, source_id)
            run = self._record(
                s.runs,
                {
                    **{
                        key: source[key]
                        for key in (
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
                    },
                    "state": "queued",
                    "execution_kind": "model",
                    "last_seq": 0,
                    "error_code": "",
                },
                agent_id,
            )
            run_id = str(run["id"])
            self._record(_deliveries, {"ticket_id": ticket_id, "run_id": run_id}, agent_id)
            self._record(
                s.work_items,
                {"run_id": run_id, "kind": "turn", "state": "pending", "payload": {"owa_delivery": True}, "attempt": 0},
                agent_id,
            )
            unit.event(agent_id, run_id, "run.queued", {"purpose": "omniweave_delivery"})
        return str(rows[-1].id) if len(rows) == 16 else ""

    def _owa_delivery_evidence(self, agent_id: str, source_id: str) -> Record | None:
        unit = cast(UnitOfWork, self)
        execution = unit.omniweave_router(agent_id, source_id)
        if execution is None or execution["cancelled_at"] is not None or not execution["plan_sha256"]:
            return None
        try:
            authorize_mandate(self, agent_id, source_id, execution)
            unit.assert_model_authorized(agent_id, source_id)
        except BusinessError:
            return None
        root_id, ticket_id = str(execution["root_ticket_id"]), str(execution["ticket_id"])
        if goal_blocker(self, goal_runs(agent_id, root_id)):
            return None
        attempts = self.connection.execute(
            select(
                s.owa_tasks.c.task_key,
                s.owa_task_attempts.c.run_id,
                s.runs.c.state,
                s.runs.c.error_code,
                s.owa_mandates.c.profile,
            )
            .select_from(s.owa_tasks)
            .join(s.owa_task_attempts, s.owa_task_attempts.c.task_id == s.owa_tasks.c.id)
            .join(
                s.owa_mandates,
                (s.owa_mandates.c.task_id == s.owa_tasks.c.id)
                & (s.owa_mandates.c.goal_version == s.owa_task_attempts.c.goal_version),
            )
            .join(s.runs, s.runs.c.id == s.owa_task_attempts.c.run_id)
            .where(s.owa_tasks.c.ticket_id == ticket_id)
            .order_by(s.owa_tasks.c.task_key, s.owa_task_attempts.c.attempt_no)
        ).all()
        tasks: dict[str, Record] = {}
        verifiers: dict[str, list[str]] = {}
        for key, identity, state, error, profile in attempts:
            verification = unit.run_completion(agent_id, identity)["state"]
            clean = verification in ("verified", "not_required")
            tasks[key] = {
                "key": key,
                "run_id": identity,
                "state": state,
                "error_code": error,
                "verification_passed": clean and (key not in tasks or tasks[key]["verification_passed"] is True),
            }
            if profile == "verification":
                verifiers.setdefault(key, []).append(identity)
        retained_artifacts: list[JSON] = []
        reused_verifiers: dict[str, list[str]] = {}
        for key, source_id, source_version in self.connection.execute(
            select(
                s.owa_task_reuse.c.task_key,
                s.owa_task_reuse.c.source_task_id,
                s.owa_task_reuse.c.source_goal_version,
            )
            .where(s.owa_task_reuse.c.agent_id == agent_id, s.owa_task_reuse.c.ticket_id == ticket_id)
            .order_by(s.owa_task_reuse.c.task_key)
        ).all():
            if key in tasks:
                return None
            prior_run = self.connection.execute(select(latest_run_id(str(source_id)))).scalar_one_or_none()
            if prior_run is None:
                return None
            run = self._one(s.runs, agent_id, prior_run)
            source_mandate = mandate(self, agent_id, prior_run)
            verified = (
                run["state"] == "succeeded"
                and unit.run_completion(agent_id, prior_run)["state"] in ("verified", "not_required")
                and source_mandate is not None
                and cast(int, source_mandate["goal_version"]) == cast(int, source_version)
                and evidence_authorized(self, agent_id, prior_run)
            )
            tasks[key] = {
                "key": key,
                "run_id": prior_run,
                "state": str(run["state"]),
                "error_code": str(run["error_code"]),
                "verification_passed": verified,
                "reused_from_version": source_version,
            }
            if verified and source_mandate is not None and source_mandate["profile"] == "verification":
                reused_verifiers.setdefault(key, []).append(prior_run)
            retained_artifacts.extend(cast(list[JSON], self._owa_retained_artifacts(agent_id, str(source_id))))
        planned_count = (
            self.connection.execute(
                select(func.count()).select_from(s.owa_tasks).where(s.owa_tasks.c.ticket_id == ticket_id)
            ).scalar_one()
            + self.connection.execute(
                select(func.count())
                .select_from(s.owa_task_reuse)
                .where(s.owa_task_reuse.c.agent_id == agent_id, s.owa_task_reuse.c.ticket_id == ticket_id)
            ).scalar_one()
        )
        if (
            not tasks
            or len(tasks) != planned_count
            or any(
                item["state"] == "succeeded" and waiting_for_peer(self, agent_id, str(item["run_id"]))
                for item in tasks.values()
            )
        ):
            return None
        artifacts = self._owa_public_artifacts(agent_id, ticket_id)
        versioned = {str(item["artifact_id"]): str(item["sha256"]) for item in cast(list[Record], artifacts)}
        readable = {
            **versioned,
            **{str(item["artifact_id"]): str(item["sha256"]) for item in cast(list[Record], retained_artifacts)},
        }
        grants: list[tuple[str, str, str | None]] = []
        if readable and (verifiers or reused_verifiers):
            # A verifier can only have consumed artifacts published before its
            # latest attempt ended; later grants belong to a different round.
            windows: dict[str, str] = {}
            for key, identities in {**verifiers, **reused_verifiers}.items():
                windows[key] = str(
                    self.connection.execute(
                        select(func.max(s.runs.c.ended_at)).where(s.runs.c.id.in_(identities))
                    ).scalar_one()
                    or ""
                )
            rows = self.connection.execute(
                select(
                    s.owa_tasks.c.task_key,
                    s.owa_artifact_grants.c.artifact_id,
                    s.owa_artifact_grants.c.revoked_at,
                    owa_artifacts.c.created_at,
                )
                .select_from(s.owa_artifact_grants)
                .join(s.owa_artifact_links, s.owa_artifact_links.c.id == s.owa_artifact_grants.c.link_id)
                .join(s.owa_tasks, s.owa_tasks.c.id == s.owa_artifact_links.c.reader_id)
                .join(owa_artifacts, owa_artifacts.c.id == s.owa_artifact_grants.c.artifact_id)
                .where(s.owa_artifact_grants.c.artifact_id.in_(list(readable)))
            ).all()
            grants = [
                (str(reader), str(artifact), cast(str | None, revoked))
                for reader, artifact, revoked, created in rows
                if str(created) <= windows.get(str(reader), "")
            ]
        for key, identities in {**verifiers, **reused_verifiers}.items():
            assigned = [(artifact, revoked_at) for reader, artifact, revoked_at in grants if reader == key]
            expected = {(artifact, readable[artifact]) for artifact, _ in assigned}
            if expected and (
                any(revoked_at is not None for _, revoked_at in assigned)
                or not expected <= self._owa_verified_artifacts(agent_id, identities)
            ):
                tasks[key]["verification_passed"] = False
        accepted = sum(item["state"] == "succeeded" and item["verification_passed"] is True for item in tasks.values())
        outcome = "completed" if accepted == len(tasks) else "partial" if accepted or artifacts else "failed"
        return {
            "ticket_id": root_id,
            "goal_version": execution["goal_version"],
            "outcome": outcome,
            "tasks": cast(list[JSON], list(tasks.values())),
            "artifacts": artifacts,
            "retained_artifacts": retained_artifacts,
        }

    def _owa_retained_artifacts(self, agent_id: str, producer_task_id: str) -> list[Record]:
        rows = (
            self.connection.execute(
                select(owa_artifacts, current_artifact_version().label("current_version"))
                .where(
                    owa_artifacts.c.agent_id == agent_id,
                    owa_artifacts.c.producer_id == producer_task_id,
                    current_artifact_version(),
                )
                .order_by(owa_artifacts.c.created_at, owa_artifacts.c.id)
            )
            .mappings()
            .all()
        )
        result: list[Record] = []
        for row in rows:
            run_id = self.connection.execute(select(latest_run_id(row["producer_id"]))).scalar_one_or_none()
            if run_id is None:
                continue
            captured = mandate(self, agent_id, run_id)
            action = self._one(s.actions, agent_id, row["action_id"])
            if (
                captured is None
                or captured["goal_version"] != row["goal_version"]
                or action["body_pruned_at"]
                or not evidence_authorized(self, agent_id, run_id)
            ):
                continue
            obj = self._one(s.objects, agent_id, row["object_id"])
            if obj["media_type"] != ARTIFACT_MEDIA_TYPE:
                continue
            result.append({**artifact_view(cast(Record, dict(row)), obj)})
        return result

    def _owa_verified_artifacts(self, agent_id: str, run_ids: list[str]) -> set[tuple[str, str]]:
        rows = self.connection.execute(
            select(
                func.json_extract(s.actions.c.result, "$.data.artifact_id"),
                func.json_extract(s.actions.c.result, "$.data.sha256"),
            ).where(
                s.actions.c.agent_id == agent_id,
                s.actions.c.run_id.in_(run_ids),
                s.actions.c.tool == "read_shared",
                s.actions.c.state == "ok",
                s.actions.c.body_pruned_at.is_(None),
            )
        ).all()
        return {(key, digest) for key, digest in rows if isinstance(key, str) and isinstance(digest, str)}

    def _owa_delivery_target(self, agent_id: str, run_id: str) -> tuple[str, str] | None:
        row = self.connection.execute(
            select(_deliveries.c.id, s.owa_tickets.c.run_id.label("source_id"))
            .select_from(_deliveries)
            .join(s.owa_tickets, s.owa_tickets.c.id == _deliveries.c.ticket_id)
            .join(s.runs, s.runs.c.id == _deliveries.c.run_id)
            .where(
                _deliveries.c.agent_id == agent_id,
                _deliveries.c.run_id == run_id,
                _deliveries.c.message_id.is_(None),
                s.runs.c.state == "queued",
            )
        ).one_or_none()
        return (row.id, row.source_id) if row else None

    def omniweave_delivery_manifest(self, agent_id: str, run_id: str, token: str, fence: int) -> Record | None:
        target = self._owa_delivery_target(agent_id, run_id)
        if target is None:
            return None
        cast(UnitOfWork, self).start_claimed_run(agent_id, run_id, token, fence)
        return self._owa_delivery_evidence(agent_id, target[1])

    def omniweave_defer_delivery(self, agent_id: str, run_id: str, token: str, fence: int) -> None:
        if self._owa_delivery_target(agent_id, run_id) is None:
            return
        unit = cast(UnitOfWork, self)
        run = unit.get_run(agent_id, run_id)
        changed = self.connection.execute(
            update(s.work_items)
            .where(
                s.work_items.c.run_id == run_id,
                s.work_items.c.agent_id == agent_id,
                s.work_items.c.kind == "turn",
                s.work_items.c.state == "claimed",
                s.work_items.c.claim_token == token,
                s.work_items.c.lease_fence == fence,
            )
            .values(state="pending", claim_token=None, lease_fence=None, claimed_by=None)
        ).rowcount
        if changed:
            unit.lease_release(agent_id, "session/" + str(run["session_id"]), token, fence)

    def omniweave_finish_delivery(
        self,
        agent_id: str,
        run_id: str,
        token: str,
        fence: int,
        fingerprint: str,
        missing_artifacts: frozenset[str],
    ) -> None:
        unit = cast(UnitOfWork, self)
        target = self._owa_delivery_target(agent_id, run_id)
        if target is None:
            return
        identity, source_id = target
        unit.start_claimed_run(agent_id, run_id, token, fence)
        evidence = self._owa_delivery_evidence(agent_id, source_id)
        if evidence is None or goal_fingerprint(evidence) != fingerprint:
            unit.transition(agent_id, run_id, "cancelled", "owa_delivery_recheck")
            self.connection.execute(_deliveries.delete().where(_deliveries.c.id == identity))
            return
        if missing_artifacts:
            artifacts = cast(list[Record], evidence["artifacts"])
            unavailable = [
                item
                for item in artifacts + cast(list[Record], evidence.get("retained_artifacts", []))
                if item["artifact_id"] in missing_artifacts
            ]
            evidence["unavailable_artifacts"] = cast(list[JSON], unavailable)
            evidence["artifacts"] = cast(
                list[JSON], [item for item in artifacts if item["artifact_id"] not in missing_artifacts]
            )
            evidence["retained_artifacts"] = cast(
                list[JSON],
                [
                    item
                    for item in cast(list[Record], evidence.get("retained_artifacts", []))
                    if item["artifact_id"] not in missing_artifacts
                ],
            )
            has_result = bool(evidence["artifacts"]) or any(
                item["state"] == "succeeded" and item["verification_passed"] is True
                for item in cast(list[Record], evidence["tasks"])
            )
            evidence["outcome"] = "partial" if has_result else "failed"
        unit.transition(agent_id, run_id, "running")
        unit.finish(agent_id, run_id, self._owa_delivery_text(evidence))
        message = self.connection.execute(
            select(s.messages.c.id).where(
                s.messages.c.agent_id == agent_id, s.messages.c.run_id == run_id, s.messages.c.role == "assistant"
            )
        ).scalar_one()
        self.connection.execute(
            update(_deliveries)
            .where(_deliveries.c.id == identity)
            .values(message_id=message, outcome=evidence["outcome"], evidence=evidence)
        )
        unit.event(
            agent_id,
            run_id,
            "omniweave.delivered",
            {name: evidence[name] for name in ("ticket_id", "goal_version", "outcome")} | {"message_id": message},
        )

    @staticmethod
    def _owa_delivery_text(evidence: Record) -> str:
        state = {"completed": "已完成", "partial": "部分完成", "failed": "未完成"}[str(evidence["outcome"])]
        lines = [f"OmniWeave 交付 · 第 {evidence['goal_version']} 版 · {state}"]
        for task in cast(list[Record], evidence["tasks"]):
            if task["state"] == "succeeded":
                detail = "已完成" if task["verification_passed"] else "执行结束，仍需核验"
            else:
                detail = f"{task['state']} · {task['error_code'] or '未取得可验收结果'}"
            if task.get("reused_from_version") is not None:
                version = task["reused_from_version"]
                detail += (
                    f" · 沿用第 {version} 版已验收结果"
                    if task["verification_passed"]
                    else f" · 第 {version} 版沿用证据需要复核"
                )
            lines.append(f"- {task['key']} · {detail}")
        artifacts = cast(list[Record], evidence["artifacts"])
        if artifacts:
            lines.extend(("", "已登记产物"))
            lines.extend(f"- {item['name']} v{item['version']} · {item['artifact_id']}" for item in artifacts)
        retained = cast(list[Record], evidence.get("retained_artifacts", []))
        if retained:
            lines.extend(("", "沿用证据（修订前版本产物）"))
            lines.extend(f"- {item['name']} v{item['version']} · {item['artifact_id']}" for item in retained)
        for item in cast(list[Record], evidence.get("unavailable_artifacts", [])):
            lines.append(f"- {item['name']} v{item['version']} · 无法核对存储内容")
        return "\n".join(lines)
