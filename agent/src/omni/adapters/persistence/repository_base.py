"""Common transaction-scoped identity, ownership and audit primitives."""

from __future__ import annotations

from collections.abc import Mapping
from typing import Any, cast

from sqlalchemy import Connection, Table, insert, select, update

from ...application.ports import Clock, Ids, Record
from ...domain.contracts import BusinessError
from . import schema as s
from .history_sequence import allocate_history


class RepositoryBase:
    def __init__(self, connection: Connection, clock: Clock, ids: Ids, boot_id: str = "") -> None:
        self.connection, self.clock, self.ids = connection, clock, ids
        self.boot_id = boot_id

    def _record(self, table: Table, values: Mapping[str, Any], agent_id: str | None = None) -> Record:
        row = {"id": self.ids.new(), "created_at": self.clock.now(), **values}
        if agent_id is not None:
            row["agent_id"] = agent_id
        if table is s.audit:
            row["sequence"] = allocate_history(self.connection, "audit")
        if table in (s.work_items, s.outbox):
            row["queue_sequence"] = self.connection.execute(
                update(s.instances)
                .where(s.instances.c.slot == 1)
                .values(next_work_sequence=s.instances.c.next_work_sequence + 1)
                .returning(s.instances.c.next_work_sequence)
            ).scalar_one()
        self.connection.execute(insert(table).values(**row))
        return row

    def _one(self, table: Table, agent_id: str, entity_id: str) -> Record:
        row = (
            self.connection.execute(select(table).where(table.c.id == entity_id, table.c.agent_id == agent_id))
            .mappings()
            .first()
        )
        if row is None:
            raise BusinessError("not_found", "Resource does not exist in this Agent space", status=404)
        return cast(Record, dict(row))

    def audit(self, agent_id: str, kind: str, operation_id: str | None, data: Record) -> str:
        return str(self._record(s.audit, {"kind": kind, "operation_id": operation_id, "data": data}, agent_id)["id"])
