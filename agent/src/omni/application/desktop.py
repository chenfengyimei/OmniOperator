"""Owner desktop observations with short-lived scoped identities; never authorizes or dispatches desktop writes."""

from __future__ import annotations

import asyncio
import copy
from collections import OrderedDict
from collections.abc import Awaitable, Callable, Mapping
from dataclasses import dataclass, field
from typing import Any, TypeVar, cast

from ..domain.contracts import JSON, BusinessError
from ..domain.desktop import (
    parse_owner,
    validate_capabilities,
    validate_observation,
    validate_targets,
    validate_verification,
)
from .objects import ObjectService
from .ports import Clock, DatabasePort, DesktopBackendPort, Ids, Observer, Record

T = TypeVar("T")
VIEW_LIMIT, REQUEST_LIMIT, SNAPSHOT_SECONDS, MAX_SNAPSHOTS = 8, 8, 60, 4


@dataclass
class DesktopView:
    generation: str
    capabilities: Record | None = None
    checked_at: str | None = None
    snapshot_id: str | None = None
    expires: float = 0
    targets: dict[str, Record] = field(default_factory=lambda: dict[str, Record]())
    observation_id: str | None = None
    observed_target: str | None = None
    references: dict[str, Record] = field(default_factory=lambda: dict[str, Record]())


class DesktopService:
    def __init__(
        self,
        database: DatabasePort,
        backend: DesktopBackendPort,
        clock: Clock,
        ids: Ids,
        boot_id: str,
        observer: Observer,
        objects: ObjectService,
        *,
        on_detach: Callable[[str, str], Awaitable[None]] | None = None,
    ) -> None:
        self._db, self._backend, self._clock, self._ids, self._boot_id = database, backend, clock, ids, boot_id
        self._observer = observer
        self._objects = objects
        self._views: OrderedDict[str, DesktopView] = OrderedDict()
        self._pending: set[asyncio.Task[object]] = set()
        self._active: set[str] = set()
        self._listing: set[str] = set()
        self._closing = False
        self._on_detach = on_detach
        self._detached: list[tuple[str, str]] = []

    async def require_view(self, agent_id: str, view_id: str) -> None:
        await self._db.read(lambda u: u.diagnostic_agent(agent_id))
        view = self._views.get(agent_id)
        if view is None or view.generation != view_id:
            raise BusinessError("desktop_view_changed", "Desktop view changed", status=409)
        self._current(agent_id, view, view_id)

    async def _detach_view(self, agent_id: str, view_id: str) -> None:
        if self._on_detach is not None:
            await self._on_detach(agent_id, view_id)

    def _expire(self, view: DesktopView) -> None:
        if view.expires <= self._clock.monotonic():
            view.targets.clear()
            view.references.clear()
            view.snapshot_id = None

    def _view(self, agent_id: str) -> DesktopView:
        if agent_id not in self._views:
            for cached in self._views.values():
                self._expire(cached)
            if len(self._views) >= VIEW_LIMIT:
                victim = next(
                    (
                        key
                        for key, cached in self._views.items()
                        if key not in self._active and cached.snapshot_id is None
                    ),
                    None,
                )
                if victim is None:
                    raise BusinessError("desktop_busy", "Desktop observation capacity is exhausted", status=503)
                if self._on_detach is not None:
                    task = asyncio.create_task(
                        self._detach_view(victim, self._views[victim].generation), name="desktop-view-eviction"
                    )
                    self._pending.add(task)

                    def detached(done: asyncio.Task[object]) -> None:
                        self._pending.discard(done)
                        if not done.cancelled():
                            done.exception()

                    task.add_done_callback(lambda future: detached(cast(asyncio.Task[object], future)))
                del self._views[victim]
            self._views[agent_id] = DesktopView(self._ids.new())
        self._views.move_to_end(agent_id)
        view = self._views[agent_id]
        self._expire(view)
        return view

    async def _execute(
        self, agent_id: str, view_id: str, kind: str, operation: Callable[[DesktopView, str, str], Awaitable[T]]
    ) -> T:
        if self._closing:
            raise BusinessError("desktop_draining", "Desktop observation admission is closed", status=503)
        if agent_id in self._active or len(self._pending) >= REQUEST_LIMIT:
            raise BusinessError("desktop_busy", "Desktop observations are bounded and serialized per Agent", status=503)
        view = self._views.get(agent_id)
        if view is None or view.generation != view_id:
            raise BusinessError(
                "desktop_view_changed", "Establish this Agent's observation view before querying", status=409
            )
        self._active.add(agent_id)
        generation = view.generation
        operation_id = self._ids.new()

        async def perform() -> T:
            with (
                self._observer.context(agent_id=agent_id, operation_id=operation_id),
                self._observer.span("desktop." + kind),
            ):
                await self._db.read(lambda u: u.diagnostic_agent(agent_id))
                self._current(agent_id, view, generation)
                self._expire(view)
                return await operation(view, generation, operation_id)

        task = asyncio.create_task(perform(), name="desktop-owner-observation")
        self._pending.add(task)

        def finished(done: asyncio.Task[object]) -> None:
            self._pending.discard(done)
            self._active.discard(agent_id)
            self._listing.discard(agent_id)
            if not done.cancelled():
                done.exception()

        task.add_done_callback(lambda future: finished(cast(asyncio.Task[object], future)))
        return await asyncio.shield(task)

    def _current(self, agent_id: str, view: DesktopView, generation: str) -> None:
        if self._closing or self._views.get(agent_id) is not view or view.generation != generation:
            raise BusinessError(
                "desktop_view_changed", "Desktop observation was detached; explicitly refresh", status=409
            )

    def _dispatch_guard(
        self, agent_id: str, view: DesktopView, generation: str, snapshot_id: str | None = None
    ) -> Callable[[], Awaitable[None]]:
        async def guard() -> None:
            self._current(agent_id, view, generation)
            self._expire(view)
            if snapshot_id is not None and view.snapshot_id != snapshot_id:
                raise BusinessError(
                    "desktop_snapshot_expired", "Desktop observation expired before native dispatch", status=410
                )

        return guard

    async def status(self, agent_id: str) -> Record:
        await self._db.read(lambda u: u.diagnostic_agent(agent_id))
        view = self._views.get(agent_id)
        if view is not None:
            self._expire(view)
        return {
            "boot_id": self._boot_id,
            "owner_only": True,
            "observation_only": True,
            "model_actions": False,
            "capabilities": copy.deepcopy(view.capabilities) if view else None,
            "checked_at": view.checked_at if view else None,
            "snapshot_id": view.snapshot_id if view else None,
            "snapshot_targets": len(view.targets) if view else 0,
            "limits": {"max_targets": 50, "snapshot_seconds": SNAPSHOT_SECONDS, "max_snapshots": MAX_SNAPSHOTS},
            "runtime": self.health(),
        }

    async def begin(self, agent_id: str, data: object) -> Record:
        request = parse_owner("begin", data)
        await self._db.read(lambda u: u.diagnostic_agent(agent_id))
        if self._closing:
            raise BusinessError("desktop_draining", "Desktop observation admission is closed", status=503)
        view = self._view(agent_id)
        if view.generation != cast(str, request["view_id"]):
            previous = view.generation
            view.generation = cast(str, request["view_id"])
            view.targets.clear()
            view.snapshot_id = None
            if self._on_detach is not None:
                await self._on_detach(agent_id, previous)
        return {
            "view_id": view.generation,
            "boot_id": self._boot_id,
            "observation_only": True,
            "capabilities": copy.deepcopy(view.capabilities),
            "checked_at": view.checked_at,
            "runtime": self.health(),
        }

    async def _audit(
        self, agent_id: str, operation_id: str, operation: str, *, completed: bool = False, **metadata: Any
    ) -> None:
        await self._db.write(
            lambda u: u.diagnostic_audit(
                agent_id,
                operation_id,
                "desktop.observation_completed" if completed else "desktop.observation_requested",
                {"operation": operation, "observation_only": True, **metadata},
            )
        )

    async def probe(self, agent_id: str, data: object) -> Record:
        request = parse_owner("probe", data)

        async def perform(view: DesktopView, generation: str, operation_id: str) -> Record:
            if self._on_detach is not None:
                await self._on_detach(agent_id, generation)
            view.targets.clear()
            view.snapshot_id = None
            view.capabilities, view.checked_at = None, None
            await self._audit(agent_id, operation_id, "probe")
            self._current(agent_id, view, generation)
            report = validate_capabilities(
                await self._backend.probe(before_dispatch=self._dispatch_guard(agent_id, view, generation))
            )
            self._current(agent_id, view, generation)
            await self._audit(agent_id, operation_id, "probe", completed=True)
            self._current(agent_id, view, generation)
            view.capabilities = cast(Record, copy.deepcopy(report))
            view.checked_at = self._clock.now()
            # Re-probing permissions invalidates observations made under an earlier report.
            view.targets.clear()
            view.snapshot_id = None
            return {
                "operation_id": operation_id,
                "checked_at": view.checked_at,
                "capabilities": cast(JSON, report),
                "runtime": self.health(),
            }

        return await self._execute(agent_id, cast(str, request["view_id"]), "probe", perform)

    async def targets(self, agent_id: str, data: object) -> Record:
        request = parse_owner("targets", data)

        async def perform(view: DesktopView, generation: str, operation_id: str) -> Record:
            view.targets.clear()
            view.snapshot_id = None
            for cached in self._views.values():
                self._expire(cached)
            if (
                sum(cached.snapshot_id is not None for cached in self._views.values()) + len(self._listing)
                >= MAX_SNAPSHOTS
            ):
                raise BusinessError(
                    "desktop_snapshot_capacity",
                    "Release an earlier desktop snapshot before listing another",
                    status=503,
                )
            self._listing.add(agent_id)
            await self._audit(agent_id, operation_id, "targets")
            self._current(agent_id, view, generation)
            report = validate_targets(
                await self._backend.targets(
                    cast(int, request["limit"]), before_dispatch=self._dispatch_guard(agent_id, view, generation)
                ),
                cast(int, request["limit"]),
            )
            targets = {self._ids.new(): target for target in cast(list[Record], report["items"])}
            self._current(agent_id, view, generation)
            await self._audit(
                agent_id, operation_id, "targets", completed=True, count=len(targets), truncated=report["truncated"]
            )
            self._current(agent_id, view, generation)
            view.snapshot_id, view.expires, view.targets = (
                self._ids.new(),
                self._clock.monotonic() + SNAPSHOT_SECONDS,
                targets,
            )
            return {
                "operation_id": operation_id,
                "snapshot_id": view.snapshot_id,
                "boot_id": self._boot_id,
                "expires_in_s": SNAPSHOT_SECONDS,
                "observation_only": True,
                "truncated": cast(JSON, report["truncated"]),
                "items": [
                    {
                        "target_id": identity,
                        "application": target["application_id"],
                        "pid": target["pid"],
                        "backend": target["backend"],
                        "can_observe": target["backend"] != "x11",
                        "can_capture": target["backend"] != "atspi",
                    }
                    for identity, target in targets.items()
                ],
            }

        return await self._execute(agent_id, cast(str, request["view_id"]), "targets", perform)

    async def verify(self, agent_id: str, data: object) -> Record:
        request = parse_owner("verify", data)

        async def perform(view: DesktopView, generation: str, operation_id: str) -> Record:
            target = view.targets.get(cast(str, request["target_id"]))
            if view.snapshot_id != cast(str, request["snapshot_id"]) or target is None:
                raise BusinessError("desktop_snapshot_expired", "Refresh this Agent's desktop observations", status=410)
            await self._audit(agent_id, operation_id, "verify")
            self._current(agent_id, view, generation)
            self._expire(view)
            if view.snapshot_id != cast(str, request["snapshot_id"]):
                raise BusinessError(
                    "desktop_snapshot_expired", "Desktop observation expired before verification", status=410
                )
            report = validate_verification(
                await self._backend.verify(
                    copy.deepcopy(target),
                    before_dispatch=self._dispatch_guard(agent_id, view, generation, cast(str, request["snapshot_id"])),
                ),
                target,
            )
            self._current(agent_id, view, generation)
            self._expire(view)
            if view.snapshot_id != cast(str, request["snapshot_id"]):
                raise BusinessError(
                    "desktop_snapshot_expired", "Desktop observation expired during verification", status=410
                )
            await self._audit(agent_id, operation_id, "verify", completed=True, matches=report["matches"])
            self._current(agent_id, view, generation)
            self._expire(view)
            if view.snapshot_id != cast(str, request["snapshot_id"]):
                raise BusinessError(
                    "desktop_snapshot_expired", "Desktop observation expired during settlement", status=410
                )
            if not report["matches"]:
                view.targets.pop(cast(str, request["target_id"]), None)
            return {
                "operation_id": operation_id,
                "target_id": cast(str, request["target_id"]),
                "matches": cast(JSON, report["matches"]),
                "reason": cast(JSON, report["reason"]),
                "verified_at": self._clock.now(),
                "observation_only": True,
            }

        return await self._execute(agent_id, cast(str, request["view_id"]), "verify", perform)

    async def observe(self, agent_id: str, data: object) -> Record:
        request = parse_owner("observe", data)

        async def perform(view: DesktopView, generation: str, operation_id: str) -> Record:
            target = view.targets.get(cast(str, request["target_id"]))
            if view.snapshot_id != cast(str, request["snapshot_id"]) or target is None:
                raise BusinessError("desktop_snapshot_expired", "Refresh this Agent's desktop observations", status=410)
            guard = self._dispatch_guard(agent_id, view, generation, cast(str, request["snapshot_id"]))
            await self._audit(agent_id, operation_id, "observe")
            await guard()
            try:
                report = validate_observation(
                    await self._backend.observe(copy.deepcopy(target), before_dispatch=guard), target
                )
            except BusinessError as error:
                if error.code in {"desktop_target_changed", "desktop_permission_denied"}:
                    view.targets.pop(cast(str, request["target_id"]), None)
                raise
            await guard()
            await self._audit(
                agent_id,
                operation_id,
                "observe",
                completed=True,
                count=len(cast(list[Record], report["nodes"])),
                truncated=report["truncated"],
            )
            await guard()
            # UI text is returned once, never cached, audited or turned into an action lease.
            identities = {str(node["id"]): self._ids.new() for node in cast(list[Record], report["nodes"])}
            view.observation_id, view.observed_target = self._ids.new(), cast(str, request["target_id"])
            view.references = {
                identities[str(node["id"])]: {"reference": node["reference"], "actions": node["actions"]}
                for node in cast(list[Record], report["nodes"])
                if node.get("reference") and node.get("actions")
            }
            nodes: list[JSON] = [
                {
                    **{k: v for k, v in node.items() if k not in {"id", "parent", "reference"}},
                    "node_id": identities[str(node["id"])],
                    "parent_id": identities.get(str(node["parent"])),
                }
                for node in cast(list[Record], report["nodes"])
            ]
            return {
                "operation_id": operation_id,
                "observation_id": view.observation_id,
                "target_id": cast(str, request["target_id"]),
                "snapshot_id": cast(str, request["snapshot_id"]),
                "observed_at": self._clock.now(),
                "observation_only": True,
                "expires_in_s": max(0, view.expires - self._clock.monotonic()),
                "nodes": nodes,
                "truncated": cast(JSON, report["truncated"]),
                **(
                    {"coordinate_space": cast(JSON, report["coordinate_space"])} if "coordinate_space" in report else {}
                ),
            }

        return await self._execute(agent_id, cast(str, request["view_id"]), "observe", perform)

    def action_binding(self, agent_id: str, request: Mapping[str, object]) -> dict[str, object]:
        view = self._views.get(agent_id)
        if view is None:
            raise BusinessError("desktop_view_changed", "Desktop view is no longer available")
        self._current(agent_id, view, cast(str, request["view_id"]))
        self._expire(view)
        node = view.references.get(cast(str, request["node_id"]))
        target = view.targets.get(cast(str, request["target_id"]))
        if (
            view.snapshot_id != cast(str, request["snapshot_id"])
            or view.observation_id != cast(str, request["observation_id"])
            or view.observed_target != cast(str, request["target_id"])
            or node is None
            or target is None
        ):
            raise BusinessError(
                "desktop_observation_expired", "Refresh the window observation before proposing an action", status=410
            )
        if cast(str, request["kind"]) not in cast(list[JSON], node["actions"]):
            raise BusinessError("desktop_pattern_unavailable", "Observed control does not support this semantic action")
        return {"target": copy.deepcopy(target), "node": copy.deepcopy(node["reference"]), "expires": view.expires}

    def consume_observation(self, agent_id: str, observation_id: str) -> None:
        view = self._views.get(agent_id)
        if view and view.observation_id == observation_id:
            view.references.clear()

    async def capture(self, agent_id: str, data: object) -> Record:
        request = parse_owner("capture", data)

        async def perform(view: DesktopView, generation: str, operation_id: str) -> Record:
            target = view.targets.get(cast(str, request["target_id"]))
            if view.snapshot_id != cast(str, request["snapshot_id"]) or target is None:
                raise BusinessError("desktop_snapshot_expired", "Refresh this Agent's desktop observations", status=410)
            epoch = await self._db.read(lambda u: u.capture_session(agent_id, cast(str, request["session_id"])))
            view_guard = self._dispatch_guard(agent_id, view, generation, cast(str, request["snapshot_id"]))

            async def guard() -> None:
                await view_guard()
                current = await self._db.read(lambda u: u.capture_session(agent_id, cast(str, request["session_id"])))
                if current != epoch:
                    raise BusinessError(
                        "context_invalidated", "Conversation content changed during capture", status=409
                    )
                await view_guard()

            await self._audit(agent_id, operation_id, "capture")
            await guard()
            try:
                frame = await self._backend.capture(copy.deepcopy(target), before_dispatch=guard)
            except BusinessError as error:
                if error.code in {"desktop_target_changed", "desktop_permission_denied"}:
                    view.targets.pop(cast(str, request["target_id"]), None)
                raise
            await guard()
            result = await self._objects.publish_capture(
                agent_id, cast(str, request["session_id"]), epoch, operation_id, frame, before_publish=guard
            )
            # A committed artifact stays visible in session history even if the view is
            # subsequently detached. A late HTTP response never grants a fresh preview.
            await guard()
            return {**result, "observation_only": True, "attached_to_model": False}

        return await self._execute(agent_id, cast(str, request["view_id"]), "capture", perform)

    async def forget(self, agent_id: str, view_id: str) -> Record:
        await self._db.read(lambda u: u.diagnostic_agent(agent_id))
        view = self._views.get(agent_id)
        matches = view is not None and view.generation == view_id
        if view is not None and matches:
            view.generation = self._ids.new()
            view.targets.clear()
            view.references.clear()
            view.snapshot_id = None
            if self._on_detach is not None:
                await self._on_detach(agent_id, view_id)
        return {"forgotten": matches, "observation_only": True}

    def health(self) -> Record:
        return {
            "pending": len(self._pending),
            "cached_views": len(self._views),
            "closing": self._closing,
            "backend": self._backend.health(),
        }

    def stop_accepting(self) -> None:
        if self._closing:
            return
        self._closing = True
        self._detached = [(agent_id, view.generation) for agent_id, view in self._views.items()]
        for view in self._views.values():
            view.generation = self._ids.new()
            view.targets.clear()
            view.references.clear()
            view.snapshot_id = None

    async def close(self, timeout_s: float) -> bool:
        self.stop_accepting()
        if self._pending:
            _, pending = await asyncio.wait(self._pending, timeout=max(0, timeout_s))
            if pending:
                return False
        if self._on_detach is not None:
            for agent_id, view_id in self._detached:
                await self._on_detach(agent_id, view_id)
        self._detached.clear()
        self._views.clear()
        return True
