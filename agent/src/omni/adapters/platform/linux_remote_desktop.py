"""Separate Wayland device consent and owned EI core negotiation; no ScreenCast reuse, clipboard, restored authority or input."""

from __future__ import annotations

import asyncio
import uuid
from collections.abc import Awaitable, Callable
from typing import Any, cast

from ...domain.desktop_control import devices
from ...domain.desktop_portal import CHOICE_SECONDS, LIFETIME_SECONDS
from .linux_bus import BusPort, Endpoint, failure
from .linux_eis import EisConnection
from .linux_eis_devices import EisDevices
from .linux_fds import OwnedFd
from .linux_portal import PATH, PORTAL, SESSION, PortalSession
from .linux_seat_input import SeatInput
from .linux_session import PROPERTIES, SessionInspector, number, object_path, one, variant
from .linux_signals import SignalBus
from .linux_wayland_windows import WaylandWindows

REMOTE_DESKTOP = "org.freedesktop.portal.RemoteDesktop"


class ControlSession(PortalSession):
    interface = REMOTE_DESKTOP

    def __init__(
        self,
        inspector: SessionInspector,
        requested: int,
        *,
        signal_factory: Callable[[BusPort], SignalBus] = SignalBus,
        eis_factory: Callable[[OwnedFd, Callable[[], Awaitable[None]]], EisConnection] = EisConnection,
        device_factory: Callable[[OwnedFd, Callable[[], Awaitable[None]], int], EisConnection] = EisDevices,
        input_factory: Callable[[SessionInspector, Callable[[], Awaitable[None]]], SeatInput] = SeatInput,
        window_factory: Callable[
            [SessionInspector, OwnedFd, str, Callable[[], Awaitable[None]]], WaylandWindows
        ] = WaylandWindows,
    ) -> None:
        super().__init__(inspector, signal_factory=signal_factory)
        self.requested = devices(requested)
        self._attempted = self._invalidated = self._closed = False
        self._granted = 0
        self._deadline: float | None = None
        self._eis_factory = eis_factory
        self._device_factory = device_factory
        self._eis_attempted = False
        self._eis_task: asyncio.Task[EisConnection] | None = None
        self._eis: EisConnection | None = None
        self._eis_descriptor: OwnedFd | None = None
        self._input_factory = input_factory
        self._input_attempted = False
        self._input: SeatInput | None = None
        self._input_task: asyncio.Task[SeatInput] | None = None
        self._window_factory = window_factory
        self._window_attempted = False
        self._windows: WaylandWindows | None = None
        self._window_task: asyncio.Task[WaylandWindows] | None = None

    async def prepare(self) -> None:
        if self._attempted or self._closed or self._invalidated:
            raise failure("portal_state_invalid")
        self._attempted = True
        self._deadline = asyncio.get_running_loop().time() + LIFETIME_SECONDS
        try:
            await self.inspector.open(accessibility=False)
            if self.inspector.session is None or self.inspector.session.kind != "wayland":
                raise failure("portal_session_unavailable")
            self.bus = await self.inspector.open_bus(
                Endpoint(f"/run/user/{self.inspector.ops.uid}/bus", self.inspector.ops.uid)
            )
            self.owner = await self.inspector.owner(self.bus, PORTAL, self.inspector.ops.uid)
            self.signals = self._signals(self.bus)
            raw = one(
                await self.bus.call(self.owner.name, PATH, PROPERTIES, "GetAll", "s", [REMOTE_DESKTOP], returns="a{sv}")
            )
            if not isinstance(raw, dict) or len(cast(dict[str, object], raw)) > 32:
                raise failure("portal_response_invalid")
            row = cast(dict[str, Any], raw)
            if number(variant(row.get("version"), "u")) < 2:
                raise failure("portal_version_unavailable")
            available = number(variant(row.get("AvailableDeviceTypes"), "u"))
            if available & self.requested != self.requested:
                raise failure("portal_control_devices_unavailable")
            self.prepared = True
            await self.check()
        except BaseException:
            self._invalidated = True
            raise

    async def system_check(self) -> None:
        try:
            if (
                self._invalidated
                or self._closed
                or self._deadline is None
                or asyncio.get_running_loop().time() >= self._deadline
                or self._eis is not None
                and not self._eis.alive
            ):
                raise failure("portal_revoked")
            await super().check()
        except BaseException:
            self._invalidated = True
            self._granted = 0
            raise

    async def native_check(self) -> None:
        await self.system_check()
        if self._input is not None:
            try:
                await self._input.check()
            except BaseException:
                self._invalidated, self._granted = True, 0
                raise

    async def check(self) -> None:
        await self.native_check()
        if self._windows is not None:
            try:
                await self._windows.check()
            except BaseException:
                self._invalidated, self._granted = True, 0
                raise

    async def prepare_windows(self, name: str) -> WaylandWindows:
        if (
            self._window_attempted
            or self._closed
            or self._eis_descriptor is None
            or self._eis is None
            or self._granted != self.requested
        ):
            raise failure("portal_state_invalid")
        self._window_attempted = True
        self._window_task = asyncio.create_task(self._prepare_windows(name), name="portal-owned-wayland-windows")
        self._window_task.add_done_callback(lambda task: task.exception() if not task.cancelled() else None)
        return await asyncio.shield(self._window_task)

    async def _prepare_windows(self, name: str) -> WaylandWindows:
        try:
            await self.native_check()
            assert self._eis_descriptor is not None
            self._windows = self._window_factory(self.inspector, self._eis_descriptor, name, self.native_check)
            await self._windows.prepare()
            await self.check()
            return self._windows
        except BaseException:
            self._invalidated, self._granted = True, 0
            if self._windows is not None:
                await self._windows.close()
            raise

    async def prepare_input_monitor(self) -> SeatInput:
        if self._input_attempted or self._closed or not self.started or self._granted != self.requested:
            raise failure("portal_state_invalid")
        self._input_attempted = True
        self._input_task = asyncio.create_task(self._prepare_input(), name="portal-owned-seat-input")
        self._input_task.add_done_callback(lambda task: task.exception() if not task.cancelled() else None)
        return await asyncio.shield(self._input_task)

    async def _prepare_input(self) -> SeatInput:
        try:
            await self.system_check()
            self._input = self._input_factory(self.inspector, self.system_check)
            await self._input.prepare()
            await self.check()
            return self._input
        except BaseException:
            self._invalidated, self._granted = True, 0
            if self._input is not None:
                await self._input.close()
            raise

    async def request(
        self, member: str, signature: str, arguments: list[object], options: dict[str, object]
    ) -> dict[str, Any]:
        # Keep this reusable request mechanism incapable of reaching Notify*
        # methods even if a future caller passes an untrusted member name.
        allowed = {"CreateSession": "a{sv}", "SelectDevices": "oa{sv}", "Start": "osa{sv}"}
        if member not in allowed or signature != allowed[member]:
            raise failure("portal_control_invalid")
        return await super().request(member, signature, arguments, options)

    async def authorize(self, phase: Callable[[str], Awaitable[None]]) -> None:
        if not self.prepared or self.started or self._closed:
            raise failure("portal_state_invalid")
        self.started = True
        try:
            await self.check()
            assert self.signals is not None and self.owner is not None
            sender = self.signals.name[1:].replace(".", "_")
            token = "omni_" + uuid.uuid4().hex
            self.session = f"{PATH}/session/{sender}/{token}"
            self.closed_subscription = await self.signals.listen(
                self.owner.name, self.session, SESSION, "Closed", "a{sv}"
            )
            async with asyncio.timeout(CHOICE_SECONDS):
                await phase("creating")
                value = await self.request(
                    "CreateSession", "a{sv}", [], {"session_handle_token": self.signals.variant("s", token)}
                )
                if object_path(variant(value.get("session_handle"), "s")) != self.session:
                    raise failure("portal_response_invalid")
                await phase("selecting")
                await self.request(
                    "SelectDevices",
                    "oa{sv}",
                    [self.session],
                    {"types": self.signals.variant("u", self.requested), "persist_mode": self.signals.variant("u", 0)},
                )
                await phase("authorizing")
                value = await self.request("Start", "osa{sv}", [self.session, ""], {})
                if set(value) - {"devices", "clipboard_enabled", "streams"}:
                    raise failure("portal_control_scope_changed")
                if number(variant(value.get("devices"), "u")) != self.requested:
                    raise failure("portal_control_devices_denied")
                if (
                    "restore_token" in value
                    or "clipboard_enabled" in value
                    and variant(value["clipboard_enabled"], "b") is not False
                ):
                    raise failure("portal_control_scope_changed")
                if "streams" in value and variant(value["streams"], "a(ua{sv})") != []:
                    raise failure("portal_control_scope_changed")
                await self.check()
                self._granted = self.requested
                await phase("system_authorized")
        except BaseException:
            self._granted = 0
            self._invalidated = True
            raise

    async def granted_devices(self) -> int:
        await self.check()
        return self._granted

    async def open_pipewire(self) -> OwnedFd:
        raise failure("portal_control_scope_changed")

    async def prepare_eis(self, *, with_devices: bool = False) -> EisConnection:
        if self._eis_attempted or self._closed or not self.started or self._granted != self.requested:
            raise failure("portal_state_invalid")
        self._eis_attempted = True
        self._eis_task = asyncio.create_task(self._prepare_eis(with_devices), name="portal-owned-eis")
        self._eis_task.add_done_callback(lambda task: task.exception() if not task.cancelled() else None)
        # A disconnected caller does not abandon a late SCM_RIGHTS reply or
        # start a second ConnectToEIS. close retains this exact operation.
        return await asyncio.shield(self._eis_task)

    async def _prepare_eis(self, with_devices: bool) -> EisConnection:
        try:
            await self.check()
            bus = cast(BusPort | None, self.bus)
            if bus is None or self.owner is None or self.session is None:
                raise failure("portal_state_invalid")
            remote = await getattr(bus, "connect_eis")(self.owner.name, self.session)
            self._eis_descriptor = remote
            self._eis = (
                self._device_factory(remote, self.check, self.requested)
                if with_devices
                else self._eis_factory(remote, self.check)
            )
            await self._eis.prepare()
            await self.check()
            return self._eis
        except BaseException:
            self._invalidated, self._granted = True, 0
            if self._eis is not None:
                await self._eis.close()
            elif self._eis_descriptor is not None:
                self._eis_descriptor.close()
            raise

    async def close(self) -> bool:
        self._granted = 0
        self._closed = True
        if self._eis_task is not None and not self._eis_task.done():
            return False
        if self._eis is not None and not await self._eis.close():
            return False
        if self._windows is not None and not await self._windows.close():
            return False
        if self._window_task is not None and not self._window_task.done():
            return False
        if self._input_task is not None and not self._input_task.done():
            return False
        if self._input is not None and not await self._input.close():
            return False
        return await super().close()
