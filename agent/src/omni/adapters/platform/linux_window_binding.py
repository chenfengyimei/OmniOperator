"""Bind an explicit AT-SPI provider XID to continuous native ownership/focus; no guessed mapping, focus acquisition or input."""

from __future__ import annotations

from collections.abc import Callable
from typing import cast

from ...domain.desktop import validate_target
from .linux_atspi import ACCESSIBLE, AtspiDesktop
from .linux_bus import failure
from .linux_foreground import WindowFocus
from .linux_input import X11Input
from .linux_session import SessionInspector
from .linux_window_leases import LeaseWire, WindowLeases
from .linux_x11 import X11Desktop

Payload = dict[str, object]


def declared_window(value: object) -> int:
    """Accept only a documented native-XID provider attribute, never a generic id or geometry hint."""
    if not isinstance(value, dict):
        raise failure("desktop_input_unverified")
    attributes = cast(dict[object, object], value)
    if not 1 <= len(attributes) <= 128:
        raise failure("desktop_input_unverified")
    size = 0
    for key, item in attributes.items():
        if not isinstance(key, str) or not isinstance(item, str) or not 1 <= len(key) <= 80 or len(item) > 512:
            raise failure("desktop_input_unverified")
        try:
            size += len(key.encode("utf-8")) + len(item.encode("utf-8"))
        except UnicodeError:
            raise failure("desktop_input_unverified") from None
    if size > 16384 or attributes.get("toolkit") != "Avalonia":
        raise failure("desktop_input_unverified")
    # Avalonia emits this only for RootAtSpiNode with HandleDescriptor == XID,
    # using invariant decimal. Unknown providers/attribute names have no guess.
    handle = attributes.get("avalonia-window-handle")
    if not isinstance(handle, str) or not 1 <= len(handle) <= 10 or not handle.isascii() or not handle.isdecimal():
        raise failure("desktop_input_unverified")
    window = int(handle)
    if not 2 <= window <= 0xFFFFFFFF or str(window) != handle:
        raise failure("desktop_input_unverified")
    return window


def native_desktop(inspector: SessionInspector) -> X11Desktop:
    return X11Desktop(inspector, wire_factory=LeaseWire)


class WindowBinding:
    """Own the binding's native connection and focus monitor, borrowing the verified accessibility session."""

    def __init__(
        self,
        desktop: AtspiDesktop,
        monitor: X11Input,
        *,
        native_factory: Callable[[SessionInspector], X11Desktop] = native_desktop,
    ) -> None:
        self.desktop, self.monitor, self._native_factory = desktop, monitor, native_factory
        self.native: X11Desktop | None = None
        self.focus: WindowFocus | None = None
        self._target: Payload | None = None
        self._ref: tuple[str, str] | None = None
        self._window: int | None = None
        self._attempted = self._ready = self._closed = False

    async def _descriptor(self) -> tuple[tuple[str, str], int]:
        assert self._target is not None
        ref = await self.desktop.verified(self._target)
        attributes = await self.desktop.call(ref, ACCESSIBLE, "GetAttributes", returns="a{ss}")
        return ref, declared_window(attributes)

    async def prepare(self, target: Payload) -> None:
        if self._attempted or self._closed:
            raise failure("desktop_input_unverified")
        self._attempted = True
        try:
            inspector = self.desktop.inspector
            if (
                inspector is not self.monitor.inspector
                or inspector.session is None
                or inspector.session.kind != "x11"
                or self.monitor.connection is not None
                or self.monitor.wire is not None
                or self.monitor.armed
            ):
                raise failure("desktop_input_unverified")
            self._target = validate_target(target)
            self._ref, self._window = await self._descriptor()
            native = self._native_factory(inspector)
            self.native = native  # retain actual ownership before its first await
            if native.inspector is not inspector:
                raise failure("desktop_input_unverified")
            await native.open()
            original, _geometry, _visual = await native.target(self._window)
            if any(
                original[field] != self._target[field]
                for field in ("platform", "pid", "process_created", "application_id")
            ):
                raise failure("desktop_target_changed")
            # These leases belong solely to this binding's native connection;
            # no discovery group's lifetime or another caller's target is used.
            leases = WindowLeases(native)
            alias = (await leases.bind([original]))[0]
            self.focus = WindowFocus(leases, self.monitor)
            await self.focus.prepare(alias)
            await self._validate()
            self._ready = True
        except BaseException:
            self._ready = False
            raise

    async def _validate(self) -> None:
        if self._closed or self.focus is None or self._ref is None or self._window is None:
            raise failure("desktop_input_unverified")
        if await self._descriptor() != (self._ref, self._window):
            raise failure("desktop_target_changed")
        await self.focus.check()
        if await self._descriptor() != (self._ref, self._window):
            raise failure("desktop_target_changed")
        await self.focus.check()

    async def check(self) -> None:
        if not self._ready:
            raise failure("desktop_input_unverified")
        try:
            await self._validate()
        except BaseException:
            self._ready = False
            raise

    async def close(self) -> bool:
        self._closed, self._ready = True, False
        # A pending key release retains BOTH actual connections. The session
        # inspector/accessibility buses remain owned by the outer worker.
        if not await (self.monitor.close() if self.focus is None else self.focus.close()):
            return False
        return self.native is None or self.native.connection is None or await self.native.connection.close()
