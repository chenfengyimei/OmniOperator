"""Offline release preparation and single-directory selection; never elevates, registers or starts a service."""

from __future__ import annotations

import asyncio
from contextlib import AbstractContextManager
from typing import cast

from ..domain.contracts import BusinessError
from ..domain.recovery_authority import RecoverySpec
from ..domain.releases import (
    ReleaseEvent,
    ReleasePlan,
    ReleasePrepared,
    ReleaseSnapshot,
    make_release,
    validate_release,
)
from ..domain.service_control import make_control
from ..domain.service_deployment import make_plan, validate_plan
from .ports import (
    Clock,
    DeploymentFilesPort,
    Ids,
    Record,
    ReleaseDataPort,
    ReleaseDrainPort,
    ReleaseFilesPort,
    ServiceRegistrationPort,
)


class Releases:
    def __init__(
        self,
        deployments: DeploymentFilesPort,
        files: ReleaseFilesPort,
        data: ReleaseDataPort,
        manager: ServiceRegistrationPort,
        clock: Clock,
        ids: Ids,
        drain: ReleaseDrainPort | None = None,
    ) -> None:
        self.deployments, self.files, self.data, self.manager, self.clock, self.ids = (
            deployments,
            files,
            data,
            manager,
            clock,
            ids,
        )
        self._pending: asyncio.Task[Record] | None = None
        self._guard: AbstractContextManager[None] | None = None
        self.drain = drain

    def draft(self, source: Record, request: Record, recovery: RecoverySpec | None = None) -> ReleasePlan:
        if set(request) != {"target", "backup", "journal"}:
            raise BusinessError("release_request_invalid", "Supply target service request, new backup and new journal")
        reviewed = validate_plan(source)
        target = make_plan(
            request["target"],
            self.deployments.capture(),
            self.ids.new(),
            self.clock.now(),
            isolated_registration=reviewed.get("isolated_registration") is True
            or (recovery is not None and recovery["isolation"] is not None),
        )
        source_runtime = cast(Record, reviewed["runtime"])
        target_runtime = cast(Record, target["runtime"])
        plan = make_release(
            reviewed,
            target,
            {key: request[key] for key in ("backup", "journal")},
            self.ids.new(),
            self.clock.now(),
            recovery,
            migration=self.data.schema_upgrade(source_runtime, target_runtime)
            if recovery is None and source_runtime["schema_head"] != target_runtime["schema_head"]
            else None,
        )
        self.files.installations(plan)
        return plan

    def plan(self, source: Record, request: Record, output: str, recovery: RecoverySpec | None = None) -> Record:
        return self.files.publish(self.draft(source, request, recovery), output)

    async def plan_online(self, source: Record, request: Record, output: str) -> Record:
        plan = self.draft(source, request)
        if self.drain is None or plan["source"]["request"]["target"] not in {"windows-user", "linux-user"}:
            raise BusinessError(
                "release_drain_unavailable", "Online preparation requires an explicit user-service drain port"
            )
        identity = await self.drain.probe(source)
        if identity.get("error_code") is not None or not isinstance(identity.get("boot_id"), str):
            raise BusinessError(
                "release_source_boot_unverified", "Prove the exact source boot before reviewing its shutdown"
            )
        stop = make_control(source, "stop", self.ids.new(), self.clock.now(), str(identity["boot_id"]))
        online = make_release(
            source,
            plan["target"],
            plan["paths"],
            plan["id"],
            plan["created_at"],
            source_stop=stop,
            migration=plan["schema_upgrade"],
        )
        self.files.installations(online)
        return self.files.publish(online, output)

    @staticmethod
    def approved(plan: ReleasePlan, approval: str) -> ReleasePlan:
        checked = validate_release(plan)
        if approval != checked["sha256"]:
            raise BusinessError("release_not_approved", "Approve the exact release fingerprint")
        return checked

    def inspect(self, plan: ReleasePlan) -> Record:
        checked = validate_release(plan)
        history = self.files.history(checked)
        return {
            "release_id": checked["id"],
            "sha256": checked["sha256"],
            "state": history[-1]["phase"] if history else "not_started",
            "events": [cast(Record, dict(row)) for row in history],
            "starts_service": False,
            "stops_service": checked["stops_service"],
            "native_registration_verified": False,
            "ready": False,
            "source_schema": checked["source"]["runtime"]["schema_head"],
            "target_schema": checked["target"]["runtime"]["schema_head"],
            "upgrades_schema": checked["schema_upgrade"] is not None,
            "restores_schema_from_snapshot": checked["recovery"] is not None
            and checked["source"]["runtime"]["schema_head"] != checked["target"]["runtime"]["schema_head"],
            "restore_policy": checked["recovery"]["restore_policy"] if checked["recovery"] is not None else None,
            "predecessor_cleanup_pending": checked["recovery"] is not None
            and checked["recovery"]["isolation"] is not None,
        }

    def _append(self, plan: ReleasePlan, phase: str, data: Record | ReleaseSnapshot | ReleasePrepared) -> None:
        self.files.append(plan, phase, cast(Record, dict(data)), self.clock.now())

    def prepared(self, history: list[ReleaseEvent]) -> ReleasePrepared:
        values = [row["data"] for row in history if row["phase"] == "prepared"]
        if len(values) != 1 or not isinstance(cast(object, values[0]), dict):
            raise BusinessError("release_not_prepared", "A complete prepared release is required")
        return cast(ReleasePrepared, values[0])

    def prepare(self, plan: ReleasePlan, approval: str) -> Record:
        checked = self.approved(plan, approval)
        self.files.installations(checked)
        history = self.files.history(checked)
        phase = history[-1]["phase"] if history else ""
        if checked["source_stop"] is not None and phase not in {"drained", "prepared"}:
            raise BusinessError("release_requires_drain", "Use prepare-online to prove the reviewed source boot closed")
        if history and phase != "drained":
            if phase != "prepared":
                raise BusinessError(
                    "release_interrupted", "Preserve partial work; inspect or abandon before a new plan"
                )
            with self.files.exclusive(checked):
                self.files.require_fence(checked, "target")
                self.data.verify(checked, self.prepared(history))
            return self.inspect(checked)
        with self.files.exclusive(checked, create_target=True):
            self.data.source(checked)
            if not history:
                self.files.begin(checked)
            self._append(checked, "intent", {})
            snapshot = self.data.snapshot(checked)
            self._append(checked, "snapshot", snapshot)
            restored = self.data.restore(checked, snapshot)
            self._append(checked, "restored", {"execution_quarantined": True, "credentials_restored": False})
            target = checked["target"]
            self.deployments.materialize(target, target["request"]["bundle_dir"])
            verification = self.deployments.verify(target["request"]["bundle_dir"])
            if verification.get("eligible") is not True or verification.get("plan") != target:
                raise BusinessError("release_target_bundle_changed", "The staged deployment was not verified")
            self.data.verify(checked, restored)
            self._append(checked, "prepared", restored)
        return self.inspect(checked)

    async def prepare_online(self, value: object, approval: str) -> Record:
        plan = self.approved(validate_release(value), approval)
        if plan["source_stop"] is None or self.drain is None:
            raise BusinessError("release_drain_unavailable", "Review a source boot shutdown before online preparation")
        if self._pending is not None or self._guard is not None:
            raise BusinessError("release_busy", "A release operation is still owned")
        self._pending = asyncio.create_task(self._prepare_online(plan), name="release-online-preparation")
        self._pending.add_done_callback(lambda task: task.exception() if not task.cancelled() else None)
        return await asyncio.shield(self._pending)

    async def _prepare_online(self, plan: ReleasePlan) -> Record:
        assert self.drain is not None and plan["source_stop"] is not None
        self.files.installations(plan)
        guard = self.files.preparation(plan)
        guard.__enter__()
        self._guard = guard
        try:
            history = self.files.history(plan)
            phase = history[-1]["phase"] if history else ""
            if phase in {"prepared", "drained"}:
                return self.prepare(plan, plan["sha256"])
            if phase not in {"", "drain_intent", "drain_unknown"}:
                raise BusinessError("release_interrupted", "Preserve partial preparation and inspect its journal")
            if len(history) >= 24:
                raise BusinessError("release_journal_full", "Reserve room for preparation and selection witnesses")
            if not history:
                self.files.begin(plan)
                self._append(
                    plan,
                    "drain_intent",
                    {"operation_id": plan["source_stop"]["id"], "operation_sha256": plan["source_stop"]["sha256"]},
                )
            try:
                result = await self.drain.stop(plan["source_stop"], reconcile=bool(history))
                closed = await self.drain.close(8)
                success = (
                    result.get("state") == "succeeded"
                    and result.get("boot_id") == plan["source_stop"]["boot_id"]
                    and result.get("currently_verified", True) is True
                    and closed
                )
                code = "" if success else "release_source_drain_unresolved"
            except (BusinessError, OSError, ValueError) as exc:
                success, code = (
                    False,
                    exc.code if isinstance(exc, BusinessError) else "release_source_drain_interrupted",
                )
            self._append(
                plan,
                "drained" if success else "drain_unknown",
                {
                    "operation_id": plan["source_stop"]["id"],
                    "operation_sha256": plan["source_stop"]["sha256"],
                    "error_code": code,
                },
            )
            return self.prepare(plan, plan["sha256"]) if success else self.inspect(plan)
        finally:
            drain_closed = await self.drain.close(8)
            manager_closed = await self.manager.close(8)
            if drain_closed and manager_closed:
                self._release()

    async def select(self, plan: ReleasePlan, approval: str) -> Record:
        checked = self.approved(plan, approval)
        if self._pending is not None or self._guard is not None:
            raise BusinessError("release_busy", "A release selection is still owned")
        self._pending = asyncio.create_task(self._select(checked), name="release-selection")
        self._pending.add_done_callback(lambda task: task.exception() if not task.cancelled() else None)
        return await asyncio.shield(self._pending)

    async def _select(self, plan: ReleasePlan) -> Record:
        self.files.installations(plan)
        history = self.files.history(plan)
        phase = history[-1]["phase"] if history else "not_started"
        if phase == "selected":
            return self.inspect(plan)
        if phase not in {"prepared", "selection_intent", "selection_committed"}:
            raise BusinessError("release_not_prepared", "Select only a complete reviewed release")
        guard = self.files.exclusive(plan)
        guard.__enter__()
        self._guard = guard
        try:
            if phase == "selection_committed":
                self.files.require_fence(plan, "source")
            self.files.require_fence(plan, "target", missing_ok=phase == "selection_committed")
            self.data.verify(plan, self.prepared(history))
            target = self.deployments.verify(plan["target"]["request"]["bundle_dir"])
            if target.get("eligible") is not True or target.get("plan") != plan["target"]:
                raise BusinessError("release_target_bundle_changed", "The staged deployment changed")
            # Registration removal is a separate reviewed native operation. A
            # query never removes/stops a process and cannot authorize replay.
            isolated = plan["recovery"] is not None and plan["recovery"]["isolation"] is not None
            report = {} if isolated else await self.manager.inspect(cast(Record, plan["source"]))
            if not isolated and not (
                report.get("available") is True
                and report.get("installed") is False
                and report.get("definition_present") is False
                and report.get("state") == "stopped"
            ):
                raise BusinessError(
                    "release_source_registered", "Remove the stopped source registration before selection"
                )
            self.files.installations(plan)
            self.data.verify(plan, self.prepared(history))
            if phase == "prepared":
                self._append(plan, "selection_intent", {})
            self.files.retire_source(plan)
            if phase != "selection_committed":
                self._append(plan, "selection_committed", {"source_retired": True, "target_selected": False})
            self.files.activate_target(plan)
            self._append(
                plan,
                "selected",
                {"source_retired": True, "target_selected": True, "registration_pending": True, "ready": False},
            )
            return self.inspect(plan)
        finally:
            if await self.manager.close(6):
                self._release()

    def abandon(self, plan: ReleasePlan, approval: str) -> Record:
        checked = self.approved(plan, approval)
        with self.files.exclusive(checked):
            history = self.files.history(checked)
            if not history or any(
                row["phase"].startswith("selection_") or row["phase"] == "selected" for row in history
            ):
                raise BusinessError(
                    "release_cannot_abandon", "A selected or partially selected release requires recovery"
                )
            self.files.require_fence(checked, "target")
            if history[-1]["phase"] != "abandoned":
                self._append(checked, "abandoned", {"target_stays_fenced": True, "files_deleted": False})
        return self.inspect(checked)

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
        manager_closed = await self.manager.close(max(0, deadline - asyncio.get_running_loop().time()))
        drain_closed = self.drain is None or await self.drain.close(
            max(0, deadline - asyncio.get_running_loop().time())
        )
        if not manager_closed or not drain_closed:
            return False
        self._release()
        return True

