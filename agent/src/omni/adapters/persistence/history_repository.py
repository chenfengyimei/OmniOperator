"""Read history pages and retention revisions in one transaction; does not delete history."""

from __future__ import annotations

from sqlalchemy import select

from ...domain.contracts import BusinessError
from ...domain.history import decode_cursor, encode_cursor
from . import schema as s
from .repository_base import RepositoryBase


class HistoryRepositories(RepositoryBase):
    def history_page(
        self, kind: str, agent_id: str = "", *, cursor: str = "", limit: int = 100, run_id: str = ""
    ) -> dict:
        if type(limit) is not int or not 1 <= limit <= 200 or not isinstance(cursor, str):
            raise BusinessError("invalid_history_query", "History page bounds are invalid", status=422)
        if kind not in {"events", "audit", "instance_audit"} or (run_id and kind != "events"):
            raise BusinessError("invalid_history_query", "Unknown history stream or filter", status=422)
        instance = self.connection.execute(select(s.instances.c.id)).scalar_one()
        scope_id = instance
        if kind != "instance_audit":
            if not self.connection.execute(select(s.agents.c.id).where(s.agents.c.id == agent_id)).first():
                raise BusinessError("not_found", "Agent does not exist", status=404)
            scope_id = agent_id
            if run_id:
                self._one(s.runs, agent_id, run_id)
        elif agent_id:
            raise BusinessError("invalid_history_query", "Instance history has no Agent filter", status=422)
        epoch = self.connection.execute(select(s.history_identity.c.epoch)).scalar_one()
        state = dict(
            self.connection.execute(
                select(s.history_scopes).where(s.history_scopes.c.kind == kind, s.history_scopes.c.scope_id == scope_id)
            )
            .mappings()
            .one()
        )
        table = {"events": s.run_events, "audit": s.audit, "instance_audit": s.instance_audit}[kind]
        if kind == "instance_audit":
            # sqlite_sequence persists across deleting the last row; MAX does not.
            row = self.connection.exec_driver_sql("SELECT seq FROM sqlite_sequence WHERE name='instance_audit'").first()
            high_water = int(row[0]) if row else 0
        else:
            high_water = self.connection.execute(
                select(s.history_sequences.c.last_issued).where(s.history_sequences.c.kind == kind)
            ).scalar_one()
        filters = {"run_id": run_id} if run_id else {}
        position = (
            decode_cursor(
                cursor,
                epoch=epoch,
                kind=kind,
                scope=scope_id,
                revision=state["trim_revision"],
                high_water=high_water,
                filters=filters,
            )
            if cursor
            else 0
        )
        statement = select(table).where(table.c.sequence > position, table.c.sequence <= high_water)
        if kind != "instance_audit":
            statement = statement.where(table.c.agent_id == agent_id)
        if run_id:
            statement = statement.where(table.c.run_id == run_id)
        rows = [
            dict(row)
            for row in self.connection.execute(statement.order_by(table.c.sequence).limit(limit + 1)).mappings()
        ]
        items = rows[:limit]
        position = items[-1]["sequence"] if items else position
        return {
            "items": items,
            "next_cursor": encode_cursor(epoch, kind, scope_id, state["trim_revision"], position, filters=filters),
            "has_more": len(rows) > limit,
            "history_trimmed": state["deleted_count"] > 0,
            "stream": {
                "high_water": high_water,
                **{key: state[key] for key in ("trim_revision", "deleted_count", "last_trim_at")},
            },
        }
