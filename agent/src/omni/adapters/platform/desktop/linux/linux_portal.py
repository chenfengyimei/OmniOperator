"""Explicit ephemeral window selection on a pinned Wayland portal; never opens PipeWire FDs or dispatches input."""

from __future__ import annotations

import asyncio
import uuid
from collections.abc import Awaitable, Callable
from typing import Any, cast

from omni.domain.desktop_portal import CHOICE_SECONDS
from omni.adapters.platform.desktop.linux.linux_bus import BusPort, Endpoint, LocalBus, RemoteError, failure
from omni.adapters.platform.desktop.linux.linux_fds import OwnedFd
from omni.adapters.platform.desktop.linux.linux_session import (
    PROPERTIES,
    LocalFactory,
    Owner,
    SessionInspector,
    number,
    object_path,
    one,
    variant,
)
from omni.adapters.platform.desktop.linux.linux_signals import SignalBus

PORTAL = "org.freedesktop.portal.Desktop"
PATH = "/org/freedesktop/portal/desktop"
SCREENCAST = "org.freedesktop.portal.ScreenCast"
REQUEST = "org.freedesktop.portal.Request"
SESSION = "org.freedesktop.portal.Session"


class PortalFactory(LocalFactory):
    async def open(self, endpoint: Endpoint) -> LocalBus:
        return await LocalBus(endpoint, self.ops, unix_fds=endpoint.address == f"/run/user/{self.ops.uid}/bus").open()


class PortalSession:
    interface = SCREENCAST

    def __init__(
        self, inspector: SessionInspector, *, signal_factory: Callable[[BusPort], SignalBus] = SignalBus
    ) -> None:
        self.inspector, self._signals = inspector, signal_factory
        self.bus = None
        self.signals = None
        self.owner: Owner | None = None
        self.session: str | None = None
        self.pending: set[str] = set()
        self.closed_subscription = None
        self.stream: tuple[int, int] | None = None
        self.prepared = False
        self.started = False
        self.revoked = False

    async def prepare(self) -> None:
        await self.inspector.open(accessibility=False)
        if self.inspector.session is None or self.inspector.session.kind != "wayland":
            raise failure("portal_session_unavailable")
        self.bus = await self.inspector.open_bus(
            Endpoint(f"/run/user/{self.inspector.ops.uid}/bus", self.inspector.ops.uid)
        )
        self.owner = await self.inspector.owner(self.bus, PORTAL, self.inspector.ops.uid)
        self.signals = self._signals(self.bus)
        raw_row = one(
            await self.bus.call(self.owner.name, PATH, PROPERTIES, "GetAll", "s", [SCREENCAST], returns="a{sv}")
        )
        if not isinstance(raw_row, dict):
            raise failure("portal_response_invalid")
        row = cast(dict[str, Any], raw_row)
        if len(row) > 32:
            raise failure("portal_response_invalid")
        # V6 gives the non-reused PipeWire object serial. Never substitute a
        # recycled node ID for future frame targeting on older implementations.
        if number(variant(row.get("version"), "u")) < 6:
            raise failure("portal_version_unavailable")
        if (
            not number(variant(row.get("AvailableSourceTypes"), "u")) & 2
            or not number(variant(row.get("AvailableCursorModes"), "u")) & 1
        ):
            raise failure("portal_window_source_unavailable")
        self.prepared = True
        await self.check()

    async def check(self) -> None:
        if self.revoked:
            raise failure("portal_revoked")
        await self.inspector.check()
        assert self.bus is not None and self.owner is not None
        if await self.inspector.owner(self.bus, PORTAL, self.inspector.ops.uid) != self.owner:
            raise failure("portal_owner_changed")
        if self.closed_subscription is not None:
            if self.closed_subscription.error:
                raise failure("portal_signal_invalid")
            if not self.closed_subscription.queue.empty():
                self.revoked = True
                raise failure("portal_revoked")

    async def request(
        self, member: str, signature: str, arguments: list[object], options: dict[str, object]
    ) -> dict[str, Any]:
        await self.check()
        assert self.signals is not None and self.owner is not None and self.bus is not None
        token = "omni_" + uuid.uuid4().hex
        sender = self.signals.name[1:].replace(".", "_")
        path = f"{PATH}/request/{sender}/{token}"
        self.pending.add(path)
        subscription = await self.signals.listen(self.owner.name, path, REQUEST, "Response", "ua{sv}")
        try:
            options = {**options, "handle_token": self.signals.variant("s", token)}
            returned = one(
                await self.bus.call(
                    self.owner.name, PATH, self.interface, member, signature, [*arguments, options], returns="o"
                )
            )
            if returned != path:
                raise failure("portal_response_invalid")
            body = await subscription.next()
            if (
                len(body) != 2
                or type(body[0]) is not int
                or body[0] not in {0, 1, 2}
                or not isinstance(body[1], dict)
                or len(cast(dict[str, Any], body[1])) > 32
            ):
                raise failure("portal_response_invalid")
            self.pending.discard(path)
            if body[0] != 0:
                raise failure("portal_cancelled" if body[0] == 1 else "portal_request_failed")
            await self.check()
            return cast(dict[str, Any], body[1])
        finally:
            if not await subscription.close():
                raise failure("portal_cleanup_pending")

    async def authorize(self, phase: Callable[[str], Awaitable[None]]) -> None:
        if not self.prepared or self.started:
            raise failure("portal_state_invalid")
        self.started = True
        assert self.signals is not None and self.owner is not None
        sender = self.signals.name[1:].replace(".", "_")
        token = "omni_" + uuid.uuid4().hex
        self.session = f"{PATH}/session/{sender}/{token}"
        self.closed_subscription = await self.signals.listen(self.owner.name, self.session, SESSION, "Closed", "a{sv}")
        async with asyncio.timeout(CHOICE_SECONDS):
            await phase("creating")
            value = await self.request(
                "CreateSession", "a{sv}", [], {"session_handle_token": self.signals.variant("s", token)}
            )
            # The portal ABI deliberately uses 's' for this object path.
            if object_path(variant(value.get("session_handle"), "s")) != self.session:
                raise failure("portal_response_invalid")
            await phase("selecting")
            await self.request(
                "SelectSources",
                "oa{sv}",
                [self.session],
                {
                    "types": self.signals.variant("u", 2),
                    "multiple": self.signals.variant("b", False),
                    "cursor_mode": self.signals.variant("u", 1),
                    "persist_mode": self.signals.variant("u", 0),
                },
            )
            await phase("authorizing")
            value = await self.request("Start", "osa{sv}", [self.session, ""], {})
            raw_streams = variant(value.get("streams"), "a(ua{sv})")
            if not isinstance(raw_streams, list):
                raise failure("portal_response_invalid")
            streams = cast(list[object], raw_streams)
            if len(streams) != 1:
                raise failure("portal_response_invalid")
            first = streams[0]
            if not isinstance(first, list):
                raise failure("portal_response_invalid")
            pair = cast(list[object], first)
            if len(pair) != 2:
                raise failure("portal_response_invalid")
            node, raw_properties = pair
            if not isinstance(raw_properties, dict):
                raise failure("portal_window_source_unavailable")
            properties = cast(dict[str, Any], raw_properties)
            if len(properties) > 32 or number(variant(properties.get("source_type"), "u")) != 2:
                raise failure("portal_window_source_unavailable")
            serial = number(variant(properties.get("pipewire-serial"), "t"), (1 << 64) - 1)
            if not serial or not number(node):
                raise failure("portal_response_invalid")
            self.stream = (cast(int, node), serial)
            await self.check()
            await phase("active")

    async def close(self) -> bool:
        clean = True
        if self.bus is not None and self.owner is not None:
            for path, interface in [(p, REQUEST) for p in sorted(self.pending)] + (
                [(self.session, SESSION)] if self.session else []
            ):
                try:
                    await self.bus.call(self.owner.name, path, interface, "Close", returns="")
                except RemoteError as error:
                    if error.name not in {
                        "org.freedesktop.DBus.Error.UnknownObject",
                        "org.freedesktop.DBus.Error.UnknownMethod",
                        "org.freedesktop.DBus.Error.NameHasNoOwner",
                        "org.freedesktop.DBus.Error.ServiceUnknown",
                    }:
                        clean = False
                except Exception:  # noqa: BLE001 -- retain cleanup failure without provider text
                    clean = False
            self.pending.clear()
        if self.signals is not None:
            try:
                clean = await self.signals.close() and clean
            except Exception:  # noqa: BLE001 -- subscription cleanup is part of the session obligation
                clean = False
        self.stream = None
        return clean

    async def open_pipewire(self) -> OwnedFd:
        await self.check()
        if self.stream is None or self.session is None or self.bus is None or self.owner is None:
            raise failure("portal_state_invalid")
        # Narrow optional bus capability; no second socket or node ID escapes.
        remote = await getattr(self.bus, "open_pipewire")(self.owner.name, self.session)
        try:
            await self.check()
        except BaseException:
            remote.close()
            raise
        return remote
