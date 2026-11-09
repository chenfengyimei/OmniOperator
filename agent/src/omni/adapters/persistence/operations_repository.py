"""Operational aggregates and owner credential audit; business bodies and secret values stay excluded."""

from __future__ import annotations

import re

from sqlalchemy import Table, func, select

from ...domain.contracts import BusinessError
from . import schema as s
from .credential_predicates import unreviewed_credentials
from .credential_repository import CredentialRepositories
from .delivery_predicates import unresolved_outbound


class OperationsRepositories(CredentialRepositories):
    def diagnostic_agent(self, agent_id: str) -> None:
        if self.connection.execute(select(s.agents.c.id).where(s.agents.c.id == agent_id)).first() is None:
            raise BusinessError("not_found", "Agent space does not exist", status=404)

    def diagnostic_audit(self, agent_id: str, operation_id: str, kind: str, data: dict) -> None:
        self.diagnostic_agent(agent_id)
        self.audit(agent_id, kind, operation_id, {**data, "boot_id": self.boot_id})

    def operations_status(self) -> dict:
        def states(table: Table) -> dict[str, int]:
            return {
                str(state): count
                for state, count in self.connection.execute(select(table.c.state, func.count()).group_by(table.c.state))
            }

        money = [
            {
                "currency": currency if re.fullmatch(r"[A-Z]{3}", currency) else None,
                "currency_valid": bool(re.fullmatch(r"[A-Z]{3}", currency)),
                "reserved_micro": held,
                "known_spent_micro": spent,
            }
            for currency, held, spent in self.connection.execute(
                select(
                    s.budget_accounts.c.currency,
                    func.sum(s.budget_accounts.c.held_micro),
                    func.sum(s.budget_accounts.c.spent_micro),
                )
                .group_by(s.budget_accounts.c.currency)
                .order_by(s.budget_accounts.c.currency)
                .limit(17)
            )
        ]
        return {
            "snapshot_at": self.clock.now(),
            "database": {
                "schema_version": self.connection.exec_driver_sql(
                    "SELECT version_num FROM alembic_version"
                ).scalar_one(),
                "sqlite_version": self.connection.execute(select(func.sqlite_version())).scalar_one(),
                "declared_tables": {name: list(table.c.keys()) for name, table in s.metadata.tables.items()},
            },
            "scope": "instance totals; no Agent identities",
            "runs": states(s.runs),
            "work_items": states(s.work_items),
            "model_calls": states(s.model_calls),
            "actions": states(s.actions),
            "deliveries": states(s.outbox),
            "document_cleanup": states(s.document_erasure_jobs),
            "execution_retention": states(s.retention_batches),
            "reference_release": states(s.reference_release_batches),
            "audit_retention": states(s.audit_retention_batches),
            "event_retention": states(s.event_retention_batches),
            "maintenance": states(s.maintenance_jobs),
            "memory_organization": states(s.memory_organization_batches),
            "extensions": states(s.extensions),
            "terminal_sessions": states(s.terminal_sessions),
            "terminal_operations": states(s.terminal_operations),
            "unreviewed_deliveries": self.connection.execute(
                select(func.count()).select_from(s.outbox).where(unresolved_outbound())
            ).scalar_one(),
            "held_dispatch_resources": self.connection.execute(
                select(func.count()).select_from(s.dispatch_resources).where(s.dispatch_resources.c.state == "held")
            ).scalar_one(),
            "money": money[:16],
            "money_truncated": len(money) > 16,
            "money_limit": 16,
            "unknown_cost_is_zero": False,
            "unresolved_credential_changes": self.connection.execute(
                select(func.count()).select_from(s.credential_references).where(unreviewed_credentials())
            ).scalar_one(),
            "credential_authority": states(s.credential_references),
            "credential_operations": states(s.credential_operations),
            "configurations": [
                {"kind": kind, "enabled": enabled, "count": count}
                for kind, enabled, count in self.connection.execute(
                    select(s.configurations.c.kind, s.configurations.c.enabled, func.count())
                    .where(s.configurations.c.deleted_at.is_(None))
                    .group_by(s.configurations.c.kind, s.configurations.c.enabled)
                )
            ],
        }
