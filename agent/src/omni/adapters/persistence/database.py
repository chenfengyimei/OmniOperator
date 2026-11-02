"""Bounded, single-writer database actor with isolated read connections."""

from __future__ import annotations

import asyncio
import contextvars
import sqlite3
from collections.abc import Callable
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from typing import TypeVar

from alembic import command
from alembic.config import Config
from sqlalchemy import create_engine, event, inspect
from sqlalchemy.exc import IntegrityError, OperationalError
from sqlalchemy.pool import NullPool

from ...application.ports import Clock, Ids, UnitOfWork
from ...domain.contracts import BusinessError
from .repository import Repositories

T = TypeVar("T")


class Database:
    def __init__(self, path: Path, clock: Clock, ids: Ids, *, max_writes: int = 256, max_reads: int = 64) -> None:
        self.path, self.clock, self.ids = path, clock, ids
        self.max_writes, self.max_reads = max_writes, max_reads
        self._writes = self._reads = 0
        self._closed = False
        self._last_error = ""
        self._pending: set[asyncio.Future] = set()
        self._writer = ThreadPoolExecutor(max_workers=1, thread_name_prefix="omni-db-write")
        self._readers = ThreadPoolExecutor(max_workers=4, thread_name_prefix="omni-db-read")
        path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
        if path.is_symlink() or path.is_junction():
            raise BusinessError("unsafe_database", "Database cannot be a link")
        if path.exists():
            with sqlite3.connect(path.resolve().as_uri() + "?mode=ro", uri=True) as probe:
                tables = {row[0] for row in probe.execute("SELECT name FROM sqlite_master WHERE type='table'")}
                if tables and not {"instances", "alembic_version"}.issubset(tables):
                    raise BusinessError(
                        "foreign_database", "Existing database is not a V2 instance; choose an empty home"
                    )
        self._engine = create_engine(
            "sqlite+pysqlite:///" + str(path), poolclass=NullPool, connect_args={"check_same_thread": False}
        )

        @event.listens_for(self._engine, "connect")
        def setup(connection, _record):
            connection.isolation_level = None
            connection.execute("PRAGMA foreign_keys=ON")
            connection.execute("PRAGMA busy_timeout=5000")
            connection.execute("PRAGMA journal_mode=WAL")
            connection.execute("PRAGMA synchronous=FULL")

        @event.listens_for(self._engine, "begin")
        def begin(connection):
            connection.exec_driver_sql("BEGIN IMMEDIATE" if connection.info.get("omni_write") else "BEGIN")

        try:
            with self._engine.connect() as connection:
                connection.info["omni_write"] = True
                cfg = Config()
                cfg.set_main_option("script_location", str(Path(__file__).with_name("migrations")))
                cfg.attributes["connection"] = connection
                command.upgrade(cfg, "head")
                if connection.in_transaction():
                    connection.commit()
        except BaseException:
            self._engine.dispose()
            self._writer.shutdown(wait=False)
            self._readers.shutdown(wait=False)
            raise

    @property
    def metrics(self) -> dict:
        return {
            "pending_writes": self._writes,
            "pending_reads": self._reads,
            "closed": self._closed,
            "error_code": self._last_error,
        }

    async def _execute(self, operation: Callable[[UnitOfWork], T], *, write: bool) -> T:
        if self._closed:
            raise BusinessError("database_closed", "Database is closing", status=503)
        if (self._writes >= self.max_writes) if write else (self._reads >= self.max_reads):
            raise BusinessError("database_busy", "Database queue is full", retryable=True, status=503)
        if write:
            self._writes += 1
        else:
            self._reads += 1

        def perform() -> T:
            try:
                with self._engine.connect() as connection:
                    connection.info["omni_write"] = write
                    if not write:
                        connection.exec_driver_sql("PRAGMA query_only=ON")
                        connection.commit()
                    with connection.begin():
                        result = operation(Repositories(connection, self.clock, self.ids))
                    if write:
                        self._last_error = ""
                    return result
            except IntegrityError as exc:
                raise BusinessError("data_constraint", "Operation violates a data constraint") from exc
            except OperationalError as exc:
                self._last_error = "database_unavailable"
                raise BusinessError("database_unavailable", "Database operation failed", status=503) from exc

        future = asyncio.get_running_loop().run_in_executor(
            self._writer if write else self._readers, contextvars.copy_context().run, perform
        )
        self._pending.add(future)
        try:
            return await asyncio.shield(future)
        except asyncio.CancelledError:
            # A submitted commit cannot be cancelled with its HTTP request. Keep admission
            # charged until the real operation finishes; retries use the idempotency key.
            while not future.done():
                try:
                    await asyncio.shield(future)
                except asyncio.CancelledError:
                    continue
            # Retrieve any exception so cancelled clients do not leave unhandled futures.
            if not future.cancelled():
                future.exception()
            raise
        finally:
            self._pending.discard(future)
            if write:
                self._writes -= 1
            else:
                self._reads -= 1

    async def write(self, operation: Callable[[UnitOfWork], T]) -> T:
        return await self._execute(operation, write=True)

    async def read(self, operation: Callable[[UnitOfWork], T]) -> T:
        return await self._execute(operation, write=False)

    async def close(self) -> None:
        if self._closed:
            return
        self._closed = True
        if self._pending:
            await asyncio.gather(*self._pending, return_exceptions=True)
        self._writer.shutdown(wait=True)
        self._readers.shutdown(wait=True)
        self._engine.dispose()

    async def integrity(self) -> dict:
        def check(uow):
            return {
                "integrity": uow.connection.exec_driver_sql("PRAGMA integrity_check").scalar(),
                "foreign_keys": len(uow.connection.exec_driver_sql("PRAGMA foreign_key_check").all()),
                "tables": sorted(inspect(uow.connection).get_table_names()),
            }

        return await self.read(check)
