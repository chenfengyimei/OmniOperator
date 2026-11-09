"""Bounded operational queries and privacy-preserving diagnostic export; no model or device actions."""

from __future__ import annotations

import asyncio
from collections.abc import Awaitable, Callable
from typing import TypeVar

from ..domain.contracts import BusinessError
from .debugging import DebugService
from .ports import DatabasePort, DiagnosticPort, MetricsPort, Record

T = TypeVar("T")


class OperationsService:
    def __init__(
        self,
        database: DatabasePort,
        metrics: MetricsPort,
        diagnostic: DiagnosticPort,
        health: Callable[[], Record],
        debugging: DebugService,
    ) -> None:
        self._db, self._metrics, self._diagnostic = database, metrics, diagnostic
        self._health = health
        self._debugging = debugging
        self._slots = asyncio.Semaphore(4)
        self._pending: set[asyncio.Task] = set()
        self._closing = False
        self._port_close: asyncio.Task | None = None

    def observe(self, operation: str, outcome: str, seconds: float) -> None:
        self._metrics.observe(operation, outcome, seconds)

    async def _information(self) -> Record:
        persisted = await self._db.read(lambda u: u.operations_status())
        host = await self._diagnostic.describe()
        return {
            "schema": 1,
            "persisted": persisted,
            "measurements": self._metrics.snapshot(),
            "host": host,
            "runtime": self._health(),
        }

    async def _owned(self, operation: Callable[[], Awaitable[T]]) -> T:
        if self._closing:
            raise BusinessError("operations_closing", "Operational diagnostics are draining", status=503)
        if self._slots.locked():
            raise BusinessError("diagnostics_busy", "Diagnostic queries are busy", status=503, retryable=True)
        await self._slots.acquire()

        async def perform():
            return await operation()

        task = asyncio.create_task(perform(), name="omni-diagnostic-query")
        self._pending.add(task)

        def completed(future):
            self._pending.discard(future)
            self._slots.release()
            if not future.cancelled():
                future.exception()

        task.add_done_callback(completed)
        return await asyncio.shield(task)

    async def status(self) -> Record:
        return await self._owned(self._information)

    async def export(self) -> bytes:
        async def perform():
            return await self._diagnostic.bundle(await self._information())

        return await self._owned(perform)

    async def logs(self, filters: Record) -> Record:
        return await self._owned(lambda: self._diagnostic.log_page(filters))

    async def debug_status(self, agent_id: str) -> Record:
        return await self._owned(lambda: self._debugging.status(agent_id))

    async def debug_start(self, agent_id: str, duration_s: int) -> Record:
        return await self._owned(lambda: self._debugging.enable(agent_id, duration_s))

    async def debug_stop(self, agent_id: str, operation_id: str) -> Record:
        return await self._owned(lambda: self._debugging.disable(agent_id, operation_id))

    async def debug_logs(self, agent_id: str, operation_id: str, filters: Record) -> Record:
        return await self._owned(lambda: self._debugging.logs(agent_id, operation_id, filters))

    def stop_accepting(self) -> None:
        self._closing = True
        self._debugging.stop_accepting()

    async def close(self, timeout_s: float = 30) -> bool:
        self.stop_accepting()
        deadline = asyncio.get_running_loop().time() + max(0, timeout_s)
        if self._pending:
            _, pending = await asyncio.wait(self._pending, timeout=max(0, deadline - asyncio.get_running_loop().time()))
            if pending:
                return False
        if self._port_close is None or (
            self._port_close.done()
            and (
                self._port_close.cancelled()
                or self._port_close.exception() is not None
                or self._port_close.result() is not True
            )
        ):

            async def finish():
                if not await self._debugging.close():
                    return False
                return await self._diagnostic.close(max(0, deadline - asyncio.get_running_loop().time()))

            self._port_close = asyncio.create_task(finish(), name="diagnostics-close")
        _, pending = await asyncio.wait(
            (self._port_close,), timeout=max(0, deadline - asyncio.get_running_loop().time())
        )
        return (
            not pending
            and not self._port_close.cancelled()
            and self._port_close.exception() is None
            and self._port_close.result() is True
        )
