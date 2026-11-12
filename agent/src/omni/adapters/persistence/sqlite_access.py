"""Owned SQLite connections for offline inspection, backup and restore only."""

from __future__ import annotations

import sqlite3
from collections.abc import Generator
from contextlib import contextmanager
from pathlib import Path

from ...domain.contracts import BusinessError


@contextmanager
def sqlite_connection(path: Path, *, read_only: bool = True) -> Generator[sqlite3.Connection, None, None]:
    if path.is_symlink() or path.is_junction() or (path.exists() and (not path.is_file() or path.stat().st_nlink != 1)):
        raise BusinessError("unsafe_database", "SQLite files must be regular files without links")
    connection = sqlite3.connect(
        path.resolve().as_uri() + "?mode=ro" if read_only else str(path), uri=read_only, isolation_level=None, timeout=5
    )
    try:
        connection.execute("PRAGMA foreign_keys=ON")
        connection.execute("PRAGMA busy_timeout=5000")
        if read_only:
            connection.execute("PRAGMA query_only=ON")
        else:
            connection.execute("PRAGMA synchronous=FULL")
        yield connection
    finally:
        if connection.in_transaction:
            connection.rollback()
        # Connection.__exit__ does not close or checkpoint the last WAL.
        connection.close()
