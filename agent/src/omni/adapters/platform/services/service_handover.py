"""Two pinned root phases, opened in order and owned until native lock release; never elevates or spawns a broker."""

from __future__ import annotations

import asyncio
from typing import cast

from omni.adapters.platform.services.service_admin_client import ExchangeAdministrator
from omni.application.ports import Record
from omni.domain.contracts import BusinessError
from omni.domain.service_admin import socket_path
from omni.domain.service_exchange import administrator_approval, stages, validate_exchange


class AdministratorHandover:
    def __init__(self, value: Record) -> None:
        self.plan = validate_exchange(value)
        self.parents: dict[str, ExchangeAdministrator] = {}
        for role in ("source", "target"):
            approval = cast(Record, administrator_approval(self.plan, role))
            self.parents[role] = ExchangeAdministrator(approval, socket_path(approval), str(approval["broker_sha256"]))
        self._admitted: set[str] = set()
        self._source_closed = False
        self._target_closed = False

    def prepare_resume(self, completed: int) -> None:
        self._source_closed = completed > stages(self.plan).index("close_source")
        self._target_closed = completed > stages(self.plan).index("close_target")

    async def admit(self, stage: str) -> bool:
        if stage not in stages(self.plan):
            raise BusinessError("service_exchange_stage_invalid", "Choose a reviewed handover stage")
        if stage in {"remove_source", "select_target", "close_source"}:
            role = "source"
            if self._source_closed:
                raise BusinessError(
                    "service_exchange_source_phase_closed", "The completed source phase cannot be reopened"
                )
        else:
            role = "target"
            if self._target_closed:
                raise BusinessError("service_exchange_target_phase_closed", "The completed target phase cannot reopen")
            if not self._source_closed:
                raise BusinessError(
                    "service_exchange_source_cleanup_pending",
                    "Source administrator cleanup must finish before target admission",
                )
        if role not in self._admitted:
            if not await self.parents[role].probe():
                return False
            self._admitted.add(role)
        return True

    async def close_source(self, timeout_s: float) -> bool:
        if self._source_closed:
            return True
        if not await self.parents["source"].close(timeout_s):
            return False
        self._source_closed = True
        return True

    async def close_target(self, timeout_s: float) -> bool:
        if not self._source_closed:
            raise BusinessError("service_exchange_source_cleanup_pending", "Source phase cleanup is required")
        if self._target_closed:
            return True
        if not await self.parents["target"].close(timeout_s):
            return False
        self._target_closed = True
        return True

    async def close(self, timeout_s: float = 30) -> bool:
        deadline = asyncio.get_running_loop().time() + max(0, timeout_s)
        complete: list[bool] = []
        for parent in self.parents.values():
            complete.append(await parent.close(max(0, deadline - asyncio.get_running_loop().time())))
        return all(complete)
