"""Offline registration use cases with durable intent, no replay, and retained cancellation ownership."""

from __future__ import annotations

import asyncio
from contextlib import AbstractContextManager
from typing import cast

from ..domain.contracts import BusinessError
from ..domain.service_operations import make_operation, registration_goal, registration_precondition, validate_operation
from .ports import Clock, DeploymentFilesPort, Ids, Record, ServiceOperationFilesPort, ServiceRegistrationPort


class ServiceOperations:
    """One offline command owns its instance lock through actual native cleanup."""

    def __init__(
        self,
        deployments: DeploymentFilesPort,
        files: ServiceOperationFilesPort,
        native: ServiceRegistrationPort,
        clock: Clock,
        ids: Ids,
    ) -> None:
        self.deployments, self.files, self.native, self.clock, self.ids = deployments, files, native, clock, ids
        self._pending: asyncio.Task[Record] | None = None
        self._guard: AbstractContextManager[None] | None = None
        self._operation: Record | None = None
        self._receipt: Record | None = None

    def plan(self, bundle: str, action: str, output: str) -> Record:
        verification = self.deployments.verify(bundle)
        self._eligible(verification)
        operation = make_operation(verification["plan"], action, self.ids.new(), self.clock.now())
        return self.files.publish(operation, output)

    @staticmethod
    def _eligible(verification: Record) -> None:
        if verification.get("valid") is not True or verification.get("eligible") is not True:
            raise BusinessError(
                "deployment_not_eligible", "Service changes require the verified immutable installation"
            )

    def _verify_operation_deployment(self, deployment: Record) -> None:
        request = cast(Record, deployment["request"])
        verification = self.deployments.verify(str(request["bundle_dir"]))
        self._eligible(verification)
        if verification.get("plan") != deployment:
            raise BusinessError(
                "service_operation_changed", "The installed deployment differs from the reviewed operation"
            )

    async def apply(self, operation: Record, approved_sha256: str, *, accept_observed_goal: bool = False) -> Record:
        checked = validate_operation(operation)
        if approved_sha256 != checked["sha256"]:
            raise BusinessError("service_operation_not_approved", "Approve the exact reviewed operation fingerprint")
        return await self._start(checked, reconcile=False, accept_observed_goal=accept_observed_goal)

    async def reconcile(self, operation: Record, *, accept_observed_goal: bool = False) -> Record:
        return await self._start(
            validate_operation(operation), reconcile=True, accept_observed_goal=accept_observed_goal
        )

    async def cancel(self, operation: Record) -> Record:
        return await self._start(validate_operation(operation), reconcile=False, cancel=True)

    async def _start(
        self, operation: Record, *, reconcile: bool, cancel: bool = False, accept_observed_goal: bool = False
    ) -> Record:
        if self._pending is not None or self._guard is not None:
            raise BusinessError("service_operation_busy", "One offline service command may be owned at a time")
        self._pending = asyncio.create_task(
            self._run(operation, reconcile=reconcile, cancel=cancel, accept_observed_goal=accept_observed_goal),
            name="service-operation",
        )
        self._pending.add_done_callback(lambda done: done.exception() if not done.cancelled() else None)
        return await asyncio.shield(self._pending)

    async def _run(
        self, operation: Record, *, reconcile: bool, cancel: bool, accept_observed_goal: bool = False
    ) -> Record:
        deployment = cast(Record, operation["deployment"])
        self._verify_operation_deployment(deployment)
        guard = self.files.exclusive(operation)
        guard.__enter__()
        self._guard = guard
        self._operation = operation
        try:
            previous = self.files.latest(operation)
            if previous is not None and previous.get("terminal") is True and not (reconcile and accept_observed_goal):
                self._receipt = previous
                return previous
            if cancel:
                if previous is None:
                    raise BusinessError(
                        "service_operation_not_started", "There is no persisted maintenance intent to cancel"
                    )
                if self.files.dispatched(operation):
                    raise BusinessError(
                        "service_operation_needs_reconciliation",
                        "A dispatched or unknown action cannot be cancelled as an ordinary failure",
                    )
                result = self._record(
                    operation, "cancelled", "none", "", {}, terminal=True, verification="no_persisted_dispatch"
                )
                return result
            if reconcile:
                if previous is None and not accept_observed_goal:
                    raise BusinessError("service_operation_not_started", "There is no persisted operation to reconcile")
                report = await self.native.inspect(deployment)
                goal = registration_goal(str(operation["action"]), report)
                if accept_observed_goal:
                    # Exchange resumption is read-only even when the child intent was never written.
                    if goal and report.get("state") == "stopped":
                        if previous is None:
                            self.files.activate(operation)
                        result = self._record(
                            operation,
                            "succeeded",
                            "applied",
                            "",
                            report,
                            terminal=True,
                            verification="observed_registration_postcondition",
                        )
                        return result
                    return {
                        "state": "unknown",
                        "operation_id": operation["id"],
                        "operation_sha256": operation["sha256"],
                        "registration": report,
                        "verification": "observed_registration_postcondition",
                        "ready": False,
                    }
                if goal:
                    result = self._record(
                        operation,
                        "succeeded",
                        "applied",
                        "",
                        report,
                        terminal=True,
                        verification="observed_registration_postcondition",
                    )
                    return result
                return self._record(operation, "unknown", "unknown", "service_outcome_unresolved", report)
            if previous is not None:
                # Even a crash between intent and spawn cannot authorize replay.
                raise BusinessError(
                    "service_operation_needs_reconciliation", "Inspect the persisted operation; never replay it"
                )
            self.files.activate(operation)
            self._record(operation, "intent", "none", "", {})
            dispatched = False
            try:
                report = await self.native.inspect(deployment)
                if (
                    accept_observed_goal
                    and registration_goal(str(operation["action"]), report)
                    and report.get("state") == "stopped"
                ):
                    result = self._record(
                        operation,
                        "succeeded",
                        "none",
                        "",
                        report,
                        terminal=True,
                        verification="observed_registration_without_dispatch",
                    )
                    return result
                if not registration_precondition(str(operation["action"]), report):
                    result = self._record(
                        operation, "denied", "none", "service_registration_precondition", report, terminal=True
                    )
                    return result
                # Re-hash program, bundle, dependencies and principal immediately before dispatch.
                self._verify_operation_deployment(deployment)
                self._record(operation, "dispatch", "unknown", "", report)
                dispatched = True
                changed = await self.native.change(deployment, str(operation["action"]))
                report = await self.native.inspect(deployment)
                if changed.get("effect") == "applied" and registration_goal(str(operation["action"]), report):
                    result = self._record(
                        operation,
                        "succeeded",
                        "applied",
                        "",
                        report,
                        terminal=True,
                        verification="command_and_registration_postcondition",
                    )
                    return result
                if changed.get("effect") == "none":
                    result = self._record(
                        operation,
                        "denied",
                        "none",
                        str(changed.get("error_code") or "service_change_denied"),
                        report,
                        terminal=True,
                    )
                    return result
                return self._record(operation, "unknown", "unknown", "service_outcome_unresolved", report)
            except BaseException as exc:
                code = exc.code if isinstance(exc, BusinessError) else "service_operation_interrupted"
                result = self._record(
                    operation,
                    "unknown" if dispatched else "failed",
                    "unknown" if dispatched else "none",
                    code,
                    {},
                    terminal=not dispatched,
                )
                if isinstance(exc, (KeyboardInterrupt, SystemExit, asyncio.CancelledError)):
                    raise
                return result
        finally:
            # A failed helper cleanup keeps the actual instance descriptor owned.
            if await self.native.close(6):
                self._settle()
                self._release()

    def _record(
        self,
        operation: Record,
        state: str,
        effect: str,
        code: str,
        report: Record,
        *,
        terminal: bool = False,
        verification: str = "",
    ) -> Record:
        result: Record = {
            "operation_id": operation["id"],
            "operation_sha256": operation["sha256"],
            "action": operation["action"],
            "state": state,
            "effect": effect,
            "terminal": terminal,
            "error_code": code,
            "registration": report,
            "ready": False,
            "recorded_at": self.clock.now(),
            "verification": verification,
            "cleanup_complete": False,
        }
        self.files.append(operation, result)
        self._receipt = result
        return result

    def _settle(self) -> None:
        if self._operation is None or self._guard is None:
            return
        latest = self.files.latest(self._operation)
        if latest is not None:
            if latest.get("cleanup_complete") is not True:
                latest = {**latest, "cleanup_complete": True}
                self.files.append(self._operation, latest)
            if self._receipt is not None:
                self._receipt["cleanup_complete"] = True
            if latest.get("terminal") is True:
                self.files.settled(self._operation)

    def _release(self) -> None:
        guard, self._guard = self._guard, None
        if guard is not None:
            guard.__exit__(None, None, None)

    async def close(self, timeout_s: float = 30) -> bool:
        deadline = asyncio.get_running_loop().time() + max(0, timeout_s)
        if self._pending is not None:
            done, _ = await asyncio.wait((self._pending,), timeout=max(0, timeout_s))
            if not done:
                return False
        if not await self.native.close(max(0, deadline - asyncio.get_running_loop().time())):
            return False
        self._settle()
        self._release()
        return True
