"""Deterministic supervision loop for managed long jobs (architecture spec §11)."""

from __future__ import annotations

import asyncio
import logging

from ..domain.omniweave import WATCHDOG_SCAN_S
from .ports import DatabasePort

log = logging.getLogger("omni.v2.watchdog")


class Watchdog:
    """Independent supervision coroutine owned by the OmniWeave feature.

    Every WATCHDOG_SCAN_S it runs one deterministic scan transaction (stall
    alerts and bounded safe requeues). A failed scan is logged and retried on
    the next cadence; the loop itself never dispatches business work.
    """

    def __init__(self, database: DatabasePort) -> None:
        self._database = database
        self._task: asyncio.Task[None] | None = None

    def start(self) -> None:
        if self._task is None:
            self._task = asyncio.create_task(self._loop(), name="owa-watchdog")

    def stop(self) -> None:
        if self._task is not None:
            self._task.cancel()

    async def close(self) -> None:
        task, self._task = self._task, None
        if task is not None:
            task.cancel()
            try:
                await task
            except asyncio.CancelledError:
                pass

    async def _loop(self) -> None:
        while True:
            try:
                await self._database.write(lambda unit: unit.omniweave_watchdog_scan())
            except asyncio.CancelledError:
                raise
            except Exception:  # noqa: BLE001 -- supervision never dies on one bad scan
                log.exception("Watchdog scan failed", extra={"event": "owa.watchdog.scan_failed"})
            await asyncio.sleep(WATCHDOG_SCAN_S)
