"""Read-only Xorg XTEST device identity proof; no input dispatch or own-event exemption."""

from __future__ import annotations

import struct
from dataclasses import dataclass

from .linux_bus import failure
from .linux_input import X11Input, device_topology
from .linux_x11_wire import padded, unpack


@dataclass(frozen=True)
class Sources:
    pointer_master: int
    keyboard_master: int
    pointer: int
    keyboard: int


class XTestSources:
    """Borrow one armed monitor; all XTEST clients share these sources, so identity is not attribution."""

    def __init__(self, monitor: X11Input) -> None:
        self.monitor = monitor
        self._attempted = self._valid = False
        self._atom: int | None = None
        self._sources: Sources | None = None
        self._opcode: int | None = None

    @property
    def opcode(self) -> int:
        if not self._valid or self._opcode is None:
            raise failure("desktop_input_unverified")
        return self._opcode

    async def _read(self) -> Sources:
        wire = self.monitor.wire
        if wire is None or wire.opcode is None or self._atom is None:
            raise failure("desktop_input_unverified")
        data = await wire.request(wire.opcode, struct.pack("<Hxx", 0), 48, maximum=32768)
        topology = device_topology(data)
        if topology.fingerprint != self.monitor.topology:
            raise failure("desktop_user_intervened")
        masters = {device.kind: device.identity for device in topology.entries if device.kind in {1, 2}}
        found: dict[int, int] = {}
        # Query every device, including disabled/floating devices. Checking only
        # a named or attached candidate lets a forged property hide a real pair.
        for device in topology.entries:
            reply = await wire.request(
                wire.opcode,
                struct.pack("<HBxIIII", device.identity, 0, self._atom, 0, 0, 1),
                59,
                maximum=4,
            )
            kind, remaining, count = unpack("III", reply, 8)
            if (kind, remaining, count, reply[20], len(reply)) == (0, 0, 0, 0, 32):
                continue
            if (kind, remaining, count, reply[20], len(reply)) != (19, 0, 1, 8, 36) or reply[32] != 1:
                raise failure("desktop_input_unverified")
            if (
                device.kind not in {3, 4}
                or not device.enabled
                or device.kind in found
                or device.attachment != masters[device.kind - 2]
                or (1 if device.kind == 3 else 0) not in device.classes
            ):
                raise failure("desktop_input_unverified")
            found[device.kind] = device.identity
        if set(found) != {3, 4}:
            raise failure("desktop_input_unverified")
        return Sources(masters[1], masters[2], found[3], found[4])

    async def prepare(self) -> Sources:
        if self._attempted:
            raise failure("desktop_input_unverified")
        self._attempted = True
        try:
            if not self.monitor.armed:
                raise failure("desktop_input_unverified")
            await self.monitor.check()
            wire = self.monitor.wire
            if wire is None or wire.opcode is None:
                raise failure("desktop_input_unverified")
            opcode = await wire.extension(b"XTEST")
            if opcode == wire.opcode:
                raise failure("desktop_input_unverified")
            version = await wire.request(opcode, struct.pack("<BxH", 2, 2), maximum=0)
            if len(version) != 32 or version[1] != 2 or unpack("H", version, 8)[0] < 2:
                raise failure("desktop_input_unverified")
            name = b"XTEST Device"
            atom = await wire.request(16, struct.pack("<Hxx", len(name)) + padded(name), 1, maximum=0)
            if len(atom) != 32 or not unpack("I", atom, 8)[0]:
                raise failure("desktop_input_unverified")
            self._atom = unpack("I", atom, 8)[0]
            original = await self._read()
            await self.monitor.check()
            if await self._read() != original:
                raise failure("desktop_user_intervened")
            await self.monitor.check()
            self._sources, self._opcode, self._valid = original, opcode, True
            return original
        except BaseException:
            self._valid = False
            raise

    async def verify(self) -> Sources:
        if not self._valid or self._sources is None:
            raise failure("desktop_input_unverified")
        try:
            await self.monitor.check()
            if await self._read() != self._sources:
                raise failure("desktop_user_intervened")
            await self.monitor.check()
            return self._sources
        except BaseException:
            self._valid = False
            raise

    async def read_current(self) -> Sources:
        """Leaf comparison for a caller holding the complete native read-only guard."""
        if not self._valid or self._sources is None:
            raise failure("desktop_input_unverified")
        try:
            if await self._read() != self._sources:
                raise failure("desktop_user_intervened")
            return self._sources
        except BaseException:
            self._valid = False
            raise
