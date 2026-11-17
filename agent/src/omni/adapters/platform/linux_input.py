"""Verified Xorg input-change guard for addressed semantic actions; never grabs, synthesizes or stores input content."""

from __future__ import annotations

import asyncio
import hashlib
import json
import socket
import struct
from collections.abc import Awaitable, Callable

from .linux_bus import failure
from .linux_session import SessionInspector
from .linux_x11_socket import Connection
from .linux_x11_wire import Wire, unpack

RAW = frozenset({13, 14, 15, 16, 17, 22, 23, 24})
DEVICE = frozenset({1, 11})


def devices(data: bytes) -> str:
    """Prove one master pair and released buttons; unsupported held-state sources deny control."""
    if len(data) < 32 or not 1 <= unpack("H", data, 8)[0] <= 64:
        raise failure("desktop_input_unverified")
    count, position = unpack("H", data, 8)[0], 32
    ids: set[int] = set()
    masters: dict[int, tuple[int, int]] = {}
    topology: list[object] = []
    for _ in range(count):
        identity, kind, attachment, classes, name_length, enabled = unpack("HHHHHB", data, position)
        if (
            identity < 2
            or identity in ids
            or kind not in {1, 2, 3, 4, 5}
            or enabled not in {0, 1}
            or classes > 32
            or name_length > 1024
        ):
            raise failure("desktop_input_unverified")
        ids.add(identity)
        name_digest = hashlib.sha256(data[position + 12 : position + 12 + name_length]).hexdigest()
        position += 12 + (name_length + 3) // 4 * 4
        if position > len(data):
            raise failure("desktop_input_unverified")
        if kind in {1, 2}:
            if not enabled or kind in masters:
                raise failure("desktop_input_unverified")
            masters[kind] = (identity, attachment)
        seen: set[int] = set()
        described: list[tuple[int, int, int, object]] = []
        for _ in range(classes):
            category, words, _source = unpack("HHH", data, position)
            end = position + words * 4
            if words < 2 or end > len(data):
                raise failure("desktop_input_unverified")
            if enabled and (category not in {0, 1, 2, 3} or kind == 5 and category == 0):
                # Touch/gesture and floating-keyboard held state is not proven
                # by this contract. No environment/absence-of-events fallback.
                raise failure("desktop_input_unverified")
            seen.add(category)
            detail = None
            if category == 0:
                keys = unpack("H", data, position + 6)[0]
                if not 1 <= keys <= 256 or end != position + 8 + keys * 4:
                    raise failure("desktop_input_unverified")
                detail = hashlib.sha256(data[position + 8 : end]).hexdigest()
            if category == 1:
                buttons = unpack("H", data, position + 6)[0]
                mask_bytes = (buttons + 31) // 32 * 4
                if not 1 <= buttons <= 256 or end != position + 8 + mask_bytes + buttons * 4:
                    raise failure("desktop_input_unverified")
                if enabled and any(data[position + 8 : position + 8 + mask_bytes]):
                    raise failure("desktop_input_held")
                detail = buttons
            described.append((category, words, _source, detail))
            position = end
        if kind == 1 and 1 not in seen or kind == 2 and 0 not in seen:
            raise failure("desktop_input_unverified")
        topology.append((identity, kind, attachment, enabled, name_digest, described))
    if position != len(data) or set(masters) != {1, 2} or masters[1] != masters[2][::-1]:
        raise failure("desktop_input_unverified")
    # Device names, button masks and key identifiers never leave this helper.
    return hashlib.sha256(json.dumps(topology, separators=(",", ":")).encode()).hexdigest()


class InputWire(Wire):
    def __init__(self, connection: socket.socket, check: Callable[[], None]) -> None:
        super().__init__(connection, check)
        self.opcode: int | None = None
        self.xkb_event: int | None = None
        self.changed = False
        self.focus: int | None = None
        self.structures: set[int] = set()
        self.events = 0

    async def read(self, length: int) -> bytes:
        if self.received + length > 1024 * 1024:
            raise failure("desktop_input_unverified")
        return await super().read(length)

    async def event(self, header: bytes) -> None:
        self.events += 1
        if self.events > 256 or header[0] & 0x80:
            raise failure("desktop_input_unverified")
        if self.xkb_event is not None and header[0] == self.xkb_event:
            if header[1] not in {0, 1, 2, 3, 7}:
                raise failure("desktop_input_unverified")
        elif header[0] == 35 and header[1] == self.opcode:
            length = unpack("I", header, 4)[0] * 4
            category = unpack("H", header, 8)[0]
            if category not in RAW | DEVICE or length > 4096:
                raise failure("desktop_input_unverified")
            await self.read(length)  # discard keycodes, coordinates and valuators
        elif header[0] in {9, 10}:
            if self.focus is None or unpack("I", header, 4)[0] != self.focus:
                raise failure("desktop_input_unverified")
        elif header[0] in range(16, 28):
            if self.focus is None or unpack("I", header, 8)[0] not in self.structures | {self.focus}:
                raise failure("desktop_input_unverified")
        else:
            raise failure("desktop_input_unverified")
        self.changed = True


class X11Input:
    def __init__(
        self,
        inspector: SessionInspector,
        *,
        connection_factory: Callable[[SessionInspector], Awaitable[Connection]] = Connection.open,
        wire_factory: Callable[[socket.socket, Callable[[], None]], InputWire] = InputWire,
    ) -> None:
        self.inspector, self._connect, self._wire = inspector, connection_factory, wire_factory
        self.connection = self.wire = None
        self.topology: str | None = None
        self.armed = False
        self.lineage_watched = False

    async def open(self) -> None:
        self.connection = await self._connect(self.inspector)
        self.wire = self._wire(self.connection.socket, self.connection.check)
        await self.wire.authenticate(self.connection.cookie(), self.connection.screen)
        opcode = await self.wire.extension(b"XInputExtension")
        version = await self.wire.request(opcode, struct.pack("<HH", 2, 2), 47)
        major, minor = unpack("HH", version, 8)
        if len(version) != 32 or major != 2 or minor < 2:
            raise failure("desktop_input_unverified")
        self.wire.opcode = opcode
        await self.released()
        await self.connection.verify()

    async def released(self) -> None:
        assert self.wire is not None and self.wire.opcode is not None
        data = await self.wire.request(self.wire.opcode, struct.pack("<Hxx", 0), 48, maximum=32768)
        topology = devices(data)
        if self.topology is not None and self.topology != topology:
            raise failure("desktop_input_unverified")
        self.topology = topology
        keys = await self.wire.request(44)
        if len(keys) != 40 or any(keys[8:40]):
            raise failure("desktop_input_held")

    async def arm(self) -> None:
        assert self.wire is not None and self.wire.opcode is not None and self.wire.setup is not None
        if self.armed:
            raise failure("desktop_input_unverified")
        focus = await self.wire.request(43)
        if len(focus) != 32 or unpack("I", focus, 8)[0] < 2:
            raise failure("desktop_input_unverified")
        self.wire.focus = unpack("I", focus, 8)[0]
        mask = sum(1 << kind for kind in RAW | DEVICE)
        await self.wire.send(self.wire.opcode, struct.pack("<IHxxHHI", self.wire.setup.root, 1, 0, 1, mask), 46)
        # Watch the already-focused X window for changes only; never map this
        # XID to an AT-SPI control or use it as an action target.
        await self.wire.send(2, struct.pack("<III", self.wire.focus, 1 << 11, (1 << 21) | (1 << 17)))
        self.armed = True
        await self.check()
        await asyncio.sleep(0.25)
        await self.check()

    async def check(self) -> None:
        assert self.connection is not None and self.wire is not None
        await self.connection.verify()
        await self.released()
        focus = await self.wire.request(43)
        if len(focus) != 32 or unpack("I", focus, 8)[0] != self.wire.focus or self.wire.changed:
            raise failure("desktop_user_intervened")

    async def watch_lineage(self, windows: tuple[int, ...]) -> None:
        """Extend one armed guard to a bounded native ancestry; never acquires focus or changes input."""
        assert self.wire is not None and self.wire.setup is not None
        if (
            not self.armed
            or self.lineage_watched
            or not 1 <= len(windows) <= 16
            or len(set(windows)) != len(windows)
            or windows[0] != self.wire.focus
            or any(
                type(window) is not int or not 2 <= window <= 0xFFFFFFFF or window == self.wire.setup.root
                for window in windows
            )
        ):
            raise failure("desktop_input_unverified")
        self.lineage_watched = True
        # Register before the first subscription's reply barrier so an event
        # arriving while later ancestors are subscribed is already understood.
        self.wire.structures.update(windows)
        for window in windows[1:]:
            await self.wire.send(2, struct.pack("<III", window, 1 << 11, 1 << 17))
        await self.check()

    async def close(self) -> bool:
        return self.connection is None or await self.connection.close()
