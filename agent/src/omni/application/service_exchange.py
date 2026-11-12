"""Ordered service exchange ownership; interrupted native steps are observed, never automatically replayed."""

from __future__ import annotations

import asyncio
from contextlib import AbstractContextManager
from typing import cast

from ..domain.contracts import JSON, BusinessError
from ..domain.service_control import make_control
from ..domain.service_exchange import (
    ADMIN_PROTOCOL,
    ExchangePlan,
    checked_proof,
    make_exchange,
    progress,
    stages,
    validate_exchange,
)
from ..domain.service_operations import make_operation
from .ports import Clock, ExchangeFilesPort, ExchangeStepsPort, Ids, Record


class ServiceExchange:
    def __init__(self, files: ExchangeFilesPort, steps: ExchangeStepsPort, clock: Clock, ids: Ids) -> None:
        self.files, self.steps, self.clock, self.ids = files, steps, clock, ids
        self._pending: asyncio.Task[Record] | None = None
        self._guard: AbstractContextManager[None] | None = None

    def plan(self, release: object, journal: str, output: str, administrators: Record | None = None) -> Record:
        from ..domain.releases import validate_release

        checked = validate_release(release)
        self.steps.prepared(checked)
        at = self.clock.now()
        participants: dict[str, dict[str, str]] | None = None
        if administrators is not None:
            from ..domain.service_deployment import deployment_fields, deployment_sha

            roles = deployment_fields(administrators, {"source", "target"})
            participants = {}
            for role in ("source", "target"):
                row = deployment_fields(roles[role], {"protocol", "bundle_sha256"})
                if row["protocol"] != ADMIN_PROTOCOL:
                    raise BusinessError(
                        "service_exchange_admin_protocol_unsupported",
                        "Use the explicitly supported administrator protocol",
                    )
                participants[role] = {
                    "id": self.ids.new(),
                    "protocol": ADMIN_PROTOCOL,
                    "bundle_sha256": deployment_sha(row["bundle_sha256"]),
                }
        plan = make_exchange(
            checked,
            make_operation(checked["source"], "uninstall", self.ids.new(), at),
            make_operation(checked["target"], "install", self.ids.new(), at),
            make_control(checked["target"], "start", self.ids.new(), at, self.ids.new()),
            journal,
            self.ids.new(),
            at,
            participants,
        )
        return self.files.publish(plan, output)

    def inspect(self, plan: ExchangePlan) -> Record:
        checked = validate_exchange(plan)
        history = self.files.history(checked)
        count, active, state = progress(checked, history)
        recovery = checked["release"]["recovery"]
        return {
            "exchange_id": checked["id"],
            "sha256": checked["sha256"],
            "state": state or "not_started",
            "completed_stages": list(stages(checked)[:count]),
            "next_stage": stages(checked)[count] if count < len(stages(checked)) else "",
            "active_stage": active,
            "events": [cast(JSON, row) for row in history],
            "ready": False,
            "historically_ready": state == "finished",
            "automatic_rollback": False,
            "stops_service": checked["stops_service"],
            "releases_activation_on_abandon": checked["releases_activation_on_abandon"],
            "predecessor_cleanup_pending": recovery is not None and recovery["isolation"] is not None,
            "predecessor_cleanup_status": "not_observed"
            if recovery is not None and recovery["isolation"] is not None
            else "not_applicable",
        }

    async def apply(self, value: object, approval: str, *, resume: bool = False, abandon: bool = False) -> Record:
        plan = validate_exchange(value)
        if approval != plan["sha256"]:
            raise BusinessError("service_exchange_not_approved", "Approve the full exchange fingerprint")
        if self._pending is not None or self._guard is not None:
            raise BusinessError("service_exchange_busy", "The previous exchange still owns actual work")
        self._pending = asyncio.create_task(self._run(plan, resume, abandon), name="service-exchange")
        self._pending.add_done_callback(lambda task: task.exception() if not task.cancelled() else None)
        return await asyncio.shield(self._pending)

    def _append(self, plan: ExchangePlan, state: str, stage: str = "", proof: Record | None = None) -> None:
        self.files.append(
            plan,
            {
                "state": state,
                "stage": stage,
                "recorded_at": self.clock.now(),
                "proof": proof or {},
            },
        )

    async def _run(self, plan: ExchangePlan, resume: bool, abandon: bool) -> Record:
        guard = self.files.exclusive(plan)
        guard.__enter__()
        self._guard = guard
        try:
            history = self.files.history(plan)
            count, active, state = progress(plan, history)
            if state in {"isolating", "isolated"}:
                raise BusinessError(
                    "service_exchange_isolated", "Preserve the quarantined exchange; use a separate rescue plan"
                )
            if state in {"finished", "abandoned"}:
                self.files.settled(plan)  # Complete a crash after the final witness, before marker removal.
                return self.inspect(plan)
            if not history:
                if abandon:
                    raise BusinessError("service_exchange_not_started", "There is no exchange intent to resume")
                self.steps.prepared(plan["release"])
            elif not resume and not abandon:
                raise BusinessError("service_exchange_needs_resume", "Resume by observing the interrupted stage")
            self.files.claim(plan)
            if not history:
                self._append(plan, "intent")
            if abandon:
                # This only releases the reservation. It neither rolls back a selection nor undoes external effects.
                self.files.check_idle(plan)
                self._append(plan, "abandoned")
                self.files.settled(plan)
                return self.inspect(plan)
            ordered = stages(plan)
            for index in range(count, len(ordered)):
                stage = ordered[index]
                reconcile = active == stage
                if not await self.steps.admit(plan, stage, reconcile=reconcile):
                    current = self.files.history(plan)
                    if not reconcile and (
                        current[-1]["state"] != "awaiting_administrator" or current[-1]["stage"] != stage
                    ):
                        self._append(plan, "awaiting_administrator", stage)
                    return {**self.inspect(plan), "administrator_available": False}
                if not reconcile:
                    self._append(plan, "dispatch", stage)
                try:
                    proof = checked_proof(plan, stage, await self.steps.perform(plan, stage, reconcile=reconcile))
                except (BusinessError, OSError, ValueError) as exc:
                    from ..domain.service_exchange import stage_identity

                    identity, sha = stage_identity(plan, stage)
                    proof = {
                        "stage": stage,
                        "operation_id": identity,
                        "operation_sha256": sha,
                        "succeeded": False,
                        "cleanup_complete": False,
                        "error_code": exc.code if isinstance(exc, BusinessError) else "service_exchange_interrupted",
                        "verification": "",
                    }
                if proof["succeeded"] is not True or proof["cleanup_complete"] is not True:
                    self._append(plan, "blocked", stage, cast(Record, proof))
                    return self.inspect(plan)
                self._append(plan, "complete", stage, cast(Record, proof))
                active = ""
            self._append(plan, "finished")
            self.files.settled(plan)
            return {**self.inspect(plan), "ready_at_completion": True}
        finally:
            if await self.steps.close(8):
                self._release()

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
        if not await self.steps.close(max(0, deadline - asyncio.get_running_loop().time())):
            return False
        self._release()
        return True
