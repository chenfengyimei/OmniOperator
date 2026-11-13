"""Execution-body eligibility, owner pins and transactional prune progress; preserves conversations and ledgers."""

from __future__ import annotations

from itertools import chain
from typing import cast

from sqlalchemy import insert, or_, select, update

from ...application.ports import Record
from ...domain.contracts import BusinessError
from ...domain.retention import (
    PLAN_FORMAT,
    RETENTION_POLICY,
    TERMINAL_RUNS,
    body_fingerprint,
    retention_cutoff,
    retention_page,
    validate_retention_plan,
)
from . import schema as s
from .action_predicates import unresolved_action
from .delivery_predicates import held_outbound, pending_outbound
from .repository_base import RepositoryBase


class ExecutionRetentionRepositories(RepositoryBase):
    def retention_pin(self, agent_id: str, run_id: str, pinned: bool, revision: int) -> Record:
        row = self._one(s.runs, agent_id, run_id)
        if row["body_pruned_at"]:
            raise BusinessError(
                "body_already_pruned", "Restore the verified snapshot to a new home to recover this body"
            )
        if type(pinned) is not bool or type(revision) is not int or revision < 1:
            raise BusinessError("invalid_retention_pin", "Pin requires a boolean and positive revision", status=422)
        if row["retention_revision"] != revision:
            raise BusinessError("revision_conflict", "Run retention preference changed; reload before editing")
        self.connection.execute(
            update(s.runs)
            .where(s.runs.c.id == run_id)
            .values(
                retention_pinned=pinned,
                retention_revision=s.runs.c.retention_revision + 1,
            )
        )
        self.audit(agent_id, "run.retention_pin", run_id, {"pinned": pinned, "revision": revision + 1})
        return self._one(s.runs, agent_id, run_id)

    def _retention_protections(
        self, row: Record, cutoff: str, batch_id: str = "", *, event_batch_id: str = ""
    ) -> list[str]:
        """Shared business protections without loading or hashing execution bodies."""
        agent_id, run_id = cast(str, row["agent_id"]), cast(str, row["id"])
        reasons: list[str] = []
        if row["state"] not in TERMINAL_RUNS:
            reasons.append("run_not_terminal")
        if not row["ended_at"] or cast(str, row["ended_at"]) > cutoff:
            reasons.append("within_body_retention")
        if row["retention_pinned"]:
            reasons.append("run_pinned")
        if row["body_pruned_at"]:
            reasons.append("body_already_pruned")
        if row["error_code"] == "legacy_configuration_unresolved":
            reasons.append("configuration_unresolved")
        actions = select(s.actions.c.id).where(s.actions.c.run_id == run_id, s.actions.c.agent_id == agent_id)
        checks = {
            "event_retention_held": select(s.event_retention_targets.c.sequence)
            .join(s.event_retention_batches, s.event_retention_batches.c.id == s.event_retention_targets.c.batch_id)
            .where(
                s.event_retention_targets.c.agent_id == agent_id,
                s.event_retention_targets.c.run_id == run_id,
                s.event_retention_targets.c.batch_id != event_batch_id,
                s.event_retention_targets.c.state == "pending",
                s.event_retention_batches.c.state == "prepared",
            ),
            "retention_batch_held": select(s.retention_targets.c.run_id)
            .join(s.retention_batches, s.retention_batches.c.id == s.retention_targets.c.batch_id)
            .where(
                s.retention_targets.c.run_id == run_id,
                s.retention_targets.c.batch_id != batch_id,
                s.retention_targets.c.state == "pending",
                s.retention_batches.c.state == "prepared",
            ),
            "approval_pending": select(s.approvals.c.id).where(
                s.approvals.c.action_id.in_(actions), s.approvals.c.state == "pending"
            ),
            "action_unresolved": select(s.actions.c.id).where(
                s.actions.c.run_id == run_id,
                unresolved_action(),
            ),
            "model_cost_unresolved": select(s.model_calls.c.id).where(
                s.model_calls.c.run_id == run_id,
                or_(
                    s.model_calls.c.state.in_(["reserved", "unknown"]),
                    (s.model_calls.c.cost_micro.is_(None)) & (s.model_calls.c.state != "not_sent"),
                ),
            ),
            "work_unresolved": select(s.work_items.c.id).where(
                s.work_items.c.run_id == run_id,
                s.work_items.c.state.in_(["pending", "claimed", "running", "unknown"]),
            ),
            "run_resource_held": select(s.leases.c.run_id).where(
                s.leases.c.run_id == run_id, s.leases.c.state == "held"
            ),
            "outbound_unresolved": select(s.outbox.c.id).where(
                s.outbox.c.run_id == run_id,
                or_(pending_outbound(), s.outbox.c.state == "unknown", held_outbound()),
            ),
            "memory_source": select(s.source_anchors.c.id).where(s.source_anchors.c.action_id.in_(actions)),
            "document_context_source": select(s.context_documents.c.id).where(
                s.context_documents.c.action_id.in_(actions)
            ),
            "artifact_pinned": select(s.objects.c.id)
            .join(s.action_objects, s.action_objects.c.object_id == s.objects.c.id)
            .where(
                s.action_objects.c.action_id.in_(actions),
                s.objects.c.pinned.is_(True),
            ),
        }
        for reason, query in checks.items():
            if self.connection.execute(query.limit(1)).first() is not None:
                reasons.append(reason)
        return reasons

    def retention_inspect(self, agent_id: str, run_id: str, *, cutoff: str | None = None, batch_id: str = "") -> Record:
        row = self._one(s.runs, agent_id, run_id)
        cutoff = cutoff or retention_cutoff(self.clock.now())
        reasons = self._retention_protections(row, cutoff, batch_id)
        actions = select(s.actions.c.id).where(s.actions.c.run_id == run_id, s.actions.c.agent_id == agent_id)
        action_rows = self.connection.execute(
            select(s.actions).where(s.actions.c.id.in_(actions)).order_by(s.actions.c.id)
        ).mappings()
        event_rows = self.connection.execute(
            select(s.run_events).where(s.run_events.c.run_id == run_id).order_by(s.run_events.c.seq)
        ).mappings()
        fingerprint, records, size = body_fingerprint(
            chain(
                ({"kind": "run", **row},),
                ({"kind": "action", **dict(item)} for item in action_rows),
                ({"kind": "event", **dict(item)} for item in event_rows),
            )
        )
        return cast(
            Record,
            {
                "run_id": run_id,
                "agent_id": agent_id,
                "state": row["state"],
                "ended_at": row["ended_at"],
                "retention_pinned": row["retention_pinned"],
                "retention_revision": row["retention_revision"],
                "body_pruned_at": row["body_pruned_at"],
                "body_pruned_plan_id": row["body_pruned_plan_id"],
                "policy": RETENTION_POLICY,
                "cutoff": cutoff,
                "eligible": not reasons,
                "protected_reasons": reasons,
                "fingerprint": fingerprint,
                "fingerprinted_records": records,
                "fingerprinted_bytes": size,
                "can_apply": False,
            },
        )

    def retention_plan(self, after: str = "", limit: int = 20) -> Record:
        preview = self.retention_preview(after, limit)
        targets = [
            {key: row[key] for key in ("agent_id", "run_id", "fingerprint", "retention_revision")}
            for row in cast(list[Record], preview["items"])
            if row["eligible"]
        ]
        if not targets:
            raise BusinessError(
                "no_retention_candidates", "No eligible bodies in this page; inspect the preview cursor"
            )
        return cast(
            Record,
            {
                "format": PLAN_FORMAT,
                "id": self.ids.new(),
                **{key: preview[key] for key in ("instance_id", "schema_version", "policy", "created_at", "cutoff")},
                "targets": targets,
            },
        )

    def retention_batch(self, identity: str) -> Record | None:
        row = (
            self.connection.execute(select(s.retention_batches).where(s.retention_batches.c.id == identity))
            .mappings()
            .first()
        )
        return cast(Record, dict(row)) if row else None

    def _retention_target(self, plan: Record, target: Record) -> bool:
        run = self._one(s.runs, cast(str, target["agent_id"]), cast(str, target["run_id"]))
        if run["body_pruned_at"]:
            if run["body_pruned_plan_id"] != plan["id"] or run["body_sha256"] != target["fingerprint"]:
                raise BusinessError("retention_conflict", "Run was pruned by another plan")
            return False
        current = self.retention_inspect(
            cast(str, target["agent_id"]),
            cast(str, target["run_id"]),
            cutoff=cast(str, plan["cutoff"]),
            batch_id=cast(str, plan["id"]),
        )
        if not current["eligible"] or any(current[key] != target[key] for key in ("fingerprint", "retention_revision")):
            raise BusinessError(
                "retention_conflict", "Body, retention preference or protection changed; create a new plan"
            )
        return True

    def retention_validate(self, plan: Record) -> None:
        validate_retention_plan(cast(dict[str, object], plan), self.clock.now())
        instance = self.connection.execute(select(s.instances.c.id).where(s.instances.c.slot == 1)).scalar_one()
        version = self.connection.exec_driver_sql("SELECT version_num FROM alembic_version").scalar_one()
        if plan["instance_id"] != instance or plan["schema_version"] != version:
            raise BusinessError("retention_identity_mismatch", "Plan belongs to another instance or schema")
        batch = self.retention_batch(cast(str, plan["id"]))
        if batch and batch["plan_sha256"] != body_fingerprint([plan])[0]:
            raise BusinessError("retention_conflict", "Persisted plan identity cannot be changed")
        if batch and batch["state"] == "cancelled":
            raise BusinessError("retention_cancelled", "This plan was cancelled; preview and create a new plan")
        for target in cast(list[Record], plan["targets"]):
            if not self._retention_target(plan, target) and not batch:
                raise BusinessError("retention_conflict", "Prune marker has no matching batch")

    def retention_prepare(self, plan: Record, backup_path: str, backup_sha256: str) -> Record:
        self.retention_validate(plan)
        batch = self.retention_batch(cast(str, plan["id"]))
        if batch:
            if batch["backup_path"] != backup_path or batch["backup_sha256"] != backup_sha256:
                raise BusinessError("retention_backup_changed", "Resume requires the original verified backup")
            return batch
        batch = self._record(
            s.retention_batches,
            {
                "id": plan["id"],
                "instance_id": plan["instance_id"],
                "plan_sha256": body_fingerprint([plan])[0],
                "policy": plan["policy"],
                "cutoff": plan["cutoff"],
                "schema_version": plan["schema_version"],
                "backup_path": backup_path,
                "backup_sha256": backup_sha256,
                "state": "prepared",
            },
        )
        for target in cast(list[Record], plan["targets"]):
            self.connection.execute(insert(s.retention_targets).values(batch_id=plan["id"], state="pending", **target))
            self.audit(
                cast(str, target["agent_id"]),
                "retention.prepared",
                cast(str, plan["id"]),
                {
                    "run_id": target["run_id"],
                    "fingerprint": target["fingerprint"],
                    "backup_sha256": backup_sha256,
                },
            )
        return batch

    def retention_prune(self, plan: Record, target: Record) -> None:
        batch = self.retention_batch(cast(str, plan["id"]))
        if (
            not batch
            or batch["plan_sha256"] != validate_retention_plan(cast(dict[str, object], plan), self.clock.now())
            or target not in cast(list[Record], plan["targets"])
        ):
            raise BusinessError("retention_conflict", "A matching prepared batch is required")
        item = (
            self.connection.execute(
                select(s.retention_targets).where(
                    s.retention_targets.c.batch_id == plan["id"],
                    s.retention_targets.c.run_id == target["run_id"],
                )
            )
            .mappings()
            .one()
        )
        needed = self._retention_target(plan, target)
        if not needed and item["state"] == "pruned":
            return
        if not needed or item["state"] != "pending" or batch["state"] != "prepared":
            raise BusinessError("retention_conflict", "Prune progress does not match body state")
        now = self.clock.now()
        for action in self.connection.execute(
            select(s.actions).where(s.actions.c.run_id == target["run_id"])
        ).mappings():
            sha = body_fingerprint([{"result": action["result"]}])[0]
            result = cast(Record, action["result"]) if isinstance(action["result"], dict) else {}
            self.connection.execute(
                update(s.actions)
                .where(s.actions.c.id == action["id"])
                .values(
                    arguments={},
                    result={
                        "body_pruned": True,
                        "sha256": sha,
                        "status": action["state"],
                        "effect": action["effect"],
                        "error_code": result.get("error_code", ""),
                    },
                    result_sha256=sha,
                    body_pruned_at=now,
                )
            )
        for event in self.connection.execute(
            select(s.run_events).where(s.run_events.c.run_id == target["run_id"])
        ).mappings():
            sha = body_fingerprint([{"data": event["data"]}])[0]
            payload = cast(Record, event["data"]) if isinstance(event["data"], dict) else {}
            identifiers = {
                key: payload[key]
                for key in (
                    "action_id",
                    "call_id",
                    "message_id",
                    "operation_id",
                    "summary_id",
                    "outbound_id",
                    "error_code",
                    "status",
                    "state",
                    "effect",
                )
                if key in payload
            }
            self.connection.execute(
                update(s.run_events)
                .where(s.run_events.c.sequence == event["sequence"])
                .values(
                    data={**identifiers, "body_pruned": True, "plan_id": plan["id"], "sha256": sha},
                    data_sha256=sha,
                    body_pruned_at=now,
                )
            )
        self.connection.execute(
            update(s.runs)
            .where(s.runs.c.id == target["run_id"])
            .values(
                body_pruned_at=now,
                body_pruned_plan_id=plan["id"],
                body_sha256=target["fingerprint"],
            )
        )
        self.connection.execute(
            update(s.retention_targets)
            .where(
                s.retention_targets.c.batch_id == plan["id"],
                s.retention_targets.c.run_id == target["run_id"],
            )
            .values(state="pruned", pruned_at=now)
        )
        self.audit(
            cast(str, target["agent_id"]),
            "retention.pruned",
            cast(str, plan["id"]),
            {
                "run_id": target["run_id"],
                "fingerprint": target["fingerprint"],
                "backup_sha256": batch["backup_sha256"],
            },
        )

    def retention_complete(self, identity: str) -> Record:
        batch = self.retention_batch(identity)
        if not batch:
            raise BusinessError("not_found", "Retention batch does not exist", status=404)
        if batch["state"] == "cancelled":
            raise BusinessError("retention_cancelled", "Cancelled plans cannot complete")
        if self.connection.execute(
            select(s.retention_targets.c.run_id).where(
                s.retention_targets.c.batch_id == identity,
                s.retention_targets.c.state != "pruned",
            )
        ).first():
            raise BusinessError("retention_incomplete", "Prune targets remain pending")
        if batch["state"] != "complete":
            self.connection.execute(
                update(s.retention_batches)
                .where(s.retention_batches.c.id == identity)
                .values(
                    state="complete",
                    completed_at=self.clock.now(),
                )
            )
        return {"plan_id": identity, "complete": True, "backup_sha256": batch["backup_sha256"]}

    def retention_cancel(self, plan: Record, reason: str) -> Record:
        sha = validate_retention_plan(cast(dict[str, object], plan), self.clock.now(), allow_future=True)
        batch = self.retention_batch(cast(str, plan["id"]))
        if not isinstance(cast(object, reason), str) or not 1 <= len(reason.strip()) <= 500:
            raise BusinessError(
                "invalid_retention_reason", "A cancellation reason of 1 to 500 characters is required", status=422
            )
        if not batch or batch["plan_sha256"] != sha:
            raise BusinessError("retention_conflict", "Cancellation requires the original persisted plan")
        if batch["state"] == "complete":
            raise BusinessError("retention_complete", "Completed plans cannot be cancelled")
        if batch["state"] != "cancelled":
            self.connection.execute(
                update(s.retention_targets)
                .where(
                    s.retention_targets.c.batch_id == plan["id"],
                    s.retention_targets.c.state == "pending",
                )
                .values(state="cancelled")
            )
            self.connection.execute(
                update(s.retention_batches)
                .where(s.retention_batches.c.id == plan["id"])
                .values(
                    state="cancelled",
                    completed_at=self.clock.now(),
                )
            )
            for target in cast(list[Record], plan["targets"]):
                self.audit(
                    cast(str, target["agent_id"]),
                    "retention.cancelled",
                    cast(str, plan["id"]),
                    {
                        "run_id": target["run_id"],
                        "reason": reason.strip(),
                        "already_pruned_bodies_restored": False,
                    },
                )
        return {"plan_id": plan["id"], "cancelled": True, "bodies_restored": False}

    def retention_preview(self, after: str = "", limit: int = 20) -> Record:
        retention_page(after, limit)
        now = self.clock.now()
        cutoff = retention_cutoff(now)
        rows = list(
            self.connection.execute(
                select(s.runs.c.agent_id, s.runs.c.id)
                .where(s.runs.c.id > after, s.runs.c.ended_at <= cutoff)
                .order_by(s.runs.c.id)
                .limit(limit + 1)
            )
        )
        items = [
            self.retention_inspect(cast(str, agent), cast(str, identity), cutoff=cutoff)
            for agent, identity in rows[:limit]
        ]
        return cast(
            Record,
            {
                "format": "omni-execution-retention-preview-1",
                "policy": RETENTION_POLICY,
                "instance_id": self.connection.execute(
                    select(s.instances.c.id).where(s.instances.c.slot == 1)
                ).scalar_one(),
                "schema_version": self.connection.exec_driver_sql(
                    "SELECT version_num FROM alembic_version"
                ).scalar_one(),
                "created_at": now,
                "cutoff": cutoff,
                "items": items,
                "next_after": rows[limit - 1].id if len(rows) > limit else None,
                "scope": "Ended runs at least 30 days old; metadata only; preview cannot be applied",
                "can_apply": False,
            },
        )
