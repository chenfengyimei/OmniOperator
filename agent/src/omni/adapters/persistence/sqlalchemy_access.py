"""Shared SQLAlchemy SQLite transaction setup for the actor and exclusive offline migration."""

import sqlite3
from pathlib import Path

from sqlalchemy import Connection, Engine, create_engine, event
from sqlalchemy.pool import NullPool


def database_engine(path: Path) -> Engine:
    engine = create_engine(
        "sqlite+pysqlite:///" + str(path), poolclass=NullPool, connect_args={"check_same_thread": False}
    )

    @event.listens_for(engine, "connect")
    def setup(connection: sqlite3.Connection, _record: object) -> None:
        connection.isolation_level = None
        connection.execute("PRAGMA foreign_keys=ON")
        connection.execute("PRAGMA busy_timeout=5000")
        connection.execute("PRAGMA journal_mode=WAL")
        connection.execute("PRAGMA synchronous=FULL")

    @event.listens_for(engine, "begin")
    def begin(connection: Connection) -> None:
        connection.exec_driver_sql("BEGIN IMMEDIATE" if connection.info.get("omni_write") else "BEGIN")

    return engine
