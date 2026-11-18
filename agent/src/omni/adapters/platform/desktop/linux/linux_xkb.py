"""Read-only XKB server state/actions on the existing input connection; no layout mutation, device acquisition or event dispatch."""

from __future__ import annotations

import hashlib
import struct
from typing import cast

from omni.adapters.platform.desktop.linux.linux_bus import failure
from omni.adapters.platform.desktop.linux.linux_input import InputWire
from omni.adapters.platform.desktop.linux.linux_x11_wire import padded, unpack
from omni.adapters.platform.desktop.linux.linux_xkb_copy import compatibility, geometry, indicators, names
from omni.adapters.platform.desktop.linux.linux_xkb_map import map_identity

CORE_KEYBOARD = 0x100
MAP_PARTS = 0xBE  # symbols, actions, behaviors, explicit, real/virtual modifier maps
EVENTS = 0xEF  # new keyboard, map, state, controls, indicator map, names, compatibility
MAX_SYMBOLS = 64
UNSUPPORTED_CONTROLS = 2 | 4 | 8 | 16 | 1024 | 2048


async def connection(wire: InputWire) -> int:
    """Negotiate this connection without replacing existing device subscriptions or clearing changes."""
    name = b"XKEYBOARD"
    extension = await wire.request(98, struct.pack("<Hxx", len(name)) + padded(name))
    if (
        len(extension) != 32
        or extension[8] != 1
        or extension[9] < 128
        or extension[9] == wire.opcode
        or not 64 <= extension[10] < 128
        or wire.xkb_event is not None
        and wire.xkb_event != extension[10]
        or extension[10] == wire.button_mapping_event
    ):
        raise failure("desktop_input_unverified")
    opcode = extension[9]
    version = await wire.request(opcode, struct.pack("<HH", 1, 0))
    if len(version) != 32 or version[1] != 1 or unpack("HH", version, 8) != (1, 0):
        raise failure("desktop_input_unverified")
    wire.xkb_event = extension[10]
    return opcode


class XkbKeyboard:
    def __init__(self, wire: InputWire) -> None:
        self.wire = wire
        self._attempted = False
        self._ready = False
        self._opcode: int | None = None
        self._device: int | None = None
        self._source: int | None = None
        self._geometry: bool | None = None

    @property
    def transition_info(self) -> tuple[int, int, int]:
        if not self._ready or self._opcode is None or self._device is None or self._geometry is None:
            raise failure("desktop_input_unverified")
        return self._opcode, self._device, 3 if self._geometry else 1

    def _reply(self, data: bytes, size: int | None = None, *, device: int | None = None) -> None:
        expected = self._device if device is None else device
        if (
            len(data) < 32
            or size is not None
            and len(data) != size
            or not 2 <= data[1] <= 255
            or expected is not None
            and data[1] != expected
        ):
            raise failure("desktop_input_unverified")
        if device is None:
            self._device = data[1]

    async def open(self) -> None:
        if self._attempted:
            raise failure("desktop_input_unverified")
        self._attempted = True
        self._opcode = await connection(self.wire)
        # Install the event parser before subscribing. SelectAll includes every
        # detail of these event families, without optional trailing masks.
        await self.wire.send(self._opcode, struct.pack("<6H", CORE_KEYBOARD, EVENTS, 0, EVENTS, 0xFF, 0xFF), 1)
        await self._state()
        if self.wire.changed:
            raise failure("desktop_user_intervened")
        self._ready = True

    async def pointer_environment(self, keyboard: int) -> tuple[int, bytes]:
        """Read released core XKB state/controls for a verified pointer pair; no key mapping is implied."""
        if not self._ready or self._opcode is None or self._device != keyboard:
            raise failure("desktop_input_unverified")
        try:
            state, controls = await self._state(), await self._controls()
            if await self._state() != state or await self._controls() != controls or self.wire.changed:
                raise failure("desktop_user_intervened")
            return self._opcode, hashlib.sha256(state + controls).digest()
        except BaseException:
            self._ready = False
            raise

    async def _state(self, device: int = CORE_KEYBOARD) -> bytes:
        if self._opcode is None:
            raise failure("desktop_input_unverified")
        state = await self.wire.request(self._opcode, struct.pack("<Hxx", device), 4)
        self._reply(state, 32, device=None if device == CORE_KEYBOARD else device)
        # Physical release alone does not prove the effective XKB state. No
        # latched/locked groups, real/compatibility/lookup modifiers or buttons.
        if any(state[8:23]) or any(state[24:26]):
            raise failure("desktop_input_held")
        return state[8:23] + state[24:26]

    async def _controls(self, device: int = CORE_KEYBOARD) -> bytes:
        assert self._opcode is not None
        value = await self.wire.request(self._opcode, struct.pack("<Hxx", device), 6, maximum=60)
        self._reply(value, 92, device=None if device == CORE_KEYBOARD else device)
        enabled = unpack("I", value, 56)[0]
        if (
            not 1 <= value[9] <= 4
            or any(value[11:15])
            or any(value[16:20])
            or enabled & ~0x1FFF
            or enabled & UNSUPPORTED_CONTROLS
        ):
            raise failure("desktop_input_unverified")
        return value[8:15] + value[16:46] + value[48:92]

    async def _map(self, code: int, symbol: int) -> bytes:
        assert self._opcode is not None and self.wire.setup is not None
        # Each partial range names exactly the approved candidate. Full maps,
        # arbitrary virtual-modifier requests and mutations are not accepted.
        request = struct.pack(
            "<HHH8BH6Bxx", CORE_KEYBOARD, 0, MAP_PARTS, 0, 0, code, 1, code, 1, code, 1, 0, code, 1, code, 1, code, 1
        )
        data = await self.wire.request(self._opcode, request, 8, maximum=1024)
        self._reply(data)
        if len(data) < 40 or tuple(data[10:12]) != (self.wire.setup.minimum_keycode, self.wire.setup.maximum_keycode):
            raise failure("desktop_input_unverified")
        present, symbols, actions = unpack("H", data, 12)[0], unpack("H", data, 18)[0], unpack("H", data, 22)[0]
        if (
            present != MAP_PARTS
            or (data[17], data[20], data[21], data[24], data[25], data[26]) != (code, 1, code, 1, code, 1)
            or (data[28], data[29], data[31], data[32], data[34], data[35]) != (code, 1, code, 1, code, 1)
            or any(data[index] for index in (27, 33, 36))  # no nondefault behavior or real/virtual modifier binding
            or data[30] > 1
            or not 1 <= symbols <= MAX_SYMBOLS
            or actions not in {0, symbols}
        ):
            raise failure("desktop_input_unverified")
        position = 40
        group_info, width, count = cast(tuple[int, int, int], unpack("BBH", data, position + 4))
        groups = group_info & 0x0F
        if (
            not 1 <= groups <= 4
            or not 1 <= width <= 16
            or group_info & 0xC0 == 0xC0
            or count != symbols
            or count != groups * width
        ):
            raise failure("desktop_input_unverified")
        position += 8
        values = unpack("I" * count, data, position)
        if any(value != symbol for value in values):
            raise failure("desktop_input_unverified")
        position += count * 4
        if position + 4 > len(data) or data[position] != actions:
            raise failure("desktop_input_unverified")
        position += 4  # one count byte followed by protocol alignment, never hash unused padding
        if position + actions * 8 > len(data) or any(data[position + offset * 8] != 0 for offset in range(actions)):
            raise failure("desktop_input_unverified")
        position += actions * 8
        explicit = b""
        if data[30]:
            if position + 4 > len(data) or data[position] != code:
                raise failure("desktop_input_unverified")
            explicit = data[position : position + 2]
            position += 4  # one two-byte explicit entry with alignment
        if position != len(data):
            raise failure("desktop_input_unverified")
        # All reported symbol positions agree, so no key-type/group lookup or
        # client-specific translation is inferred. Only NoAction is admitted.
        return (
            bytes([data[1], code, groups, width, actions, group_info])
            + data[40 : 40 + groups]
            + data[48 : 48 + count * 4]
            + explicit
        )

    async def read_key(self, code: int, symbol: int) -> bytes:
        if (
            not self._ready
            or self.wire.setup is None
            or not self.wire.setup.minimum_keycode <= code <= self.wire.setup.maximum_keycode
        ):
            raise failure("desktop_input_unverified")
        try:
            state = await self._state()
            controls = await self._controls()
            mapping = await self._map(code, symbol)
            if await self._state() != state or await self._controls() != controls or self.wire.changed:
                raise failure("desktop_user_intervened")
            return hashlib.sha256(state + controls + mapping).digest()
        except BaseException:
            self._ready = False
            raise

    async def source_identity(self, device: int) -> bytes:
        """Prove a source's full map and controls equal the core keyboard before Xorg can pivot to it."""
        if (
            not self._ready
            or self._opcode is None
            or self._device is None
            or self.wire.setup is None
            or not 2 <= device <= 255
            or device == self._device
            or self._source is not None
            and self._source != device
        ):
            raise failure("desktop_input_unverified")
        try:
            if self._source is None:
                self._source = device
                await self.wire.send(self._opcode, struct.pack("<6H", device, EVENTS, 0, EVENTS, 0xFF, 0xFF), 1)
            identities: list[bytes] = []
            for selected in (CORE_KEYBOARD, device, CORE_KEYBOARD, device):
                state = await self._state(selected)
                controls = await self._controls(selected)
                request = struct.pack("<HHH8BH6Bxx", selected, 0xFF, 0, *([0] * 15))
                mapping = await self.wire.request(self._opcode, request, 8, maximum=65536)
                actual = self._device if selected == CORE_KEYBOARD else device
                identity = map_identity(
                    mapping, actual, self.wire.setup.minimum_keycode, self.wire.setup.maximum_keycode
                )
                compat = compatibility(
                    await self.wire.request(self._opcode, struct.pack("<HBBHH", selected, 15, 1, 0, 0), 10), actual
                )
                leds = indicators(
                    await self.wire.request(self._opcode, struct.pack("<HxxI", selected, 0xFFFFFFFF), 13), actual
                )
                label_reply = await self.wire.request(self._opcode, struct.pack("<HxxI", selected, 0x3FFF), 17)
                labels = names(
                    label_reply,
                    actual,
                    self.wire.setup.minimum_keycode,
                    self.wire.setup.maximum_keycode,
                )
                if unpack("I", label_reply, 8)[0] & 63 != 63:
                    raise failure("desktop_input_unverified")
                shape_reply = await self.wire.request(self._opcode, struct.pack("<HxxI", selected, 0), 19)
                shape = geometry(shape_reply, actual)
                if (
                    await self._state(selected) != state
                    or await self._controls(selected) != controls
                    or self.wire.changed
                ):
                    raise failure("desktop_user_intervened")
                identities.append(hashlib.sha256(state + controls + identity + compat + leds + labels + shape).digest())
            if any(identity != identities[0] for identity in identities):
                raise failure("desktop_input_unverified")
            self._geometry = bool(shape_reply[12])
            return identities[0]
        except BaseException:
            self._ready = False
            raise
