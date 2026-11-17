"""Owner/view-bound independent system-device consent and durable audit; never authorizes or dispatches an input action."""

from __future__ import annotations

import asyncio
from collections.abc import Awaitable, Callable
from dataclasses import dataclass, field
from typing import cast

from ..domain.contracts import BusinessError
from ..domain.desktop_control import PURPOSE, TERMINAL, backend_status, owner
from ..domain.desktop_portal import uuid_value
from .ports import DatabasePort, DesktopControlPort, Ids, Observer, Record


def error(code: str) -> BusinessError:
    return BusinessError(
        code,
        "Independent control consent is not available or remains unsettled",
        status=409 if code == "portal_control_changed" else 503,
    )


@dataclass
class Grant:
    scope: Record
    requested: int
    phase: str = "starting"
    code: str | None = None
    detached: bool = False
    forget_after_settlement: bool = False
    opening: bool = True
    requested_audit: bool = False
    system_audit: bool = False
    closed_audit: bool = False
    audit_error: bool = False
    native: Record | None = None
    watcher: asyncio.Task[None] | None = None
    settlement: asyncio.Task[bool] | None = None
    audit_lock: asyncio.Lock = field(default_factory=asyncio.Lock)


class DesktopControlService:
    def __init__(
        self,
        database: DatabasePort,
        backend: DesktopControlPort,
        ids: Ids,
        boot_id: str,
        observer: Observer,
        *,
        authorize: Callable[[str, str], Awaitable[None]],
    ) -> None:
        self._db, self._backend, self._ids, self._boot, self._observer = database, backend, ids, boot_id, observer
        self._authorize = authorize
        self._grants: dict[str, Grant] = {}
        self._opening: set[str] = set()
        self._pending: set[asyncio.Task[object]] = set()
        self._closing = False

    def _own(self, task: asyncio.Task[object]) -> None:
        self._pending.add(task)

        def done(future: asyncio.Task[object]) -> None:
            self._pending.discard(future)
            if not future.cancelled():
                future.exception()

        task.add_done_callback(done)

    async def _guard(self, grant: Grant) -> None:
        agent = cast(str, grant.scope["agent_id"])
        if self._closing or grant.detached or self._grants.get(agent) is not grant:
            raise error("portal_control_changed")
        await self._authorize(agent, cast(str, grant.scope["view_id"]))
        if self._closing or grant.detached or self._grants.get(agent) is not grant:
            raise error("portal_control_changed")

    async def _audit(self, grant: Grant, kind: str, **metadata: object) -> None:
        try:
            await self._db.write(
                lambda unit: unit.diagnostic_audit(
                    cast(str, grant.scope["agent_id"]),
                    cast(str, grant.scope["grant_id"]),
                    kind,
                    cast(
                        Record, {"purpose": PURPOSE, "devices": grant.requested, "input_authorized": False, **metadata}
                    ),
                )
            )
        except Exception:  # noqa: BLE001 -- failed business audit cannot become permission or export database/provider bodies
            grant.audit_error = True
            raise error("portal_control_audit_failed") from None

    def _validated(self, grant: Grant, value: object) -> Record:
        try:
            native = cast(Record, backend_status(value, cast(str, grant.scope["grant_id"]), grant.requested))
        except BusinessError:
            raise error("portal_control_invalid") from None
        grant.native = native
        return native

    async def _status(self, grant: Grant) -> Record:
        try:
            return self._validated(grant, await self._backend.status(grant.scope))
        except BusinessError as caught:
            if caught.code != "portal_control_changed":
                raise
            if grant.native is not None and grant.native["phase"] in TERMINAL and not grant.native["cleanup_pending"]:
                return grant.native
            if grant.native is not None:
                raise
            return self._validated(
                grant,
                {
                    "grant_id": grant.scope["grant_id"],
                    "phase": "failed",
                    "devices": grant.requested,
                    "granted_devices": 0,
                    "input_authorized": False,
                    "revision": 0,
                    "error_code": grant.code or "portal_control_open_failed",
                    "clean": True,
                    "cleanup_pending": False,
                },
            )

    def _public(self, grant: Grant, native: Record) -> Record:
        active = (
            native["phase"] == "system_authorized"
            and not grant.detached
            and not native["cleanup_pending"]
            and not grant.audit_error
        )
        return {
            "grant_id": grant.scope["grant_id"],
            "view_id": grant.scope["view_id"],
            "phase": "failed" if grant.code else native["phase"],
            "error_code": grant.code or native["error_code"],
            "devices": grant.requested,
            "granted_devices": native["granted_devices"] if active else 0,
            "system_permission_granted": bool(active),
            "system_permission_recorded": bool(active and grant.system_audit),
            "authorization_pending": bool(active and not grant.system_audit),
            "input_authorized": False,
            "input_error_code": "portal_input_guard_pending" if active else "portal_control_inactive",
            "clean": native["clean"],
            "cleanup_pending": native["cleanup_pending"],
            "audit_error": grant.audit_error,
        }

    async def open(self, agent_id: str, data: object) -> Record:
        request = owner(data)
        if self._closing or agent_id in self._opening or len(self._opening) >= 4:
            raise error("portal_control_busy")
        while agent_id not in self._grants and len(set(self._grants) | self._opening) >= 8:
            victim = next(
                (
                    key
                    for key, old in self._grants.items()
                    if old.closed_audit
                    and not old.opening
                    and old.native is not None
                    and old.native["phase"] in TERMINAL
                    and not old.native["cleanup_pending"]
                ),
                None,
            )
            if victim is None:
                raise error("portal_control_busy")
            self._grants.pop(victim)
        self._opening.add(agent_id)
        task = asyncio.create_task(
            self._open(agent_id, cast(str, request["view_id"]), cast(int, request["devices"])),
            name="control-consent-admission",
        )
        self._own(task)
        return await asyncio.shield(task)

    async def _open(self, agent_id: str, view_id: str, requested: int) -> Record:
        grant: Grant | None = None
        try:
            await self._authorize(agent_id, view_id)
            if self._closing:
                raise error("portal_control_draining")
            previous = self._grants.get(agent_id)
            if previous is not None:
                previous.detached = True
                try:
                    native = self._validated(previous, await self._backend.revoke(previous.scope, 5))
                except BusinessError as caught:
                    if caught.code != "portal_control_changed":
                        raise
                    native = await self._status(previous)
                if native["cleanup_pending"] or not native["clean"]:
                    raise error("portal_cleanup_pending")
                if previous.watcher is not None:
                    await asyncio.shield(previous.watcher)
                if not previous.closed_audit:
                    raise error("portal_control_audit_failed")
            grant = Grant(
                {"agent_id": agent_id, "view_id": view_id, "boot_id": self._boot, "grant_id": self._ids.new()},
                requested,
            )
            self._grants[agent_id] = grant
            with self._observer.context(agent_id=agent_id, operation_id=cast(str, grant.scope["grant_id"])):
                await self._guard(grant)
                await self._audit(grant, "desktop.control_requested")
                grant.requested_audit = True
                native = self._validated(
                    grant, await self._backend.open(grant.scope, requested, before_dispatch=lambda: self._guard(grant))
                )
                await self._guard(grant)
                self._watcher(grant)
                return self._public(grant, native)
        except BaseException:
            if grant is not None:
                grant.code = grant.code or (
                    "portal_control_audit_failed" if grant.audit_error else "portal_control_open_failed"
                )
                grant.detached = True
                try:
                    self._validated(grant, await self._backend.revoke(grant.scope, 0))
                except BusinessError as caught:
                    if caught.code != "portal_control_changed":
                        raise
                self._watcher(grant)
            raise
        finally:
            if grant is not None:
                grant.opening = False
            self._opening.discard(agent_id)

    def _watcher(self, grant: Grant) -> None:
        if grant.watcher is None:
            grant.watcher = asyncio.create_task(self._watch(grant), name="control-consent-settlement")
            self._own(grant.watcher)

    async def _finish(self, grant: Grant) -> bool:
        async with grant.audit_lock:
            if grant.closed_audit:
                return True
            native = await self._status(grant)
            if grant.opening or native["cleanup_pending"] or native["phase"] not in TERMINAL:
                return False
            await self._audit(
                grant,
                "desktop.control_closed",
                phase=native["phase"],
                error_code=grant.code or native["error_code"],
                clean=native["clean"],
            )
            grant.closed_audit = True
            grant.audit_error = False
            agent_id = cast(str, grant.scope["agent_id"])
            if grant.forget_after_settlement and self._grants.get(agent_id) is grant:
                self._grants.pop(agent_id)
            return True

    async def _watch(self, grant: Grant) -> None:
        try:
            value = await self._status(grant)
            while True:
                try:
                    if not grant.detached and not self._closing:
                        await self._guard(grant)
                        if (
                            value["phase"] == "system_authorized"
                            and not value["cleanup_pending"]
                            and value["granted_devices"] == grant.requested
                            and not grant.system_audit
                        ):
                            await self._audit(grant, "desktop.control_system_authorized")
                            await self._guard(grant)
                            grant.system_audit = True
                except Exception as caught:  # noqa: BLE001 -- settlement/view failure revokes the retained owner, never grants input
                    if not grant.detached and not self._closing:
                        grant.code = (
                            caught.code
                            if isinstance(caught, BusinessError) and caught.code.startswith(("portal_", "desktop_"))
                            else "portal_control_settlement_failed"
                        )
                    grant.detached = True
                    value = self._validated(grant, await self._backend.revoke(grant.scope, 0))
                if value["phase"] in TERMINAL and not value["cleanup_pending"]:
                    break
                value = self._validated(grant, await self._backend.wait(grant.scope, cast(int, value["revision"])))
        except Exception as caught:  # noqa: BLE001 -- malformed/lost metadata cannot leave system consent unguarded
            grant.detached = True
            grant.code = (
                caught.code
                if isinstance(caught, BusinessError) and caught.code.startswith(("portal_", "desktop_"))
                else "portal_control_settlement_failed"
            )
            try:
                self._validated(grant, await self._backend.revoke(grant.scope, 0))
            except Exception:  # noqa: BLE001 -- backend retains the actual process for lifecycle retry
                grant.code = "portal_control_settlement_failed"
        finally:
            grant.system_audit = False
            await self._finish(grant)

    async def status(self, agent_id: str, view_id: str) -> Record:
        view_id = uuid_value(view_id)
        await self._authorize(agent_id, view_id)
        grant = self._grants.get(agent_id)
        if grant is None or grant.scope["view_id"] != view_id:
            return {"grant": None, "input_authorized": False}
        return {"grant": self._public(grant, await self._status(grant)), "input_authorized": False}

    async def detach(self, agent_id: str, view_id: str) -> None:
        grant = self._grants.get(agent_id)
        if grant is None or grant.scope["view_id"] != view_id:
            return
        grant.detached = True
        grant.forget_after_settlement = True
        grant.system_audit = False
        try:
            native = self._validated(grant, await self._backend.revoke(grant.scope, 5))
        except BusinessError as caught:
            if caught.code != "portal_control_changed":
                raise
            native = await self._status(grant)
        if grant.opening or native["cleanup_pending"] or not native["clean"]:
            raise error("portal_cleanup_pending")
        if grant.watcher is not None:
            await asyncio.shield(grant.watcher)
        if not grant.closed_audit and not await self._finish(grant):
            raise error("portal_control_audit_failed")
        if self._grants.get(agent_id) is grant:
            self._grants.pop(agent_id, None)

    async def close_grant(self, agent_id: str, view_id: str, grant_id: str) -> Record:
        await self._authorize(agent_id, uuid_value(view_id))
        grant = self._grants.get(agent_id)
        if grant is None or grant.scope["view_id"] != view_id or grant.scope["grant_id"] != uuid_value(grant_id):
            raise error("portal_control_changed")
        await self.detach(agent_id, view_id)
        return {"closed": True, "grant_id": grant_id, "input_authorized": False}

    def health(self) -> Record:
        return {
            "closing": self._closing,
            "pending": len(self._pending),
            "grants": len(self._grants),
            "audit_pending": sum(not grant.closed_audit for grant in self._grants.values()),
            "input_authorized": False,
            "backend": self._backend.health(),
        }

    def stop_accepting(self) -> None:
        self._closing = True
        for grant in self._grants.values():
            grant.detached = True
            grant.system_audit = False
        self._backend.stop_accepting()

    async def close(self, timeout_s: float) -> bool:
        self.stop_accepting()
        deadline = asyncio.get_running_loop().time() + max(0, timeout_s)
        if not await self._backend.close(max(0, deadline - asyncio.get_running_loop().time())):
            return False
        while self._pending:
            _, pending = await asyncio.wait(self._pending, timeout=max(0, deadline - asyncio.get_running_loop().time()))
            if pending:
                return False
        for grant in list(self._grants.values()):
            if not grant.closed_audit:
                if grant.settlement is None or grant.settlement.done():
                    grant.settlement = asyncio.create_task(self._finish(grant), name="control-consent-audit-close")
                    self._own(grant.settlement)
                done, _ = await asyncio.wait(
                    (grant.settlement,), timeout=max(0, deadline - asyncio.get_running_loop().time())
                )
                if (
                    not done
                    or grant.settlement.cancelled()
                    or grant.settlement.exception() is not None
                    or not grant.settlement.result()
                ):
                    return False
        self._grants.clear()
        return True
