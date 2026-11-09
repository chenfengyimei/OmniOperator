"""Boot-scoped debug authorization, durable audit and bounded maintenance; no log files here."""

from __future__ import annotations

import asyncio
import logging

from ..domain.contracts import BusinessError
from .ports import Clock, DatabasePort, DiagnosticPort, Ids, Record

log = logging.getLogger("omni.v2.debugging")


class DebugService:
    def __init__(self, database: DatabasePort, diagnostic: DiagnosticPort, clock: Clock, ids: Ids) -> None:
        self._db, self._files, self._clock, self._ids = database, diagnostic, clock, ids
        self._lock = asyncio.Lock()
        self._current: Record | None = None
        self._stop = asyncio.Event()
        self._worker: asyncio.Task | None = None
        self._last_cleanup = float("-inf")
        self.error_code = ""

    def start(self) -> None:
        self._worker = asyncio.create_task(self._maintain(), name="omni-debug-maintenance")

    async def _audit(self, current: Record, kind: str, data: Record) -> None:
        await self._db.write(
            lambda u: u.diagnostic_audit(str(current["agent_id"]), str(current["operation_id"]), kind, data)
        )

    async def _settle(self, snapshot: Record) -> None:
        if self._current is not None and not snapshot.get("active"):
            await self._audit(self._current, "diagnostic.debug.stopped", {"reason": snapshot.get("reason", "stopped")})
            self._current = None

    async def status(self, agent_id: str) -> Record:
        await self._db.read(lambda u: u.diagnostic_agent(agent_id))
        async with self._lock:
            snapshot = await self._files.debug_snapshot()
            await self._settle(snapshot)
            if snapshot.get("agent_id") not in {None, agent_id}:
                return {"active": False, "occupied": bool(snapshot.get("active")), "scope": "another_agent"}
            return {**snapshot, "audit_error_code": self.error_code}

    async def enable(self, agent_id: str, duration_s: int) -> Record:
        if type(duration_s) is not int or not 1 <= duration_s <= 3600:
            raise BusinessError("invalid_debug_duration", "Debug capture must last 1 to 3600 seconds", status=422)
        if self._stop.is_set():
            raise BusinessError("operations_closing", "Diagnostic maintenance is draining", status=503)
        async with self._lock:
            snapshot = await self._files.debug_snapshot()
            await self._settle(snapshot)
            if snapshot.get("active"):
                raise BusinessError("debug_already_active", "Stop the active debug capture before starting another")
            current: Record = {"agent_id": agent_id, "operation_id": self._ids.new()}
            await self._audit(
                current, "diagnostic.debug.requested", {"duration_s": duration_s, "byte_limit": 134217728}
            )
            try:
                result = await self._files.debug_start(agent_id, str(current["operation_id"]), duration_s)
            except Exception:
                await self._audit(current, "diagnostic.debug.failed", {"reason": "activation_failed"})
                raise
            self._current = current
            try:
                await self._audit(
                    current, "diagnostic.debug.started", {"duration_s": duration_s, "byte_limit": 134217728}
                )
            except BaseException:
                # Privacy control is stopped even when the second audit commit fails.
                await self._files.debug_stop("audit_failed")
                raise
            return result

    async def disable(self, agent_id: str, operation_id: str) -> Record:
        await self._db.read(lambda u: u.diagnostic_agent(agent_id))
        async with self._lock:
            snapshot = await self._files.debug_snapshot()
            if snapshot.get("agent_id") != agent_id or snapshot.get("operation_id") != operation_id:
                raise BusinessError("debug_capture_changed", "The debug capture has changed; reload its state")
            result = await self._files.debug_stop("owner_stopped")
            await self._settle(result)
            return result

    async def logs(self, agent_id: str, operation_id: str, filters: Record) -> Record:
        await self._db.read(lambda u: u.diagnostic_agent(agent_id))
        return await self._files.debug_logs(agent_id, operation_id, filters)

    async def _maintain(self) -> None:
        while not self._stop.is_set():
            try:
                async with self._lock:
                    await self._settle(await self._files.debug_snapshot())
                    if self._clock.monotonic() - self._last_cleanup >= 60:
                        await self._files.debug_maintenance()
                        self._last_cleanup = self._clock.monotonic()
                self.error_code = ""
            except Exception:  # noqa: BLE001 -- retry optional maintenance without leaking exception text
                self.error_code = "debug_maintenance_failed"
                log.warning(
                    "Debug maintenance will retry",
                    extra={"event": "debug.maintenance_failed", "error_code": self.error_code},
                )
            try:
                await asyncio.wait_for(self._stop.wait(), timeout=1)
            except TimeoutError:
                pass

    def stop_accepting(self) -> None:
        self._stop.set()

    async def close(self) -> bool:
        self.stop_accepting()
        if self._worker is not None:
            await asyncio.shield(self._worker)
        async with self._lock:
            snapshot = await self._files.debug_snapshot()
            if snapshot.get("active"):
                snapshot = await self._files.debug_stop("shutdown")
            await self._settle(snapshot)
        return True
