"""Shared provenance and maintenance protection for aged pruned history; never reads execution bodies."""

from __future__ import annotations

from sqlalchemy import select

from ...application.ports import Record
from . import schema as s
from .execution_retention_repository import ExecutionRetentionRepositories


class HistoryProtectionRepositories(ExecutionRetentionRepositories):
    def _history_run_protections(self, row: Record, cutoff: str, *, stream: str = "", batch_id: str = "") -> list[str]:
        reasons = [
            "within_run_retention" if reason == "within_body_retention" else reason
            for reason in self._retention_protections(
                row, cutoff, event_batch_id=batch_id if stream == "events" else ""
            )
            if reason != "body_already_pruned"
        ]
        if not row["body_pruned_at"]:
            reasons.append("body_not_pruned")
        completed = self.connection.execute(
            select(s.retention_targets.c.run_id)
            .join(s.retention_batches, s.retention_batches.c.id == s.retention_targets.c.batch_id)
            .where(
                s.retention_targets.c.batch_id == row["body_pruned_plan_id"],
                s.retention_targets.c.run_id == row["id"],
                s.retention_targets.c.agent_id == row["agent_id"],
                s.retention_targets.c.state == "pruned",
                s.retention_targets.c.fingerprint == row["body_sha256"],
                s.retention_targets.c.pruned_at == row["body_pruned_at"],
                s.retention_batches.c.state == "complete",
            )
        ).first()
        if not completed:
            reasons.append("pruning_batch_incomplete")
        held = self.connection.execute(
            select(s.reference_release_targets.c.reference_id)
            .join(
                s.reference_release_batches,
                s.reference_release_batches.c.id == s.reference_release_targets.c.batch_id,
            )
            .where(
                s.reference_release_targets.c.agent_id == row["agent_id"],
                s.reference_release_targets.c.run_id == row["id"],
                s.reference_release_targets.c.state == "pending",
                s.reference_release_batches.c.state == "prepared",
            )
            .limit(1)
        ).first()
        if held:
            reasons.append("reference_release_held")
        audit_held = self.connection.execute(
            select(s.audit_retention_targets.c.audit_id)
            .join(s.audit_retention_batches, s.audit_retention_batches.c.id == s.audit_retention_targets.c.batch_id)
            .where(
                s.audit_retention_targets.c.agent_id == row["agent_id"],
                s.audit_retention_targets.c.run_id == row["id"],
                s.audit_retention_targets.c.state == "pending",
                s.audit_retention_batches.c.state == "prepared",
                s.audit_retention_batches.c.id != (batch_id if stream == "audit" else ""),
            )
            .limit(1)
        ).first()
        if audit_held:
            reasons.append("audit_retention_held")
        return reasons
