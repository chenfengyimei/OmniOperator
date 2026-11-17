"""Read XKB pointer actions and released keyboard state on a mapped native point; never moves, clicks or grants input."""

from __future__ import annotations

import hashlib
import struct
from typing import Protocol, cast

from .linux_bus import failure
from .linux_button_mapping import ButtonFocus, ButtonPlan
from .linux_x11_wire import unpack
from .linux_xkb import XkbKeyboard
from .linux_xtest import Sources

DEVICE_NOTIFY = 1 << 11
BUTTON_ACTIONS = 2


class PointerFocus(ButtonFocus, Protocol):
    async def verify_button(self) -> ButtonPlan: ...


def action_identity(data: bytes, device: int, count: int) -> bytes:
    """Normalize omitted/explicit NoAction and protocol padding; all real button actions deny this path."""
    if (
        not 2 <= device <= 255
        or not 1 <= count <= 255
        or not 36 <= len(data) <= 4128
        or data[0] != 1
        or data[1] != device
        or len(data) != 32 + unpack("I", data, 4)[0] * 4
        or unpack("H", data, 8)[0] != BUTTON_ACTIONS
        or not unpack("H", data, 10)[0] & BUTTON_ACTIONS
        or unpack("H", data, 10)[0] & ~31
        or unpack("HH", data, 12) != (0, 0)
        or data[16] != 0
        or data[17] != count
        or data[20] != count
        or data[21] != 0
    ):
        raise failure("desktop_input_unverified")
    first, returned, size = data[18], data[19], cast(int, unpack("H", data, 32)[0])
    position = 32 + (size + 2 + 3) // 4 * 4
    if (
        first + returned > count
        or not 1 <= size <= 1024
        or position + returned * 8 != len(data)
        or any(data[position + index * 8] for index in range(returned))
    ):
        raise failure("desktop_input_unverified")
    # GetDeviceInfo trims leading/trailing NoAction records. A null action
    # array and an allocated all-NoAction array therefore have different
    # serialized ranges but the same effective configuration.
    return hashlib.sha256(
        bytes([device]) + data[8:16] + data[20:26] + data[28:32] + data[34 : 34 + size] + bytes(count)
    ).digest()


class PointerActions:
    def __init__(self, focus: PointerFocus) -> None:
        self.focus = focus
        self._attempted = self._valid = False
        self._plan: ButtonPlan | None = None
        self._sources: Sources | None = None
        self._keyboard: XkbKeyboard | None = None
        self._snapshot: bytes | None = None

    async def _guard(self) -> None:
        if await self.focus.verify_button() != self._plan:
            raise failure("desktop_user_intervened")

    async def _read(self) -> bytes:
        wire, plan, sources, keyboard = self.focus.monitor.wire, self._plan, self._sources, self._keyboard
        if wire is None or plan is None or sources is None or keyboard is None:
            raise failure("desktop_input_unverified")
        opcode, state = await keyboard.pointer_environment(sources.keyboard_master)
        parts = [state]
        for device, count in ((plan.master, plan.master_buttons), (plan.source, plan.source_buttons)):
            data = await wire.request(
                opcode, struct.pack("<HHBBBBHH", device, BUTTON_ACTIONS, 1, 0, 0, 0, 0, 0), 24, maximum=4096
            )
            parts.append(action_identity(data, device, count))
        if await keyboard.pointer_environment(sources.keyboard_master) != (opcode, state):
            raise failure("desktop_user_intervened")
        return hashlib.sha256(b"".join(parts)).digest()

    async def prepare(self) -> ButtonPlan:
        if self._attempted:
            raise failure("desktop_input_unverified")
        self._attempted = True
        try:
            self._plan = await self.focus.verify_button()
            self._sources = await self.focus.verify_sources()
            wire = self.focus.monitor.wire
            if wire is None or wire.xkb_pointer_devices:
                raise failure("desktop_input_unverified")
            self._keyboard = XkbKeyboard(wire)
            await self._keyboard.open()
            opcode, _state = await self._keyboard.pointer_environment(self._sources.keyboard_master)
            # Bind the parser before either void request. Select only this
            # family on each pointer; existing keyboard masks remain intact.
            wire.xkb_pointer_devices = frozenset({self._plan.master, self._plan.source})
            for device in (self._plan.master, self._plan.source):
                await wire.send(opcode, struct.pack("<6H", device, DEVICE_NOTIFY, 0, DEVICE_NOTIFY, 0, 0), 1)
            await self._guard()
            original = await self._read()
            await self._guard()
            if await self._read() != original:
                raise failure("desktop_user_intervened")
            await self._guard()
            self._snapshot, self._valid = original, True
            return self._plan
        except BaseException:
            self._valid = False
            raise

    async def verify(self) -> ButtonPlan:
        if not self._valid or self._snapshot is None or self._plan is None:
            raise failure("desktop_input_unverified")
        try:
            await self._guard()
            if await self._read() != self._snapshot:
                raise failure("desktop_user_intervened")
            await self._guard()
            return self._plan
        except BaseException:
            self._valid = False
            raise
