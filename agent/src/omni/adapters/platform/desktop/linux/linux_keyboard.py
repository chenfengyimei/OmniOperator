"""Bounded read-only core keymap preflight on an existing focus proof; never translates Unicode, changes layouts or sends input."""

from __future__ import annotations

import hashlib
import struct
from typing import Protocol

from omni.adapters.platform.desktop.linux.linux_bus import failure
from omni.adapters.platform.desktop.linux.linux_input import X11Input
from omni.adapters.platform.desktop.linux.linux_key_switch import KeySwitch
from omni.adapters.platform.desktop.linux.linux_x11_wire import unpack
from omni.adapters.platform.desktop.linux.linux_xkb import XkbKeyboard
from omni.adapters.platform.desktop.linux.linux_xtest import Sources

SYMBOLS = {
    "Enter": 0xFF0D,
    "Tab": 0xFF09,
    "Escape": 0xFF1B,
    "Backspace": 0xFF08,
    "Delete": 0xFFFF,
    "Home": 0xFF50,
    "End": 0xFF57,
    "Left": 0xFF51,
    "Right": 0xFF53,
    "Up": 0xFF52,
    "Down": 0xFF54,
}
MAX_SYMBOLS = 16


class FocusPort(Protocol):
    monitor: X11Input

    async def check(self) -> None: ...


class KeyboardMapping:
    def __init__(self, focus: FocusPort) -> None:
        self.focus = focus
        self._attempted = False
        self._source: tuple[int, bytes] | None = None
        self._valid = False
        self._key: str | None = None
        self._code: int | None = None
        self._fingerprint: bytes | None = None
        self._xkb: XkbKeyboard | None = None

    async def _read(self, key: str) -> tuple[int, bytes]:
        wire = self.focus.monitor.wire
        if wire is None or wire.setup is None or not self.focus.monitor.armed:
            raise failure("desktop_input_unverified")
        minimum, maximum = wire.setup.minimum_keycode, wire.setup.maximum_keycode
        count = maximum - minimum + 1
        mapping = await wire.request(101, struct.pack("<BBxx", minimum, count), maximum=count * MAX_SYMBOLS * 4)
        stride = mapping[1]
        if not 1 <= stride <= MAX_SYMBOLS or len(mapping) != 32 + count * stride * 4:
            raise failure("desktop_input_unverified")
        rows = [unpack("I" * stride, mapping, 32 + offset * stride * 4) for offset in range(count)]
        symbol = SYMBOLS[key]
        candidates = [minimum + offset for offset, row in enumerate(rows) if symbol in row]
        if len(candidates) != 1:
            raise failure("desktop_input_unverified")
        code = candidates[0]
        selected = rows[code - minimum]
        # No shifted-only choice, group choice, modifier chord or guessed US
        # keycode. The core row is not a proof of an application's XKB actions.
        if selected[0] != symbol or any(item not in {0, symbol} for item in selected):
            raise failure("desktop_input_unverified")
        modifiers = await wire.request(119, maximum=8 * 32)
        slots = modifiers[1]
        if slots > 32 or len(modifiers) != 32 + slots * 8:
            raise failure("desktop_input_unverified")
        assigned = [item for item in modifiers[32:] if item]
        if (
            any(not minimum <= item <= maximum for item in assigned)
            or len(set(assigned)) != len(assigned)
            or code in assigned
        ):
            raise failure("desktop_input_unverified")
        pointer = await wire.request(38, struct.pack("<I", wire.setup.root))
        if len(pointer) != 32 or pointer[1] != 1 or unpack("I", pointer, 8)[0] != wire.setup.root:
            raise failure("desktop_input_unverified")
        # Released physical keys do not imply released logical lock/latch state.
        # Only the mask is inspected; pointer coordinates are not retained.
        if unpack("H", pointer, 24)[0]:
            raise failure("desktop_input_held")
        if self._xkb is None:
            raise failure("desktop_input_unverified")
        server = await self._xkb.read_key(code, symbol)
        fingerprint = hashlib.sha256(
            bytes([minimum, maximum, stride, slots]) + mapping[32:] + modifiers[32:] + server
        ).digest()
        return code, fingerprint

    async def prepare(self, key: object) -> int:
        if self._attempted:
            raise failure("desktop_input_unverified")
        self._attempted = True
        if not isinstance(key, str) or key not in SYMBOLS:
            raise failure("desktop_input_unverified")
        self._key = key
        try:
            await self.focus.check()
            if self.focus.monitor.wire is None:
                raise failure("desktop_input_unverified")
            self._xkb = XkbKeyboard(self.focus.monitor.wire)
            await self._xkb.open()
            original = await self._read(key)
            await self.focus.check()
            if await self._read(key) != original:
                raise failure("desktop_user_intervened")
            await self.focus.check()
            self._code, self._fingerprint = original
            self._valid = True
            return self._code
        except BaseException:
            self._valid = False
            raise

    async def verify(self) -> int:
        if not self._valid or self._key is None or self._code is None or self._fingerprint is None:
            raise failure("desktop_input_unverified")
        try:
            await self.focus.check()
            if await self._read(self._key) != (self._code, self._fingerprint):
                raise failure("desktop_user_intervened")
            await self.focus.check()
            return self._code
        except BaseException:
            self._valid = False
            raise

    async def verify_source(self, device: int) -> None:
        if not self._valid or self._xkb is None:
            raise failure("desktop_input_unverified")
        try:
            await self.focus.check()
            identity = (device, await self._xkb.source_identity(device))
            if self._source is not None and identity != self._source:
                raise failure("desktop_user_intervened")
            await self.verify()
            self._source = identity
        except BaseException:
            self._valid = False
            raise

    async def _verify_copied(self, device: int) -> None:
        if not self._valid or self._xkb is None or self._source is None:
            raise failure("desktop_input_unverified")
        try:
            if (device, await self._xkb.source_identity(device)) != self._source:
                raise failure("desktop_user_intervened")
        except BaseException:
            self._valid = False
            raise

    async def switch_proof(self, sources: Sources) -> KeySwitch:
        await self.verify_source(sources.keyboard)
        assert self._xkb is not None

        async def copied() -> None:
            await self._verify_copied(sources.keyboard)

        return await KeySwitch.prepare(self.focus.monitor, sources, self._xkb.transition_info, copied)
