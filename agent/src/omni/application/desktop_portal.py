"""Owner/view-scoped temporary share sessions with durable consent metadata; no image, native identity or input authorization."""

from __future__ import annotations

import asyncio
from collections.abc import Awaitable, Callable
from dataclasses import dataclass

from ..domain.contracts import BusinessError
from ..domain.desktop_portal import TERMINAL, backend_status, capture_request, owner, uuid_value
from .objects import ObjectService
from .ports import DatabasePort, DesktopPortalPort, Ids, Observer


@dataclass
class Grant:
    scope: dict
    phase: str = "starting"
    error_code: str | None = None
    task: asyncio.Task | None = None
    authorized: bool = False


class DesktopPortalService:
    def __init__(
        self,
        database: DatabasePort,
        backend: DesktopPortalPort,
        ids: Ids,
        boot_id: str,
        observer: Observer,
        objects: ObjectService,
        *,
        authorize: Callable[[str, str], Awaitable[None]],
    ) -> None:
        self._db, self._backend, self._ids, self._boot, self._observer = database, backend, ids, boot_id, observer
        self._authorize = authorize
        self._grants: dict[str, Grant] = {}
        self._pending: set[asyncio.Task] = set()
        self._opening: set[str] = set()
        self._closing = False
        self._objects = objects
        self._capturing: set[str] = set()

    async def _audit(self, grant: Grant, kind: str, **data) -> None:
        try:
            await self._db.write(
                lambda u: u.diagnostic_audit(
                    grant.scope["agent_id"],
                    grant.scope["grant_id"],
                    kind,
                    {"window_only": True, "input_authorized": False, **data},
                )
            )
        except Exception:  # noqa: BLE001 -- a failed business audit never becomes a usable grant or leaks SQL/provider data
            raise BusinessError(
                "portal_audit_failed", "Window-share audit could not be persisted", status=503
            ) from None

    def _public(self, grant: Grant, native: dict) -> dict:
        native = backend_status(native, grant.scope["grant_id"])
        phase = grant.phase if native["phase"] == "active" or grant.phase in TERMINAL else native["phase"]
        return {
            "grant_id": grant.scope["grant_id"],
            "view_id": grant.scope["view_id"],
            "phase": phase,
            "error_code": grant.error_code or native["error_code"],
            "authorized": grant.authorized
            and phase == "active"
            and native["phase"] == "active"
            and not native["cleanup_pending"],
            "capture_available": grant.authorized
            and phase == "active"
            and native["capture_available"]
            and not native["cleanup_pending"],
            "capture_error_code": native["capture_error_code"]
            or (None if grant.authorized and phase == "active" else "portal_consent_pending"),
            "input_authorized": False,
            "cleanup_pending": native["cleanup_pending"],
            "clean": native["clean"],
        }

    async def _guard(self, grant: Grant) -> None:
        if self._closing or self._grants.get(grant.scope["agent_id"]) is not grant:
            raise BusinessError("portal_grant_changed", "Window-share grant was detached", status=409)
        await self._authorize(grant.scope["agent_id"], grant.scope["view_id"])
        if self._closing or self._grants.get(grant.scope["agent_id"]) is not grant:
            raise BusinessError("portal_grant_changed", "Window-share grant was detached", status=409)

    async def open(self, agent_id: str, data: object) -> dict:
        view_id = owner(data)
        if self._closing:
            raise BusinessError("portal_draining", "Window sharing is draining", status=503)
        if agent_id in self._opening:
            raise BusinessError("portal_busy", "Window sharing is serialized per Agent", status=409)
        await self._authorize(agent_id, view_id)
        if self._closing or agent_id in self._opening:
            raise BusinessError("portal_busy", "Window sharing admission changed", status=409)
        if agent_id not in self._grants and len(set(self._grants) | self._opening) >= 8:
            raise BusinessError("portal_busy", "Window-share views are bounded", status=503)
        self._opening.add(agent_id)
        task = asyncio.create_task(self._open(agent_id, view_id), name="portal-admission")
        self._own(task)
        return await asyncio.shield(task)

    def _own(self, task: asyncio.Task) -> None:
        self._pending.add(task)

        def done(future):
            self._pending.discard(future)
            if not future.cancelled():
                future.exception()

        task.add_done_callback(done)

    async def _open(self, agent_id: str, view_id: str) -> dict:
        previous = self._grants.get(agent_id)
        try:
            if previous is not None:
                if not await self._backend.stop(previous.scope):
                    raise BusinessError(
                        "portal_cleanup_pending", "Previous window-share helper is still owned", status=503
                    )
                if previous.task is not None:
                    await asyncio.shield(previous.task)
            grant = Grant(
                {"agent_id": agent_id, "view_id": view_id, "boot_id": self._boot, "grant_id": self._ids.new()}
            )
            self._grants[agent_id] = grant
            with self._observer.context(agent_id=agent_id, operation_id=grant.scope["grant_id"]):
                try:
                    await self._guard(grant)
                    await self._audit(grant, "desktop.portal_requested")
                    native = backend_status(
                        await self._backend.open(grant.scope, before_dispatch=lambda: self._guard(grant)),
                        grant.scope["grant_id"],
                    )
                    await self._guard(grant)
                except BaseException:
                    try:
                        await self._backend.stop(grant.scope)
                    except BusinessError:
                        pass  # admission may have failed before any helper existed
                    grant.phase = "failed"
                    grant.error_code = "portal_open_failed"
                    try:
                        if not (await self._backend.status(grant.scope))["cleanup_pending"]:
                            self._grants.pop(agent_id, None)
                    except BusinessError:
                        self._grants.pop(agent_id, None)
                    await self._audit(grant, "desktop.portal_closed", error_code=grant.error_code)
                    raise
                grant.phase = native["phase"]
                grant.task = asyncio.create_task(self._watch(grant), name="portal-consent-settlement")
                self._own(grant.task)
                return self._public(grant, native)
        finally:
            self._opening.discard(agent_id)

    async def _watch(self, grant: Grant) -> None:
        try:
            value = backend_status(await self._backend.status(grant.scope), grant.scope["grant_id"])
            while True:
                if value["phase"] == "active" and not grant.authorized:
                    await self._guard(grant)
                    await self._audit(grant, "desktop.portal_authorized")
                    await self._guard(grant)
                    grant.authorized = True
                grant.phase, grant.error_code = value["phase"], value["error_code"]
                if value["phase"] in TERMINAL and not value["cleanup_pending"]:
                    break
                value = backend_status(
                    await self._backend.wait(grant.scope, value["revision"]), grant.scope["grant_id"]
                )
        except Exception as caught:  # noqa: BLE001 -- failed audit or revoked view cannot expose a usable consent
            grant.phase = "failed"
            grant.error_code = (
                caught.code
                if isinstance(caught, BusinessError)
                and caught.code in {"portal_audit_failed", "portal_grant_changed", "desktop_view_changed"}
                else "portal_settlement_failed"
            )
            grant.authorized = False
            await self._backend.stop(grant.scope)
        finally:
            grant.authorized = False
            await self._audit(grant, "desktop.portal_closed", error_code=grant.error_code, phase=grant.phase)

    async def status(self, agent_id: str, view_id: str) -> dict:
        view_id = uuid_value(view_id)
        await self._authorize(agent_id, view_id)
        grant = self._grants.get(agent_id)
        if grant is None or grant.scope["view_id"] != view_id:
            return {"grant": None, "capture_available": False, "input_authorized": False}
        public = self._public(grant, await self._backend.status(grant.scope))
        return {
            "grant": public,
            "capture_available": public["capture_available"],
            "input_authorized": False,
        }

    async def detach(self, agent_id: str, view_id: str) -> None:
        grant = self._grants.get(agent_id)
        if grant is None or grant.scope["view_id"] != view_id:
            return
        grant.authorized = False
        if not await self._backend.stop(grant.scope):
            raise BusinessError("portal_cleanup_pending", "Window-share helper is still owned", status=503)
        if grant.task is not None:
            await asyncio.shield(grant.task)
        if self._grants.get(agent_id) is grant:
            self._grants.pop(agent_id, None)

    async def close_grant(self, agent_id: str, view_id: str, grant_id: str) -> dict:
        await self._authorize(agent_id, uuid_value(view_id))
        grant = self._grants.get(agent_id)
        if grant is None or grant.scope["view_id"] != view_id or grant.scope["grant_id"] != uuid_value(grant_id):
            raise BusinessError("portal_grant_changed", "Window-share grant changed", status=409)
        await self.detach(agent_id, view_id)
        return {"closed": True, "grant_id": grant_id}

    async def capture(self, agent_id: str, data: object) -> dict:
        request = capture_request(data)
        await self._authorize(agent_id, request["view_id"])
        grant = self._grants.get(agent_id)
        if (
            grant is None
            or grant.scope["view_id"] != request["view_id"]
            or grant.scope["grant_id"] != request["grant_id"]
        ):
            raise BusinessError("portal_grant_changed", "Window-share grant changed", status=409)
        if agent_id in self._capturing or len(self._capturing) >= 4 or self._closing:
            raise BusinessError("portal_frame_busy", "Window frame requests are bounded", status=503)
        self._capturing.add(agent_id)
        task = asyncio.create_task(self._capture(grant, request["session_id"]), name="portal-frame-publication")
        self._own(task)
        return await asyncio.shield(task)

    async def _capture(self, grant: Grant, session_id: str) -> dict:
        agent_id = grant.scope["agent_id"]
        operation_id = self._ids.new()
        try:
            epoch = await self._db.read(lambda u: u.capture_session(agent_id, session_id))

            async def guard():
                await self._guard(grant)
                native = backend_status(await self._backend.status(grant.scope), grant.scope["grant_id"])
                if (
                    not grant.authorized
                    or native["phase"] != "active"
                    or native["cleanup_pending"]
                    or not native["capture_available"]
                ):
                    raise BusinessError(
                        native["capture_error_code"] or "portal_revoked", "Window frame is not authorized", status=409
                    )
                if await self._db.read(lambda u: u.capture_session(agent_id, session_id)) != epoch:
                    raise BusinessError(
                        "context_invalidated", "Conversation content changed during capture", status=409
                    )
                await self._guard(grant)

            with self._observer.context(agent_id=agent_id, session_id=session_id, operation_id=operation_id):
                await guard()
                await self._audit(grant, "desktop.portal_capture_requested", capture_operation_id=operation_id)
                frame = await self._backend.capture(grant.scope, before_dispatch=guard)
                await guard()
                result = await self._objects.publish_capture(
                    agent_id, session_id, epoch, operation_id, frame, before_publish=guard
                )
                await guard()
                return {
                    **result,
                    "view_id": grant.scope["view_id"],
                    "grant_id": grant.scope["grant_id"],
                    "observation_only": True,
                    "attached_to_model": False,
                }
        finally:
            self._capturing.discard(agent_id)

    def health(self) -> dict:
        return {
            "pending": len(self._pending),
            "grants": len(self._grants),
            "closing": self._closing,
            "backend": self._backend.health(),
        }

    def stop_accepting(self) -> None:
        self._closing = True
        for grant in self._grants.values():
            grant.authorized = False
        self._backend.stop_accepting()

    async def close(self, timeout_s: float) -> bool:
        self.stop_accepting()
        # A failed native reclaim can leave a settlement watcher waiting for a
        # final revision after its view was already detached or evicted. Retry
        # the retained actual owner before waiting for that watcher; phase 1
        # cannot run until these phase-0 audit obligations have settled.
        for grant in list(self._grants.values()):
            try:
                if not await self._backend.stop(grant.scope):
                    return False
            except BusinessError as error:
                if error.code != "portal_grant_changed":
                    raise
        if self._pending:
            _, pending = await asyncio.wait(self._pending, timeout=max(0, timeout_s))
            if pending:
                return False
        self._grants.clear()
        return True
