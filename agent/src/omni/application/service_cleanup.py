"""Review quarantined native definitions and reuse once-only registration use cases; never clears original unknowns."""

from __future__ import annotations

from typing import cast

from ..domain.contracts import BusinessError
from ..domain.releases import digest
from ..domain.service_cleanup import CleanupPlan, make_cleanup, validate_cleanup
from ..domain.service_exchange import progress, validate_exchange
from .ports import CleanupPlanFilesPort, Clock, ExchangeFilesPort, Ids, Record, ServiceOperationFilesPort
from .service_operations import ServiceOperations


class CleanupReview:
    def __init__(self, exchanges: ExchangeFilesPort, plans: CleanupPlanFilesPort, clock: Clock, ids: Ids) -> None:
        self.exchanges, self.plans, self.clock, self.ids = exchanges, plans, clock, ids

    def plan(self, value: object, side: str, output: str) -> Record:
        exchange = validate_exchange(value)
        with self.exchanges.exclusive(exchange):
            history = self.exchanges.history(exchange)
            if progress(exchange, history)[2] != "isolated":
                raise BusinessError("service_cleanup_not_isolated", "Complete the exact offline isolation first")
            plan = make_cleanup(
                exchange,
                len(history),
                digest(history),
                history[-1]["proof"],
                side,
                self.ids.new(),
                self.ids.new(),
                self.clock.now(),
            )
            return self.plans.publish(plan, output)


class ServiceCleanup:
    def __init__(self, plan: CleanupPlan, files: ServiceOperationFilesPort, operations: ServiceOperations) -> None:
        self.plan, self.files, self.operations = validate_cleanup(plan), files, operations

    def result(self, receipt: Record | None, *, live: bool = False) -> Record:
        return {
            "cleanup_id": self.plan["id"],
            "sha256": self.plan["sha256"],
            "side": self.plan["side"],
            "state": "not_started" if receipt is None else receipt.get("state", "unknown"),
            "receipt": receipt,
            "ready": False,
            "live_observation": live,
            "absence_observed_at": receipt.get("recorded_at")
            if receipt is not None and receipt.get("state") == "succeeded"
            else None,
            "original_effect": "unknown",
            "original_records_preserved": True,
            "late_effects_possible": True,
        }

    def inspect(self) -> Record:
        with self.files.exclusive(self.plan["operation"]):
            return self.result(self.files.latest(self.plan["operation"]))

    async def apply(self, approval: str) -> Record:
        if approval != self.plan["sha256"]:
            raise BusinessError("service_cleanup_not_approved", "Approve the complete cleanup fingerprint")
        operation = cast(Record, self.plan["operation"])
        receipt = await self.operations.apply(operation, str(operation["sha256"]), accept_observed_goal=True)
        return self.result(receipt)

    async def reconcile(self) -> Record:
        receipt = await self.operations.reconcile(self.plan["operation"], accept_observed_goal=True)
        return self.result(receipt, live=True)

    async def cancel(self) -> Record:
        return self.result(await self.operations.cancel(self.plan["operation"]))

    async def close(self, timeout_s: float = 30) -> bool:
        return await self.operations.close(timeout_s)
