"""Compose existing release/registration/control use cases without holding instance locks across target startup."""

from __future__ import annotations

import asyncio
from collections.abc import Mapping
from typing import cast

from ..domain.contracts import BusinessError
from ..domain.releases import ReleasePlan
from ..domain.service_exchange import ExchangePlan, stage_identity
from ..domain.service_operations import registration_goal
from .ports import AdministratorHandoverPort, OwnedClosePort, Record
from .releases import Releases
from .service_control import ServiceControl
from .service_operations import ServiceOperations


class ExchangeSteps:
    def __init__(
        self,
        source: ServiceOperations,
        target: ServiceOperations,
        release: Releases,
        control: ServiceControl,
        owner: OwnedClosePort | None = None,
        handover: AdministratorHandoverPort | None = None,
    ):
        self.source, self.target, self.release, self.control = source, target, release, control
        self.owner = owner
        self.handover = handover

    async def admit(self, plan: ExchangePlan, stage: str, *, reconcile: bool = False) -> bool:
        if self.handover is not None and reconcile:
            if stage in {"close_source", "close_target"}:
                raise BusinessError(
                    "service_exchange_administrator_close_unresolved",
                    "A missing prior close acknowledgement needs operator review; a new session cannot prove old cleanup",
                )
            service = {"remove_source": self.source, "install_target": self.target, "start_target": self.control}.get(
                stage
            )
            if service is not None:
                previous = service.files.latest(cast(Record, dict(cast(Mapping[str, object], plan[stage]))))
                if previous is not None and previous.get("cleanup_complete") is not True:
                    raise BusinessError(
                        "service_exchange_administrator_cleanup_unresolved",
                        "Review the original native cleanup before opening another administrator session",
                    )
        return self.handover is None or await self.handover.admit(stage)

    def prepared(self, plan: ReleasePlan) -> None:
        self.release.files.installations(plan)
        history = self.release.files.history(plan)
        if not history or history[-1]["phase"] != "prepared":
            raise BusinessError("service_exchange_release_not_prepared", "Prepare and verify the release first")
        with self.release.files.exclusive(plan):
            self.release.files.require_fence(plan, "target")
            self.release.data.verify(plan, self.release.prepared(history))
            self.target.verify_operation_deployment(plan["target"])

    async def perform(self, plan: ExchangePlan, stage: str, *, reconcile: bool) -> Record:
        self.release.files.installations(plan["release"])
        success, verification, code = False, "", "service_exchange_unresolved"
        recovery = plan["release"]["recovery"]
        if stage == "remove_source" and recovery is not None and recovery["isolation"] is not None:
            with self.release.files.exclusive(plan["release"]):
                self.release.files.require_fence(plan["release"], "source")
                self.release.data.source(plan["release"])
            success, closed, verification = True, True, "quarantined_source_old_registration_retained"
        elif stage in {"remove_source", "install_target"}:
            service = self.source if stage == "remove_source" else self.target
            operation = cast(Record, plan[stage])
            previous = (
                await service.reconcile(operation, accept_observed_goal=True)
                if reconcile
                else await service.apply(operation, str(operation["sha256"]), accept_observed_goal=True)
            )
            report = cast(Record, previous.get("registration", {}))
            success = (
                previous.get("state") == "succeeded"
                and registration_goal(str(operation["action"]), report)
                and report.get("state") == "stopped"
            )
            verification = "observed_registration_postcondition" if success else ""
            closed = await service.close(8)
        elif stage in {"close_source", "close_target"}:
            if self.handover is None:
                raise BusinessError(
                    "service_exchange_handover_missing", "The source administrator must release its native lock"
                )
            close = self.handover.close_source if stage == "close_source" else self.handover.close_target
            success = closed = await close(8)
            verification = (
                stage.removeprefix("close_") + "_administrator_reclaimed_and_lock_released" if success else ""
            )
        elif stage == "select_target":
            result = await self.release.select(plan["release"], plan["release"]["sha256"])
            success = result["state"] == "selected"
            verification = "persisted_source_retired_target_selected" if success else ""
            closed = await self.release.close(8)
        elif stage == "start_target":
            operation = plan["start_target"]
            raw = cast(Record, dict(operation))
            if reconcile and self.control.files.latest(raw) is None:
                closed = await self.control.close(8)
            else:
                result = await self.control.apply(
                    operation, operation["sha256"], reconcile=reconcile, verify_terminal=True
                )
                # Never turn a historical successful start receipt into a claim of current readiness.
                success = (
                    result.get("state") == "succeeded"
                    and result.get("boot_id") == operation["boot_id"]
                    and result.get("currently_verified", True) is True
                )
                verification = "signed_ready_reserved_boot" if success else ""
                closed = await self.control.close(8)
        else:
            raise BusinessError("service_exchange_stage_invalid", "Choose a fixed exchange stage")
        identity, sha = stage_identity(plan, stage)
        return {
            "stage": stage,
            "operation_id": identity,
            "operation_sha256": sha,
            "succeeded": success,
            "cleanup_complete": closed,
            "error_code": "" if success else code,
            "verification": verification,
        }

    async def close(self, timeout_s: float) -> bool:
        deadline = asyncio.get_running_loop().time() + max(0, timeout_s)
        closed: list[bool] = []
        for service in (self.source, self.target, self.release, self.control):
            closed.append(await service.close(max(0, deadline - asyncio.get_running_loop().time())))
        if self.owner is not None:
            closed.append(await self.owner.close(max(0, deadline - asyncio.get_running_loop().time())))
        return all(closed)
