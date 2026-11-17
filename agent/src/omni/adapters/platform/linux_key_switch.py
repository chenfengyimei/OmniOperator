"""Prove the single Xorg keyboard slave switch surrounding an owned key pair; no input or configuration writes."""

from __future__ import annotations

import hashlib
import struct
from collections.abc import Awaitable, Callable

from .linux_bus import failure
from .linux_input import X11Input, device_topology
from .linux_x11_wire import unpack
from .linux_xtest import Sources


class KeySwitch:
    def __init__(
        self,
        monitor: X11Input,
        sources: Sources,
        xkb: tuple[int, int, int],
        data: bytes,
        check_copied: Callable[[], Awaitable[None]],
    ) -> None:
        wire = monitor.wire
        if wire is None or wire.setup is None or monitor.topology is None or wire.changed:
            raise failure("desktop_input_unverified")
        topology = device_topology(data)
        if topology.fingerprint != monitor.topology or xkb[1] != sources.keyboard_master:
            raise failure("desktop_input_unverified")
        classes: dict[int, tuple[int, bytes]] = {}
        position = 32
        for _ in topology.entries:
            identity, _kind, _attachment, count, size = unpack("HHHHH", data, position)
            position += 12 + (size + 3) // 4 * 4
            for _ in range(count):
                category, words = unpack("HH", data, position)
                end = position + words * 4
                if identity in {sources.keyboard_master, sources.keyboard}:
                    if count != 1 or category != 0:
                        raise failure("desktop_input_unverified")
                    classes[identity] = (position, data[position:end])
                position = end
        if set(classes) != {sources.keyboard_master, sources.keyboard}:
            raise failure("desktop_input_unverified")
        offset, master = classes[sources.keyboard_master]
        source = classes[sources.keyboard][1]
        if master[:4] + master[6:] != source[:4] + source[6:] or unpack("H", source, 4)[0] != sources.keyboard:
            raise failure("desktop_input_unverified")
        changed = bytearray(data)
        struct.pack_into("<H", changed, offset + 4, sources.keyboard)
        self.before, self.after = topology.fingerprint, device_topology(bytes(changed)).fingerprint
        self.monitor, self.xkb, self.sources = monitor, xkb, sources
        self.minimum, self.maximum = wire.setup.minimum_keycode, wire.setup.maximum_keycode
        self.source_class = hashlib.sha256(source).digest()
        self._check_copied = check_copied
        self.stage = 0

    @classmethod
    async def prepare(
        cls,
        monitor: X11Input,
        sources: Sources,
        xkb: tuple[int, int, int],
        check_copied: Callable[[], Awaitable[None]],
    ) -> KeySwitch:
        wire = monitor.wire
        if wire is None or wire.opcode is None:
            raise failure("desktop_input_unverified")
        data = await wire.request(wire.opcode, struct.pack("<Hxx", 0), 48, maximum=32768)
        return cls(monitor, sources, xkb, data, check_copied)

    @property
    def complete(self) -> bool:
        # last.slave and lastSlave differ inside Xorg: QueryDevice alone does
        # not establish whether this request must trigger a switch.
        return self.stage in {0, 2}

    def keyboard(self, header: bytes, sequence: int) -> bool:
        if (
            self.stage != 0
            or header[1] != 0
            or unpack("H", header, 2)[0] != sequence
            or header[8:16]
            != bytes([self.xkb[1], self.xkb[1], self.minimum, self.maximum, self.minimum, self.maximum, self.xkb[0], 9])
            or unpack("H", header, 16)[0] != self.xkb[2]
        ):
            return False
        self.stage = 1
        return True

    def device(self, header: bytes, payload: bytes, sequence: int) -> bool:
        if (
            self.stage != 1
            or unpack("H", header, 2)[0] != sequence
            or unpack("H", header, 10)[0] != self.sources.keyboard_master
            or unpack("HHB", header, 16) != (1, self.sources.keyboard, 1)
            or hashlib.sha256(payload).digest() != self.source_class
        ):
            return False
        self.stage = 2
        return True

    async def verify_after(self) -> None:
        monitor = self.monitor
        wire, connection = monitor.wire, monitor.connection
        if wire is None or wire.opcode is None or connection is None or not self.complete or wire.changed:
            raise failure("desktop_input_unverified")
        expected = self.before if self.stage == 0 else self.after
        await connection.verify()
        for index in range(2):
            data = await wire.request(wire.opcode, struct.pack("<Hxx", 0), 48, maximum=32768)
            if device_topology(data).fingerprint != expected:
                raise failure("desktop_user_intervened")
            if index == 0:
                await self._check_copied()
        keys = await wire.request(44)
        if len(keys) != 40 or any(keys[8:40]):
            raise failure("desktop_input_held")
        await connection.verify()
        focus = await wire.request(43)
        if len(focus) != 32 or unpack("I", focus, 8)[0] != wire.focus or wire.changed:
            raise failure("desktop_user_intervened")
        # Adopt only the single precomputed KeyClass.sourceid change after all
        # readbacks. Never clear the monitor's independent intervention latch.
        monitor.topology = expected
