"""Offline read-only operational snapshot; caller holds exclusive instance ownership."""

from __future__ import annotations

from collections.abc import Callable
from pathlib import Path
from urllib.parse import quote

from sqlalchemy import create_engine
from sqlalchemy.engine import URL
from sqlalchemy.exc import SQLAlchemyError
from sqlalchemy.pool import NullPool

from ...application.ports import Clock, Ids, Record, UnitOfWork
from ...domain.contracts import BusinessError
from .maintenance import inspect_database
from .migration_config import head
from .repository import Repositories


def offline_status(path: Path, clock: Clock, ids: Ids) -> Record:
    return offline_read(path, clock, ids, lambda u: u.operations_status())


def offline_read(path: Path, clock: Clock, ids: Ids, query: Callable[[UnitOfWork], Record]) -> Record:
    report = inspect_database(path)
    if report["schema"] != head():
        raise BusinessError(
            "diagnostic_schema_mismatch",
            "Offline diagnostics requires the installed schema; no automatic upgrade was attempted",
            status=409,
        )
    uri = "file:" + quote(path.resolve().as_posix(), safe="/:")
    engine = create_engine(
        URL.create("sqlite+pysqlite", database=uri, query={"mode": "ro", "uri": "true"}), poolclass=NullPool
    )
    try:
        with engine.connect() as connection:
            connection.exec_driver_sql("PRAGMA foreign_keys=ON")
            connection.exec_driver_sql("PRAGMA busy_timeout=5000")
            connection.exec_driver_sql("PRAGMA query_only=ON")
            connection.commit()
            connection.exec_driver_sql("BEGIN")
            try:
                result = dict(query(Repositories(connection, clock, ids)))
                result["verification"] = {
                    "integrity_ok": report["integrity"] == "ok",
                    "foreign_key_errors": report["foreign_key_errors"],
                }
                return result
            finally:
                connection.rollback()
    except SQLAlchemyError as exc:
        raise BusinessError(
            "diagnostic_database_unavailable", "Offline database metadata could not be read", status=503
        ) from exc
    finally:
        engine.dispose()
