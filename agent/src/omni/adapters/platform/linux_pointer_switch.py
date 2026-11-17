"""Precompute and verify one owned pointer slave-class copy; never writes device configuration."""

from __future__ import annotations

import struct
from collections.abc import Awaitable, Callable
from dataclasses import replace

from .linux_bus import failure
from .linux_input import X11Input, device_topology
from .linux_pointer_config import PointerDevice, PointerState, pointer_classes, pointer_devices
from .linux_x11_wire import unpack


def encoded_classes(device: PointerDevice) -> bytes:
    count = len(device.labels)
    buttons = struct.pack("<HHHH", 1, 2 + (count + 31) // 32 + count, device.button_source, count)
    buttons += bytes((count + 31) // 32 * 4) + struct.pack("<" + "I" * count, *device.labels)

    def fixed(value: int) -> bytes:
        return struct.pack("<iI", value >> 32, value & 0xFFFFFFFF)

    axes = b"".join(
        struct.pack("<HHHHI", 2, 11, axis.source, axis.number, axis.label)
        + fixed(axis.minimum)
        + fixed(axis.maximum)
        + bytes(8)
        + struct.pack("<IB3x", axis.resolution, axis.mode)
        for axis in device.axes
    )
    scrolls = b"".join(value[:10] + bytes(2) + value[10:] for value in device.scrolls)
    return buttons + axes + scrolls


class PointerSwitch:
    def __init__(
        self,
        monitor: X11Input,
        state: PointerState,
        data: bytes,
        check_copied: Callable[[PointerState, str], Awaitable[None]],
        adopt: Callable[[PointerState], None],
    ) -> None:
        wire = monitor.wire
        if wire is None or wire.setup is None or monitor.topology is None or wire.changed:
            raise failure("desktop_input_unverified")
        master, source = pointer_devices(data, state.plan, monitor.topology)
        if (master, source) != (state.master, state.source) or len(source.labels) > len(master.labels):
            raise failure("desktop_input_unverified")
        copied = replace(
            master,
            button_source=source.identity,
            labels=source.labels + master.labels[len(source.labels) :],
            axes=source.axes,
            scrolls=source.scrolls,
        )
        controls = state.source_feedback + state.master_feedback[len(state.source_feedback) :]
        if not 1 <= len(controls) <= 8 or len({item[0] for item in controls}) != len(controls):
            raise failure("desktop_input_unverified")
        self.before_state, self.after_state = state, replace(state, master=copied, master_feedback=controls)
        # Preserve every other device/header, including names, attachments,
        # keyboard classes and the master's actual button count/map.
        changed, position = bytearray(data[:32]), 32
        for _ in device_topology(data).entries:
            identity, _kind, _attachment, count, size = unpack("HHHHH", data, position)
            end = position + 12 + (size + 3) // 4 * 4
            header = bytearray(data[position:end])
            start = position = end
            for _ in range(count):
                position += unpack("H", data, position + 2)[0] * 4
            if identity == master.identity:
                struct.pack_into("<H", header, 6, 1 + len(copied.axes) + len(copied.scrolls))
                changed += header + encoded_classes(copied)
            else:
                changed += header + data[start:position]
        struct.pack_into("<I", changed, 4, (len(changed) - 32) // 4)
        self.before, self.after = monitor.topology, device_topology(bytes(changed)).fingerprint
        if pointer_devices(bytes(changed), state.plan, self.after) != (copied, source):
            raise failure("desktop_input_unverified")
        self.monitor, self._check_copied, self._adopt = monitor, check_copied, adopt
        self.stage = 0

    @property
    def complete(self) -> bool:
        # Native last.slave/lastSlave are private; QueryDevice alone cannot
        # determine whether this request will require a class-copy event.
        return self.stage in {0, 1}

    def device(self, header: bytes, payload: bytes, sequence: int) -> bool:
        source = self.before_state.source
        if (
            self.stage != 0
            or unpack("H", header, 2)[0] != sequence
            or unpack("HH", header, 8) != (1, self.before_state.master.identity)
            or unpack("HB", header, 18) != (source.identity, 1)
        ):
            return False
        try:
            value, end = pointer_classes(
                payload, 0, unpack("H", header, 16)[0], source.identity, len(source.labels), {source.identity}
            )
            if end != len(payload) or value != source:
                return False
        except Exception:  # noqa: BLE001 -- an unproven notification is never exempted
            return False
        self.stage = 1
        return True

    async def verify_after(self) -> None:
        monitor = self.monitor
        wire, connection = monitor.wire, monitor.connection
        if (
            wire is None
            or wire.opcode is None
            or connection is None
            or not self.complete
            or wire.changed
            or monitor.topology != self.before
        ):
            raise failure("desktop_input_unverified")
        topology = self.after if self.stage else self.before
        state = self.after_state if self.stage else self.before_state
        await connection.verify()
        for index in range(2):
            data = await wire.request(wire.opcode, struct.pack("<Hxx", 0), 48, maximum=32768)
            if pointer_devices(data, state.plan, topology) != (state.master, state.source):
                raise failure("desktop_user_intervened")
            if index == 0:
                await self._check_copied(state, topology)
        await monitor.check_expected(topology)
        if wire.changed or monitor.topology != self.before:
            raise failure("desktop_user_intervened")
        # Synchronous publication follows all native/configuration guards;
        # no reader can observe a provisional topology and no latch is reset.
        self._adopt(state)
        monitor.topology = topology
