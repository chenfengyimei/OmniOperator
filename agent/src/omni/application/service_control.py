"""Reviewed start and graceful stop use cases; external dispatch happens once and unknown work is never replayed."""

from __future__ import annotations

import asyncio
from contextlib import AbstractContextManager
from typing import cast

from ..domain.contracts import BusinessError
from ..domain.service_control import (
    ControlPlan,
    make_control,
    start_postcondition,
    start_precondition,
    validate_control,
)
from .ports import Clock, DeploymentFilesPort, Ids, Record, ServiceBootPort, ServiceControlFilesPort, ServiceStarterPort


class ServiceControl:
    def __init__(
        self,
        deployments: DeploymentFilesPort,
        files: ServiceControlFilesPort,
        native: ServiceStarterPort,
        boot: ServiceBootPort,
        clock: Clock,
        ids: Ids,
    ) -> None:
        self.deployments, self.files, self.native, self.boot, self.clock, self.ids = (
            deployments,
            files,
            native,
            boot,
            clock,
            ids,
        )
        self._pending: asyncio.Task[Record] | None = None
        self._guard: AbstractContextManager[None] | None = None
        self._operation: Record | None = None
        self._receipt: Record | None = None

    def verify(self, deployment: Record) -> None:
        report = self.deployments.verify(str(cast(Record, deployment["request"])["bundle_dir"]))
        if report.get("eligible") is not True or report.get("valid") is not True or report.get("plan") != deployment:
            raise BusinessError("service_control_deployment_changed", "A verified immutable deployment is required")

    async def plan(self, bundle: str, action: str, output: str) -> Record:
        report = self.deployments.verify(bundle)
        if report.get("eligible") is not True or report.get("valid") is not True:
            raise BusinessError("service_control_deployment_changed", "A verified immutable deployment is required")
        deployment = cast(Record, report["plan"])
        self.verify(deployment)
        boot_id = self.ids.new()
        if action == "stop":
            identity = await self.boot.probe(deployment)
            if identity.get("error_code") is not None or not isinstance(identity.get("boot_id"), str):
                raise BusinessError(
                    "service_control_boot_unverified", "Prove the exact running boot before planning a stop"
                )
            boot_id = str(identity["boot_id"])
        value = make_control(deployment, action, self.ids.new(), self.clock.now(), boot_id)
        return self.files.publish(cast(Record, dict(value)), output)

    async def apply(
        self,
        value: object,
        approval: str,
        *,
        reconcile: bool = False,
        cancel: bool = False,
        verify_terminal: bool = False,
    ) -> Record:
        operation = validate_control(value)
        if not reconcile and not cancel and operation["sha256"] != approval:
            raise BusinessError(
                "service_control_not_approved", "Approve the exact reviewed service control fingerprint"
            )
        if self._pending is not None or self._guard is not None:
            raise BusinessError("service_control_busy", "The actual service operation remains owned")
        self._pending = asyncio.create_task(
            self._run(operation, reconcile, cancel, verify_terminal), name="reviewed-service-control"
        )
        self._pending.add_done_callback(lambda done: done.exception() if not done.cancelled() else None)
        return await asyncio.shield(self._pending)

    def record(
        self, op: ControlPlan, state: str, code: str = "", *, terminal: bool = False, observed: Record | None = None
    ) -> Record:
        value: Record = {
            "operation_id": op["id"],
            "operation_sha256": op["sha256"],
            "action": op["action"],
            "boot_id": op["boot_id"],
            "state": state,
            "effect": "applied" if state == "succeeded" else "unknown" if state in {"dispatch", "unknown"} else "none",
            "terminal": terminal,
            "error_code": code,
            "observed": observed or {},
            "recorded_at": self.clock.now(),
            "cleanup_complete": False,
            "verification": "observed_reviewed_boot"
            if state == "succeeded" and op["action"] == "start"
            else "observed_closed_reviewed_boot"
            if state == "succeeded"
            else "",
        }
        self.files.append(cast(Record, dict(op)), value)
        self._receipt = value
        return value

    async def goal(self, op: ControlPlan) -> Record | None:
        raw = cast(Record, dict(op))
        if op["action"] == "stop":
            return {"closed": True, "instance_lock_released": True} if self.files.closed(raw) else None
        proof = await self.boot.probe(cast(Record, op["deployment"]))
        if proof.get("ready") is not True or proof.get("boot_id") != op["boot_id"]:
            return None
        native = await self.native.inspect(cast(Record, op["deployment"]))
        if start_postcondition(cast(Record, op["deployment"]), native, proof):
            self.files.finish_launch(raw)
            return proof
        return None

    async def _run(
        self, op: ControlPlan, reconcile: bool, cancel: bool = False, verify_terminal: bool = False
    ) -> Record:
        self.verify(cast(Record, op["deployment"]))
        raw = cast(Record, dict(op))
        guard = self.files.exclusive(raw)
        guard.__enter__()
        self._guard = guard
        self._operation = raw
        try:
            previous = self.files.latest(raw)
            if previous is not None and previous.get("terminal") is True:
                if reconcile and verify_terminal:
                    previous = {
                        **previous,
                        "currently_verified": previous.get("state") == "succeeded" and await self.goal(op) is not None,
                    }
                self._receipt = previous
                return previous
            if cancel:
                if previous is None or self.files.dispatched(raw):
                    raise BusinessError(
                        "service_control_needs_reconciliation", "Only an undispatched intent can be cancelled"
                    )
                result = self.record(op, "cancelled", terminal=True)
                return result
            if reconcile:
                if previous is None:
                    raise BusinessError("service_control_not_started", "There is no persisted operation to reconcile")
                observed = await self.goal(op)
                if observed is not None:
                    result = self.record(op, "succeeded", terminal=True, observed=observed)
                    return result
                return self.record(op, "unknown", "service_control_unresolved")
            if previous is not None:
                raise BusinessError(
                    "service_control_needs_reconciliation",
                    "Inspect or reconcile the original operation; never replay it",
                )
            self.files.activate(raw)
            self.record(op, "intent")
            if op["action"] == "start":
                before = await self.native.inspect(cast(Record, op["deployment"]))
                allowed = start_precondition(before)
            else:
                before = await self.boot.probe(cast(Record, op["deployment"]))
                allowed = before.get("error_code") is None and before.get("boot_id") == op["boot_id"]
            if not allowed:
                result = self.record(op, "denied", "service_control_precondition", terminal=True, observed=before)
                return result
            self.verify(cast(Record, op["deployment"]))
            self.record(op, "dispatch")
            if op["action"] == "start":
                self.files.reserve(raw)
                await self.native.start(cast(Record, op["deployment"]))
            else:
                await self.boot.drain(op)
            deadline = self.clock.monotonic() + op["wait_seconds"]
            while True:
                observed = await self.goal(op)
                if observed is not None:
                    result = self.record(op, "succeeded", terminal=True, observed=observed)
                    return result
                if self.clock.monotonic() >= deadline:
                    return self.record(op, "unknown", "service_control_outcome_unverified")
                await asyncio.sleep(0.25)
        except (BusinessError, OSError, ValueError, KeyError, TypeError) as exc:
            if self.files.dispatched(raw):
                return self.record(
                    op, "unknown", exc.code if isinstance(exc, BusinessError) else "service_control_interrupted"
                )
            if self.files.latest(raw) is not None:
                result = self.record(
                    op,
                    "failed",
                    exc.code if isinstance(exc, BusinessError) else "service_control_interrupted",
                    terminal=True,
                )
                return result
            raise
        finally:
            native_closed = await self.native.close(6)
            boot_closed = await self.boot.close(6)
            if native_closed and boot_closed:
                self._settle()
                self._release()

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
        native = await self.native.close(max(0, deadline - asyncio.get_running_loop().time()))
        boot = await self.boot.close(max(0, deadline - asyncio.get_running_loop().time()))
        if not native or not boot:
            return False
        self._settle()
        self._release()
        return True

