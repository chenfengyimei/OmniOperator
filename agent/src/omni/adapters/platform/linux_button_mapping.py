"""Read primary-button composition on a proven Xorg point/source pair; never changes maps, moves, clicks or grants dispatch."""

from __future__ import annotations

import hashlib
import struct
from dataclasses import dataclass
from typing import Protocol, cast

from .linux_bus import failure
from .linux_input import X11Input, device_topology
from .linux_pointer_target import PointerPoint
from .linux_x11_wire import padded, unpack
from .linux_xtest import Sources


class ButtonFocus(Protocol):
    monitor: X11Input

    async def check(self) -> None: ...

    async def verify_pointer(self) -> PointerPoint: ...

    async def verify_sources(self) -> Sources: ...


@dataclass(frozen=True)
class ButtonPlan:
    physical: int
    master_code: int
    logical: int
    source: int
    master: int
    source_buttons: int
    master_buttons: int


def primary(source: bytes, master: bytes, sources: Sources) -> ButtonPlan:
    if not 1 <= len(source) <= 255 or not 1 <= len(master) <= 255:
        raise failure("desktop_input_unverified")
    # Xorg maps a slave's physical detail before constructing the master
    # event; the master then applies its own map. Zero disables a button.
    # Multiple physical aliases may have the same primary effect. Pick the
    # lowest actual code deterministically, with both complete maps bound.
    for code, intermediate in enumerate(source, 1):
        if 1 <= intermediate <= len(master) and master[intermediate - 1] == 1:
            return ButtonPlan(code, intermediate, 1, sources.pointer, sources.pointer_master, len(source), len(master))
    raise failure("desktop_input_unverified")


class ButtonMapping:
    """Borrow one prepared native point and virtual pair; mapping drift/cancellation cannot rebind a prior plan."""

    def __init__(self, focus: ButtonFocus) -> None:
        self.focus = focus
        self._attempted = self._valid = False
        self._sources: Sources | None = None
        self._snapshot: tuple[ButtonPlan, bytes] | None = None

    async def _guard(self) -> None:
        await self.focus.check()
        await self.focus.verify_pointer()
        if await self.focus.verify_sources() != self._sources:
            raise failure("desktop_user_intervened")

    async def _client(self) -> None:
        wire, sources = self.focus.monitor.wire, self._sources
        if wire is None or wire.opcode is None or sources is None:
            raise failure("desktop_input_unverified")
        data = await wire.request(wire.opcode, struct.pack("<I", 0), 45, maximum=0)
        # The monitor's preceding core key/focus queries already use its
        # ClientPointer. Read it, never select or mutate it here.
        if len(data) != 32 or data[8] != 1 or unpack("H", data, 10)[0] != sources.pointer_master:
            raise failure("desktop_input_unverified")

    async def _subscribe(self) -> None:
        wire, sources = self.focus.monitor.wire, self._sources
        if (
            wire is None
            or wire.opcode is None
            or wire.setup is None
            or sources is None
            or not 2 <= sources.pointer <= 255
            or not 2 <= sources.pointer_master <= 255
            or sources.pointer == sources.pointer_master
            or wire.button_mapping_event is not None
        ):
            raise failure("desktop_input_unverified")
        name = b"XInputExtension"
        extension = await wire.request(98, struct.pack("<Hxx", len(name)) + padded(name), maximum=0)
        if (
            len(extension) != 32
            or extension[8] != 1
            or extension[9] != wire.opcode
            or not 64 <= extension[10] <= 116
            or extension[10] + 11 == wire.xkb_event
        ):
            raise failure("desktop_input_unverified")
        event = extension[10] + 11  # XI1 DeviceMappingNotify, not an XI2 generic event
        identities = (sources.pointer_master, sources.pointer)
        # Publish the parser before selecting notifications. XI1 event classes
        # encode (device << 8) | absolute event type, one class per device.
        wire.button_mapping_event = event
        wire.button_mapping_devices = frozenset(identities)
        await wire.send(
            wire.opcode,
            struct.pack("<IHxxII", wire.setup.root, 2, *(identity << 8 | event for identity in identities)),
            6,
        )
        await self._client()  # barrier acknowledges the void subscription

    async def _counts(self) -> dict[int, int]:
        wire, sources = self.focus.monitor.wire, self._sources
        if wire is None or wire.opcode is None or sources is None:
            raise failure("desktop_input_unverified")
        data = await wire.request(wire.opcode, struct.pack("<Hxx", 0), 48, maximum=32768)
        topology = device_topology(data)
        if topology.fingerprint != self.focus.monitor.topology:
            raise failure("desktop_user_intervened")
        selected = {sources.pointer_master, sources.pointer}
        counts: dict[int, int] = {}
        position = 32
        for _ in topology.entries:
            identity, _kind, _attachment, count, size = cast(
                tuple[int, int, int, int, int], unpack("HHHHH", data, position)
            )
            position += 12 + (size + 3) // 4 * 4
            for _ in range(count):
                category, words = cast(tuple[int, int], unpack("HH", data, position))
                if identity in selected and category == 1:
                    if identity in counts:
                        raise failure("desktop_input_unverified")
                    counts[identity] = cast(int, unpack("H", data, position + 6)[0])
                position += words * 4
        if set(counts) != selected or any(not 1 <= count <= 255 for count in counts.values()):
            raise failure("desktop_input_unverified")
        return counts

    async def _map(self, identity: int | None, count: int) -> bytes:
        wire = self.focus.monitor.wire
        if wire is None or wire.opcode is None:
            raise failure("desktop_input_unverified")
        if identity is None:
            data = await wire.request(117, maximum=256)
            size = data[1]
        else:
            data = await wire.request(wire.opcode, struct.pack("<Bxxx", identity), 28, maximum=256)
            if data[1] != 28:
                raise failure("desktop_input_unverified")
            size = data[8]
        if size != count or len(data) != 32 + (size + 3) // 4 * 4:
            raise failure("desktop_input_unverified")
        return data[32 : 32 + size]  # protocol padding is not mapping data

    async def _read(self) -> tuple[ButtonPlan, bytes]:
        wire, sources = self.focus.monitor.wire, self._sources
        if wire is None or wire.setup is None or sources is None:
            raise failure("desktop_input_unverified")
        await self._client()
        counts = await self._counts()
        master = await self._map(sources.pointer_master, counts[sources.pointer_master])
        source = await self._map(sources.pointer, counts[sources.pointer])
        if await self._map(None, counts[sources.pointer_master]) != master:
            raise failure("desktop_user_intervened")
        state = await wire.request(38, struct.pack("<I", wire.setup.root), maximum=0)
        if len(state) != 32 or state[1] != 1 or unpack("I", state, 8)[0] != wire.setup.root:
            raise failure("desktop_input_unverified")
        if unpack("H", state, 24)[0]:
            raise failure("desktop_input_held")
        await self._client()
        plan = primary(source, master, sources)
        fingerprint = hashlib.sha256(
            struct.pack("<HHHH", sources.pointer, sources.pointer_master, len(source), len(master)) + source + master
        ).digest()
        return plan, fingerprint

    async def prepare(self) -> ButtonPlan:
        if self._attempted:
            raise failure("desktop_input_unverified")
        self._attempted = True
        try:
            await self.focus.check()
            await self.focus.verify_pointer()
            self._sources = await self.focus.verify_sources()
            await self._subscribe()
            await self._guard()
            original = await self._read()
            await self._guard()
            if await self._read() != original:
                raise failure("desktop_user_intervened")
            await self._guard()
            self._snapshot, self._valid = original, True
            return original[0]
        except BaseException:
            self._valid = False
            raise

    async def verify(self) -> ButtonPlan:
        if not self._valid or self._snapshot is None:
            raise failure("desktop_input_unverified")
        try:
            await self._guard()
            if await self._read() != self._snapshot:
                raise failure("desktop_user_intervened")
            await self._guard()
            return self._snapshot[0]
        except BaseException:
            self._valid = False
            raise

    async def read_current(self) -> ButtonPlan:
        """Leaf comparison within WindowFocus's complete bracket; never a dispatch permit."""
        if not self._valid or self._snapshot is None:
            raise failure("desktop_input_unverified")
        try:
            if await self._read() != self._snapshot:
                raise failure("desktop_user_intervened")
            return self._snapshot[0]
        except BaseException:
            self._valid = False
            raise
