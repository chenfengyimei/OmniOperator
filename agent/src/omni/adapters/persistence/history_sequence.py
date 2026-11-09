"""Allocate global history positions in the caller's transaction; never opens connections."""

from __future__ import annotations

import sqlite3

from sqlalchemy import Connection

from ...domain.contracts import BusinessError

ALLOCATE = (
    "UPDATE history_sequences SET last_issued=last_issued+1 "
    "WHERE kind=? AND last_issued<9223372036854775807 RETURNING last_issued"
)


def allocate_history(connection: Connection | sqlite3.Connection, kind: str) -> int:
    if kind not in {"events", "audit"}:
        raise BusinessError("invalid_history_stream", "Unknown allocated history stream", status=422)
    row = (
        connection.execute(ALLOCATE, (kind,))
        if isinstance(connection, sqlite3.Connection)
        else connection.exec_driver_sql(ALLOCATE, (kind,))
    ).fetchone()
    if row is None:
        raise BusinessError("history_sequence_unavailable", "History sequence is unavailable or exhausted", status=503)
    return int(row[0])
