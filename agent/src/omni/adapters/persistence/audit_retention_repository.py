"""Bounded metadata-only audit retention preview; never deletes rows or creates executable plans."""

from __future__ import annotations

from typing import Any

from sqlalchemy import Table, select

from ...domain.audit_retention import (
    FORMAT,
    POLICY,
    SUBJECTS,
    decode_preview_cursor,
    encode_preview_cursor,
    preview_time,
)
from ...domain.contracts import BusinessError
from . import schema as s
from .history_protection import HistoryProtectionRepositories


class AuditRetentionRepositories(HistoryProtectionRepositories):
    def audit_retention_preview(
        self, agent_id: str, *, cursor: str = "", limit: int = 20, as_of: str = ""
    ) -> dict[str, Any]:
        if type(limit) is not int or not 1 <= limit <= 50 or not isinstance(cursor, str) or not isinstance(as_of, str):
            raise BusinessError(
                "invalid_audit_retention_query", "Use a cursor and a page of 1 to 50 audits", status=422
            )
        if not self.connection.execute(select(s.agents.c.id).where(s.agents.c.id == agent_id)).first():
            raise BusinessError("not_found", "Agent does not exist", status=404)
        # All cursor state, rows and business protections share this read transaction.
        now = self.clock.now()
        epoch = self.connection.execute(select(s.history_identity.c.epoch)).scalar_one()
        scope = dict(
            self.connection.execute(
                select(s.history_scopes).where(
                    s.history_scopes.c.kind == "audit", s.history_scopes.c.scope_id == agent_id
                )
            )
            .mappings()
            .one()
        )
        current_high_water = self.connection.execute(
            select(s.history_sequences.c.last_issued).where(s.history_sequences.c.kind == "audit")
        ).scalar_one()
        if cursor:
            position = decode_preview_cursor(
                cursor,
                epoch=epoch,
                agent_id=agent_id,
                revision=scope["trim_revision"],
                current_high_water=current_high_water,
                now=now,
                as_of=as_of,
            )
            as_of, cutoff = position["as_of"], position["cutoff"]
            high_water, after = position["high_water"], position["position"]
        else:
            as_of, cutoff = preview_time(as_of or now, now)
            high_water, after = current_high_water, 0
        # Do not even fetch audit.data; page limits bound work even if every row is protected.
        columns = (
            s.audit.c.id,
            s.audit.c.sequence,
            s.audit.c.agent_id,
            s.audit.c.kind,
            s.audit.c.operation_id,
            s.audit.c.created_at,
        )
        rows = [
            dict(row)
            for row in self.connection.execute(
                select(*columns)
                .where(
                    s.audit.c.agent_id == agent_id,
                    s.audit.c.sequence > after,
                    s.audit.c.sequence <= high_water,
                    s.audit.c.created_at <= cutoff,
                )
                .order_by(s.audit.c.sequence)
                .limit(limit + 1)
            ).mappings()
        ]
        page = rows[:limit]
        items = self._audit_retention_candidates(agent_id, page, cutoff)
        eligible = sum(item["eligible"] for item in items)
        return {
            "format": FORMAT,
            "policy": POLICY,
            "agent_id": agent_id,
            "instance_id": self.connection.execute(select(s.instances.c.id)).scalar_one(),
            "schema_version": self.connection.exec_driver_sql("SELECT version_num FROM alembic_version").scalar_one(),
            "as_of": as_of,
            "cutoff": cutoff,
            "items": items,
            "counts": {"scanned": len(items), "eligible": eligible, "protected": len(items) - eligible},
            "next_cursor": encode_preview_cursor(
                epoch,
                agent_id,
                scope["trim_revision"],
                as_of,
                high_water,
                items[-1]["sequence"] if items else after,
            ),
            "has_more": len(rows) > limit,
            "history_trimmed": scope["deleted_count"] > 0,
            "stream": {
                "high_water": high_water,
                **{key: scope[key] for key in ("trim_revision", "deleted_count", "last_trim_at")},
            },
            "can_apply": False,
            "scope": "Agent audit metadata only; eligibility is re-evaluated on each page; no deletion authorization",
        }

    def _audit_retention_candidates(
        self, agent_id: str, rows: list[dict[str, Any]], cutoff: str, batch_id: str = ""
    ) -> list[dict[str, Any]]:
        identities = [row["id"] for row in rows]
        memory_sources = set(
            self.connection.execute(
                select(s.source_anchors.c.manual_audit_id).where(
                    s.source_anchors.c.agent_id == agent_id,
                    s.source_anchors.c.manual_audit_id.in_(identities),
                )
            ).scalars()
        )
        authors = set(
            self.connection.execute(
                select(s.document_revisions.c.authored_audit_id).where(
                    s.document_revisions.c.agent_id == agent_id,
                    s.document_revisions.c.authored_audit_id.in_(identities),
                )
            ).scalars()
        )
        referenced = set()
        for table, column in s.AUDIT_REFERENCES:
            if table in (s.source_anchors, s.document_revisions):
                continue
            referenced.update(
                self.connection.execute(
                    select(column).where(table.c.agent_id == agent_id, column.in_(identities))
                ).scalars()
            )
        runs: dict[str, dict | None] = {}
        actions: dict[str, dict | None] = {}
        protections: dict[str, list[str]] = {}

        def owned(table: Table, identity: str | None, cache: dict[str, dict | None]) -> dict | None:
            if not identity:
                return None
            if identity not in cache:
                # Action arguments and results can be arbitrarily large; only identity/state are needed.
                statement = (
                    select(*(table.c[key] for key in ("id", "agent_id", "run_id", "state", "body_pruned_at")))
                    if table is s.actions
                    else select(table)
                )
                row = (
                    self.connection.execute(statement.where(table.c.id == identity, table.c.agent_id == agent_id))
                    .mappings()
                    .first()
                )
                cache[identity] = dict(row) if row else None
            return cache[identity]

        items = []
        for row in rows:
            subject = SUBJECTS.get(row["kind"])
            reasons: list[str] = []
            run = action = None
            if subject is None:
                reasons.append("kind_not_eligible")
            elif subject == "run":
                run = owned(s.runs, row["operation_id"], runs)
                if run and run["state"] != "succeeded":
                    reasons.append("subject_state_mismatch")
            else:
                action = owned(s.actions, row["operation_id"], actions)
                if action:
                    run = owned(s.runs, action["run_id"], runs)
                    if action["state"] != row["kind"].removeprefix("action."):
                        reasons.append("subject_state_mismatch")
            if subject is not None:
                if not run:
                    reasons.append("subject_missing")
                else:
                    if run["id"] not in protections:
                        protections[run["id"]] = self._history_run_protections(
                            run, cutoff, stream="audit", batch_id=batch_id
                        )
                    reasons.extend(protections[run["id"]])
                    if action and action["body_pruned_at"] != run["body_pruned_at"]:
                        reasons.append("body_not_pruned")
            if row["id"] in memory_sources:
                reasons.append("audit_memory_source")
            if row["id"] in authors:
                reasons.append("audit_document_author")
            if row["id"] in referenced:
                reasons.append("audit_reference")
            items.append(
                {
                    "audit_id": row["id"],
                    **{key: value for key, value in row.items() if key != "id"},
                    "subject_kind": subject,
                    "run_id": run["id"] if run else None,
                    "eligible": not reasons,
                    "protected_reasons": list(dict.fromkeys(reasons)),
                }
            )
        return items
