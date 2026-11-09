"""Check an initialized service home without migrations or business recovery."""

from __future__ import annotations

import re
import sqlite3
from pathlib import Path

from ...domain.contracts import BusinessError
from .migration_config import head
from .sqlite_access import sqlite_connection


def verify_deployed_instance(database: Path, instance_id: str, *, schema_version: str | None = None) -> None:
    expected = head() if schema_version is None else schema_version
    if not isinstance(expected, str) or re.fullmatch(r"v2_[0-9]{4}", expected) is None:
        raise BusinessError("service_instance_changed", "Use the exact reviewed deployment schema")
    try:
        with sqlite_connection(database) as connection:
            instance = connection.execute("SELECT id FROM instances WHERE slot = 1").fetchall()
            revision = connection.execute("SELECT version_num FROM alembic_version").fetchall()
        if instance != [(instance_id,)] or revision != [(expected,)]:
            raise BusinessError(
                "service_instance_changed",
                "Initialize or explicitly upgrade the intended instance before service startup",
            )
    except (sqlite3.Error, OSError) as exc:
        raise BusinessError(
            "service_instance_unavailable", "Service startup requires an initialized V2 instance"
        ) from exc
