"""Bounded, single-writer database actor with isolated read connections."""

from __future__ import annotations

import asyncio
import contextvars
from collections.abc import Callable
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from typing import TypeVar

from alembic import command
from sqlalchemy import inspect
from sqlalchemy.exc import IntegrityError, OperationalError

from ...application.ports import Clock, Ids, MetricsPort, UnitOfWork
from ...domain.contracts import BusinessError
from .migration_config import configuration
from .migration_config import head as migration_head
from .repository import Repositories
from .sqlalchemy_access import database_engine
from .sqlite_access import sqlite_connection

T = TypeVar("T")


class Database:
    def __init__(
        self,
        path: Path,
        clock: Clock,
        ids: Ids,
        *,
        max_writes: int = 256,
        max_reads: int = 64,
        objects_root: Path | None = None,
        boot_id: str | None = None,
        metrics: MetricsPort | None = None,
    ) -> None:
        self.path, self.clock, self.ids = path, clock, ids
        self._measurements = metrics
        self.boot_id = boot_id or ids.new()
        self.max_writes, self.max_reads = max_writes, max_reads
        self._writes = self._reads = 0
        self._closed = False
        self._last_error = ""
        cfg = configuration()
        head = migration_head(cfg)
        old_revision = None
        self._pending: set[asyncio.Future] = set()
        self._writer = ThreadPoolExecutor(max_workers=1, thread_name_prefix="omni-db-write")
        self._readers = ThreadPoolExecutor(max_workers=4, thread_name_prefix="omni-db-read")
        path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
        if path.is_symlink() or path.is_junction():
            raise BusinessError("unsafe_database", "Database cannot be a link")
        if path.exists():
            with sqlite_connection(path) as probe:
                tables = {row[0] for row in probe.execute("SELECT name FROM sqlite_master WHERE type='table'")}
                if tables and not {"instances", "alembic_version"}.issubset(tables):
                    raise BusinessError(
                        "foreign_database", "Existing database is not a V2 instance; choose an empty home"
                    )
                if "alembic_version" in tables:
                    old_revision = probe.execute("SELECT version_num FROM alembic_version").fetchone()[0]
        if old_revision and old_revision != head:
            from .maintenance import migration_snapshot

            migration_snapshot(path, objects_root)
        self._engine = database_engine(path)

        try:
            with self._engine.connect() as connection:
                connection.info["omni_write"] = True
                cfg.attributes["connection"] = connection
                command.upgrade(cfg, "head")
                if connection.in_transaction():
                    connection.commit()
                self.schema_version = str(
                    connection.exec_driver_sql("SELECT version_num FROM alembic_version").scalar_one()
                )
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

        queued_at = self.clock.monotonic()

        def perform() -> T:
            outcome = "ok"
            try:
                with self._engine.connect() as connection:
                    connection.info["omni_write"] = write
                    if not write:
                        connection.exec_driver_sql("PRAGMA query_only=ON")
                        connection.commit()
                    with connection.begin():
                        result = operation(Repositories(connection, self.clock, self.ids, self.boot_id))
                    if write:
                        self._last_error = ""
                    return result
            except IntegrityError as exc:
                outcome = "error"
                raise BusinessError("data_constraint", "Operation violates a data constraint") from exc
            except OperationalError as exc:
                outcome = "error"
                self._last_error = "database_unavailable"
                raise BusinessError("database_unavailable", "Database operation failed", status=503) from exc
            except BaseException:
                outcome = "error"
                raise
            finally:
                if self._measurements:
                    self._measurements.observe(
                        "database_write" if write else "database_read",
                        outcome,
                        max(0, self.clock.monotonic() - queued_at),
                    )

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
