"""Read-only wlroots toplevel generations on the granted EI compositor; no app PID inference, focus acquisition or input authority."""

from __future__ import annotations

import asyncio
import socket
import struct
from collections.abc import Awaitable, Callable
from dataclasses import dataclass, field

from omni.domain.contracts import BusinessError
from omni.adapters.platform.desktop.linux.linux_bus import failure
from omni.adapters.platform.desktop.linux.linux_fds import OwnedFd
from omni.adapters.platform.desktop.linux.linux_session import SessionInspector
from omni.adapters.platform.desktop.linux.linux_wayland_peer import WaylandPeer
from omni.adapters.platform.desktop.linux.linux_wayland_wire import (
    ORDER,
    SERVER_ID,
    WaylandWire,
    read_string,
    string,
    word,
)

MANAGER = "zwlr_foreign_toplevel_manager_v1"


@dataclass(frozen=True)
class WaylandWindow:
    identity: int
    app_id: str
    states: tuple[int, ...]
    outputs: tuple[int, ...]
    parent: int | None
    epoch: int
    closed: bool


@dataclass
class _Window:
    identity: int
    app_id: str = ""
    states: tuple[int, ...] = ()
    outputs: set[int] = field(default_factory=lambda: set[int]())
    parent: int | None = None
    epoch: int = 0
    initial: bool = False
    dirty: bool = True
    closed: bool = False

    def snapshot(self) -> WaylandWindow:
        return WaylandWindow(
            self.identity, self.app_id, self.states, tuple(sorted(self.outputs)), self.parent, self.epoch, self.closed
        )


class WaylandWindows:
    def __init__(
        self,
        inspector: SessionInspector,
        descriptor: OwnedFd,
        name: str,
        guard: Callable[[], Awaitable[None]],
        *,
        peer_factory: Callable[[SessionInspector, OwnedFd, str], WaylandPeer] = WaylandPeer,
        wire_factory: Callable[[socket.socket, Callable[[], None]], WaylandWire] = WaylandWire,
    ) -> None:
        self.guard = guard
        self.peer = peer_factory(inspector, descriptor, name)
        self._wire_factory = wire_factory
        self.wire: WaylandWire | None = None
        self.operation: asyncio.Task[None] | None = None
        self.registry = self.manager = 0
        self.globals: dict[int, tuple[str, int]] = {}
        self.removed: set[int] = set()
        self.windows: dict[int, _Window] = {}
        self.outputs: dict[int, int] = {}  # object -> advertised version
        self.output_done: set[int] = set()
        self.output_dirty: set[int] = set()
        self.selected: WaylandWindow | None = None
        self._attempted = self._ready = self._failed = self._closed = False
        self._close_uncertain = False
        self._selecting = False

    def _valid(self) -> None:
        if self._failed or self._closed:
            raise failure("portal_wayland_observation_changed")

    async def _sync(self) -> None:
        assert self.wire is not None
        callback = self.wire.allocate()
        await self.wire.send(1, 0, word(callback))
        while True:
            identity, opcode, payload = await self.wire.message()
            if identity == callback:
                if opcode != 0 or len(payload) != 4:
                    raise failure("portal_wayland_protocol_invalid")
                self.wire.retired.add(callback)
                return
            await self._event(identity, opcode, payload)

    async def _event(self, identity: int, opcode: int, payload: bytes) -> None:
        assert self.wire is not None
        if identity == 1:
            self.wire.display(opcode, payload)
            return
        if identity == self.registry:
            if opcode == 0:
                if len(payload) < 12:
                    raise failure("portal_wayland_protocol_invalid")
                name = struct.unpack_from(ORDER + "I", payload)[0]
                interface, end = read_string(payload, 4, maximum=100)
                if end + 4 != len(payload) or not name or name in self.globals or len(self.globals) >= 128:
                    raise failure("portal_wayland_protocol_invalid")
                version = struct.unpack_from(ORDER + "I", payload, end)[0]
                if not version:
                    raise failure("portal_wayland_protocol_invalid")
                self.globals[name] = (interface, version)
                if self._ready:
                    raise failure("portal_wayland_observation_changed")
                return
            if opcode == 1 and len(payload) == 4:
                name = struct.unpack(ORDER + "I", payload)[0]
                if name not in self.globals or name in self.removed:
                    raise failure("portal_wayland_protocol_invalid")
                self.removed.add(name)
                raise failure("portal_wayland_observation_changed")
            raise failure("portal_wayland_protocol_invalid")
        if identity in self.outputs:
            self._output(identity, opcode, payload)
            return
        if identity == self.manager:
            if opcode != 0 or len(payload) != 4:
                raise failure("portal_wayland_observation_changed")
            handle = struct.unpack(ORDER + "I", payload)[0]
            if handle < SERVER_ID or handle in self.windows or len(self.windows) >= 64:
                raise failure("portal_wayland_protocol_invalid")
            self.windows[handle] = _Window(handle)
            return
        window = self.windows.get(identity)
        if window is None or window.closed:
            raise failure("portal_wayland_protocol_invalid")
        window.epoch += 1
        if opcode in {0, 1}:
            value, end = read_string(payload)
            if end != len(payload):
                raise failure("portal_wayland_protocol_invalid")
            if opcode == 1:
                if any(ord(char) < 32 or 127 <= ord(char) <= 159 for char in value):
                    raise failure("portal_wayland_protocol_invalid")
                window.app_id = value
        elif opcode in {2, 3, 7} and len(payload) == 4:
            ref = struct.unpack(ORDER + "I", payload)[0]
            if opcode == 7:
                if ref and (ref not in self.windows or ref == identity):
                    raise failure("portal_wayland_protocol_invalid")
                seen = {identity}
                ancestor = ref or None
                while ancestor is not None:
                    if ancestor in seen or self.windows[ancestor].closed:
                        raise failure("portal_wayland_protocol_invalid")
                    seen.add(ancestor)
                    ancestor = self.windows[ancestor].parent
                window.parent = ref or None
            else:
                if ref not in self.outputs or (opcode == 2) == (ref in window.outputs):
                    raise failure("portal_wayland_protocol_invalid")
                if opcode == 2:
                    window.outputs.add(ref)
                else:
                    window.outputs.remove(ref)
        elif opcode == 4:
            if len(payload) < 4:
                raise failure("portal_wayland_protocol_invalid")
            size = struct.unpack_from(ORDER + "I", payload)[0]
            if size % 4 or size > 16 or len(payload) != 4 + size:
                raise failure("portal_wayland_protocol_invalid")
            states = struct.unpack(ORDER + "I" * (size // 4), payload[4:])
            if len(set(states)) != len(states) or any(value > 3 for value in states):
                raise failure("portal_wayland_protocol_invalid")
            window.states = tuple(sorted(states))
        elif opcode == 5 and not payload:
            window.initial, window.dirty = True, False
            return
        elif opcode == 6 and not payload:
            window.closed, window.dirty = True, False
            return
        else:
            raise failure("portal_wayland_protocol_invalid")
        window.dirty = True

    def _output(self, identity: int, opcode: int, payload: bytes) -> None:
        if self._ready:
            raise failure("portal_wayland_observation_changed")
        version = self.outputs[identity]
        if opcode == 0 and len(payload) >= 32:
            _make, end = read_string(payload, 20)
            _model, end = read_string(payload, end)
            if end + 4 != len(payload):
                raise failure("portal_wayland_protocol_invalid")
        elif opcode == 1 and len(payload) == 16:
            flags, width, height, refresh = struct.unpack(ORDER + "Iiii", payload)
            if flags & ~3 or not 1 <= width <= 32768 or not 1 <= height <= 32768 or refresh < 0:
                raise failure("portal_wayland_protocol_invalid")
        elif opcode == 2 and not payload:
            self.output_done.add(identity)
            self.output_dirty.discard(identity)
            return
        elif opcode == 3 and len(payload) == 4:
            if not 1 <= struct.unpack(ORDER + "i", payload)[0] <= 16:
                raise failure("portal_wayland_protocol_invalid")
        elif opcode in {4, 5} and version >= 4:
            _value, end = read_string(payload)
            if end != len(payload):
                raise failure("portal_wayland_protocol_invalid")
        else:
            raise failure("portal_wayland_protocol_invalid")
        self.output_dirty.add(identity)

    async def _snapshot(self) -> None:
        self._valid()
        await self.guard()
        self.peer.check()
        await self._sync()
        await self.guard()
        self.peer.check()
        self._valid()
        if (
            self.output_dirty
            or self.output_done != set(self.outputs)
            or any(window.dirty or not window.initial for window in self.windows.values() if not window.closed)
        ):
            raise failure("portal_wayland_protocol_invalid")
        if self.selected is not None:
            active = [
                window.identity
                for window in self.windows.values()
                if not window.closed and 2 in window.states and 1 not in window.states
            ]
            if self.windows[self.selected.identity].snapshot() != self.selected or active != [self.selected.identity]:
                raise failure("portal_wayland_observation_changed")

    async def _prepare(self) -> None:
        try:
            async with asyncio.timeout(3):
                self._valid()
                await self.guard()
                self.wire = self._wire_factory(self.peer.open(), self.peer.check)
                self.registry = self.wire.allocate()
                await self.wire.send(1, 1, word(self.registry))
                await self._sync()
                managers = [
                    (name, version) for name, (interface, version) in self.globals.items() if interface == MANAGER
                ]
                if len(managers) != 1 or managers[0][1] < 3:
                    raise failure("portal_wayland_windows_unavailable")
                output_globals = [
                    (name, version) for name, (interface, version) in self.globals.items() if interface == "wl_output"
                ]
                if len(output_globals) > 16:
                    raise failure("portal_wayland_protocol_limit")
                for name, version in output_globals:
                    if version < 2:
                        raise failure("portal_wayland_windows_unavailable")
                    identity = self.wire.allocate()
                    self.outputs[identity] = min(version, 4)
                    await self.wire.send(
                        self.registry, 0, word(name) + string("wl_output") + word(min(version, 4)) + word(identity)
                    )
                self.manager = self.wire.allocate()
                await self.wire.send(
                    self.registry, 0, word(managers[0][0]) + string(MANAGER) + word(3) + word(self.manager)
                )
                await self._snapshot()
                self._ready = True
        except (BusinessError, asyncio.CancelledError, TimeoutError):
            self._failed = True
            raise
        except Exception:  # noqa: BLE001 -- native paths and protocol text cannot escape
            self._failed = True
            raise failure("portal_wayland_unavailable") from None

    @staticmethod
    def _observe(task: asyncio.Task[None]) -> None:
        if not task.cancelled():
            task.exception()

    async def prepare(self) -> None:
        if self._attempted or self._closed:
            raise failure("portal_state_invalid")
        self._attempted = True
        self.operation = asyncio.create_task(self._prepare(), name="owned-wayland-window-prepare")
        self.operation.add_done_callback(self._observe)
        return await asyncio.shield(self.operation)

    def windows_view(self) -> tuple[WaylandWindow, ...]:
        self._valid()
        if not self._ready:
            raise failure("portal_wayland_windows_unavailable")
        return tuple(window.snapshot() for window in self.windows.values() if not window.closed)

    async def _check(self) -> None:
        try:
            async with asyncio.timeout(1):
                await self._snapshot()
        except (BusinessError, asyncio.CancelledError, TimeoutError):
            self._failed = True
            raise
        except Exception:  # noqa: BLE001 -- native paths and protocol text cannot escape
            self._failed = True
            raise failure("portal_wayland_unavailable") from None

    async def check(self) -> None:
        self._valid()
        if not self._ready:
            raise failure("portal_wayland_windows_unavailable")
        if self.operation is None or self.operation.done():
            self.operation = asyncio.create_task(self._check(), name="owned-wayland-window-check")
            self.operation.add_done_callback(self._observe)
        return await asyncio.shield(self.operation)

    async def select(self, identity: int) -> WaylandWindow:
        if self.selected is not None or self._selecting:
            raise failure("portal_state_invalid")
        # Reserve before the shared async snapshot: two waiters must not both
        # return success for one supposedly single-use target selection.
        self._selecting = True
        try:
            await self.check()
            window = self.windows.get(identity)
            active = [
                item.identity
                for item in self.windows.values()
                if not item.closed and 2 in item.states and 1 not in item.states
            ]
            if window is None or window.closed or window.dirty or active != [identity]:
                raise failure("portal_wayland_target_unverified")
            self.selected = window.snapshot()
            return self.selected
        finally:
            self._selecting = False

    async def close(self) -> bool:
        self._closed, self._ready = True, False
        if self.operation is not None and not self.operation.done():
            self.operation.cancel()
            await asyncio.gather(self.operation, return_exceptions=True)
        if self.wire is not None:
            try:
                self.wire.rights.close()
            except OSError:
                self._close_uncertain = True
        return self.peer.close() and not self._close_uncertain
