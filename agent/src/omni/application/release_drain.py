"""Bind release draining to the existing once-only boot control; reconciliation never sends another request."""

from __future__ import annotations

from typing import cast

from ..domain.service_control import ControlPlan
from .ports import Record
from .service_control import ServiceControl


class ReleaseDrain:
    def __init__(self, control: ServiceControl) -> None:
        self.control = control

    async def probe(self, deployment: Record) -> Record:
        return await self.control.boot.probe(deployment)

    async def stop(self, operation: ControlPlan, *, reconcile: bool) -> Record:
        raw = cast(Record, dict(operation))
        if reconcile and self.control.files.latest(raw) is None:
            self.control.verify(cast(Record, operation["deployment"]))
            return {
                "state": "succeeded" if self.control.files.closed(raw) else "unknown",
                "boot_id": operation["boot_id"],
            }
        return await self.control.apply(operation, operation["sha256"], reconcile=reconcile, verify_terminal=True)

    async def close(self, timeout_s: float) -> bool:
        return await self.control.close(timeout_s)
