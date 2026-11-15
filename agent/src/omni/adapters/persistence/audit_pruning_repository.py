"""Persist reviewed audit deletion and its proof atomically; preserve identities and business ledgers."""

from __future__ import annotations

from typing import Any, cast

from sqlalchemy import select

from ...domain.audit_retention import PLAN_FORMAT, POLICY, validate_audit_plan
from ...domain.contracts import BusinessError
from ...domain.retention import body_fingerprint
from . import schema as s
from .audit_retention_repository import AuditRetentionRepositories
from .history_pruning_cycle import PruningCycle, PruningSpec

AUDIT_SPEC = PruningSpec(
    "audit",
    "audit_retention",
    s.audit,
    s.audit_retention_batches,
    s.audit_retention_targets,
    "id",
    "audit_id",
    (("id", "audit_id"), ("sequence", "sequence"), ("kind", "kind"), ("operation_id", "operation_id")),
    "audit_rows_restored",
    validate_audit_plan,
    s.event_retention_batches,
)


class AuditPruningRepositories(AuditRetentionRepositories):
    def _audit_target(self, agent_id: str, audit_id: str, cutoff: str, batch_id: str = "") -> dict[str, Any]:
        row = (
            self.connection.execute(select(s.audit).where(s.audit.c.agent_id == agent_id, s.audit.c.id == audit_id))
            .mappings()
            .first()
        )
        if not row or row["created_at"] > cutoff:
            raise BusinessError("audit_retention_conflict", "The original aged audit is missing or changed")
        audit = dict(row)
        metadata = {key: value for key, value in audit.items() if key != "data"}
        item = self._audit_retention_candidates(agent_id, [metadata], cutoff, batch_id)[0]
        if not item["eligible"]:
            raise BusinessError(
                "audit_retention_conflict", "Audit provenance or business protection requires retention"
            )
        run = self._one(s.runs, agent_id, cast(str, item["run_id"]))
        return {
            **{
                key: item[key]
                for key in ("audit_id", "sequence", "agent_id", "kind", "operation_id", "subject_kind", "run_id")
            },
            "run_retention_revision": run["retention_revision"],
            "prune_plan_id": run["body_pruned_plan_id"],
            "body_sha256": run["body_sha256"],
            "fingerprint": body_fingerprint([audit])[0],
        }

    def audit_retention_plan(
        self, agent_id: str, *, cursor: str = "", limit: int = 20, as_of: str = ""
    ) -> dict[str, Any]:
        preview = self.audit_retention_preview(agent_id, cursor=cursor, limit=limit, as_of=as_of)
        targets = [
            self._audit_target(agent_id, item["audit_id"], preview["cutoff"])
            for item in preview["items"]
            if item["eligible"]
        ]
        if not targets:
            raise BusinessError(
                "no_audit_retention_candidates", "No eligible audits in this page; continue the preview"
            )
        return {
            "format": PLAN_FORMAT,
            "policy": POLICY,
            "id": self.ids.new(),
            **{key: preview[key] for key in ("instance_id", "agent_id", "schema_version", "as_of", "cutoff")},
            "created_at": self.clock.now(),
            "epoch": self.connection.execute(select(s.history_identity.c.epoch)).scalar_one(),
            "trim_revision": preview["stream"]["trim_revision"],
            "high_water": preview["stream"]["high_water"],
            "targets": targets,
        }

    def _audit_cycle(self) -> PruningCycle:
        return PruningCycle(self, AUDIT_SPEC, self._audit_target)

    def audit_retention_batch(self, identity: str) -> dict[str, Any] | None:
        return self._audit_cycle().batch(identity)

    def audit_retention_validate(self, plan: dict[str, Any]) -> None:
        self._audit_cycle().validate(plan)

    def audit_retention_prepare(self, plan: dict[str, Any], backup_path: str, backup_sha256: str) -> dict[str, Any]:
        return self._audit_cycle().prepare(plan, backup_path, backup_sha256)

    def audit_retention_delete(self, plan: dict[str, Any], target: dict[str, Any]) -> None:
        self._audit_cycle().prune(plan, target)

    def audit_retention_complete(self, identity: str) -> dict[str, Any]:
        return self._audit_cycle().complete(identity)

    def audit_retention_cancel(self, plan: dict[str, Any], reason: str) -> dict[str, Any]:
        return self._audit_cycle().cancel(plan, reason)
