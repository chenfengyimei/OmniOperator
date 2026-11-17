"""Owned libxkbcommon buffer compilation and special-key interpretation; no default layout, includes or native input."""

from __future__ import annotations

import asyncio
import ctypes
import hashlib
import sys
from collections.abc import Callable
from dataclasses import dataclass
from typing import Any

from ...domain.contracts import BusinessError
from ...domain.foreground import KEYS
from .linux_bus import failure
from .linux_eis_keymap import MAX_KEYMAP, KeymapInfo


@dataclass(frozen=True)
class KeyMeaning:
    key: str
    keysym: int
    xkb_code: int
    evdev_code: int
    keymap: KeymapInfo


class NativeXkb:
    def __init__(self, *, loader: Callable[[str], Any] | None = None) -> None:
        if loader is None and sys.platform != "linux":
            raise failure("portal_xkb_unavailable")
        try:
            self.library = (loader or ctypes.CDLL)("libxkbcommon.so.0")
            pointer, number = ctypes.c_void_p, ctypes.c_uint32
            signatures: dict[str, tuple[list[Any], Any]] = {
                "xkb_context_new": ([ctypes.c_int], pointer),
                "xkb_context_include_path_clear": ([pointer], None),
                "xkb_context_set_log_level": ([pointer, ctypes.c_int], None),
                "xkb_context_unref": ([pointer], None),
                "xkb_keymap_new_from_buffer": (
                    [pointer, pointer, ctypes.c_size_t, ctypes.c_int, ctypes.c_int],
                    pointer,
                ),
                "xkb_keymap_unref": ([pointer], None),
                "xkb_keymap_min_keycode": ([pointer], number),
                "xkb_keymap_max_keycode": ([pointer], number),
                "xkb_keymap_num_layouts": ([pointer], number),
                "xkb_keymap_num_mods": ([pointer], number),
                "xkb_state_new": ([pointer], pointer),
                "xkb_state_unref": ([pointer], None),
                "xkb_state_update_mask": ([pointer, *([number] * 6)], number),
                "xkb_state_update_key": ([pointer, number, ctypes.c_int], number),
                "xkb_state_serialize_mods": ([pointer, ctypes.c_int], number),
                "xkb_state_serialize_layout": ([pointer, ctypes.c_int], number),
                "xkb_state_key_get_syms": ([pointer, number, ctypes.POINTER(ctypes.POINTER(number))], ctypes.c_int),
                "xkb_keysym_from_name": ([ctypes.c_char_p, ctypes.c_int], number),
            }
            for name, (arguments, result) in signatures.items():
                function = getattr(self.library, name)
                function.argtypes, function.restype = arguments, result
        except (AttributeError, OSError, TypeError, ValueError):
            raise failure("portal_xkb_unavailable") from None

    def _neutral(self, state: int) -> None:
        if any(self.library.xkb_state_serialize_mods(state, flag) != 0 for flag in (1, 2, 4, 8)) or any(
            self.library.xkb_state_serialize_layout(state, flag) != 0 for flag in (16, 32, 64, 128)
        ):
            raise failure("portal_xkb_state_unverified")

    def _symbols(self, state: int, code: int) -> tuple[int, ...]:
        values = ctypes.POINTER(ctypes.c_uint32)()
        count = self.library.xkb_state_key_get_syms(state, code, ctypes.byref(values))
        if type(count) is not int or not 0 <= count <= 16 or count and not values:
            raise failure("portal_xkb_keymap_invalid")
        return tuple(values[index] for index in range(count))

    def interpret(self, data: bytes, info: KeymapInfo, key: str) -> KeyMeaning:
        if (
            key not in KEYS
            or not 1 <= len(data) <= MAX_KEYMAP
            or len(data) != info.size
            or hashlib.sha256(data).hexdigest() != info.sha256
            or data[-1:] != b"\0"
            or b"\0" in data[:-1]
        ):
            raise failure("portal_xkb_keymap_invalid")
        context = keymap = state = None
        try:
            # Compile the received self-contained map only. Never consult
            # RMLVO/environment defaults or the host's keyboard include paths.
            context = self.library.xkb_context_new(3)
            if not context:
                raise failure("portal_xkb_unavailable")
            self.library.xkb_context_include_path_clear(context)
            self.library.xkb_context_set_log_level(context, 10)
            buffer = ctypes.create_string_buffer(data, len(data))
            keymap = self.library.xkb_keymap_new_from_buffer(context, buffer, len(data) - 1, 1, 0)
            if not keymap:
                raise failure("portal_xkb_keymap_invalid")
            minimum, maximum = self.library.xkb_keymap_min_keycode(keymap), self.library.xkb_keymap_max_keycode(keymap)
            if (
                not 8 <= minimum <= maximum <= 775
                or not 1 <= self.library.xkb_keymap_num_layouts(keymap) <= 16
                or not 0 <= self.library.xkb_keymap_num_mods(keymap) <= 32
            ):
                raise failure("portal_xkb_keymap_invalid")
            state = self.library.xkb_state_new(keymap)
            if not state:
                raise failure("portal_xkb_unavailable")
            if self.library.xkb_state_update_mask(state, 0, 0, 0, 0, 0, 0):
                raise failure("portal_xkb_state_unverified")
            self._neutral(state)
            symbol = self.library.xkb_keysym_from_name(("Return" if key == "Enter" else key).encode("ascii"), 0)
            if not symbol:
                raise failure("portal_xkb_key_unavailable")
            candidates = [code for code in range(minimum, maximum + 1) if self._symbols(state, code) == (symbol,)]
            if len(candidates) != 1 or candidates[0] < 9:
                raise failure("portal_xkb_key_unavailable")
            code = candidates[0]
            for direction in (1, 0):
                if self.library.xkb_state_update_key(state, code, direction):
                    raise failure("portal_xkb_state_unverified")
                self._neutral(state)
                if self._symbols(state, code) != (symbol,):
                    raise failure("portal_xkb_state_unverified")
            # EI uses Linux evdev codes; libxkbcommon's documented evdev XKB
            # convention adds8. No key name is assigned a fixed US keycode.
            return KeyMeaning(key, int(symbol), code, code - 8, info)
        finally:
            clean = True
            for handle, release in (
                (state, self.library.xkb_state_unref),
                (keymap, self.library.xkb_keymap_unref),
                (context, self.library.xkb_context_unref),
            ):
                if handle:
                    try:
                        release(handle)
                    except Exception:  # noqa: BLE001 -- still dispose the other native handles after one release fails
                        clean = False
            if not clean:
                raise failure("portal_xkb_cleanup_pending")


class XkbCompiler:
    def __init__(self, factory: Callable[[], NativeXkb] = NativeXkb) -> None:
        self.factory = factory
        self.task: asyncio.Task[KeyMeaning] | None = None
        self.closed = False

    async def _compile(self, data: bytes, info: KeymapInfo, key: str) -> KeyMeaning:
        work = asyncio.get_running_loop().run_in_executor(None, lambda: self.factory().interpret(data, info, key))
        cancelled = False
        while True:
            try:
                result = await asyncio.shield(work)
                break
            except asyncio.CancelledError:
                cancelled = True
        if cancelled:
            raise asyncio.CancelledError
        if self.closed:
            raise failure("portal_xkb_revoked")
        return result

    async def compile(self, data: bytes, info: KeymapInfo, key: str) -> KeyMeaning:
        if self.task is not None or self.closed:
            raise failure("portal_state_invalid")
        self.task = asyncio.create_task(self._compile(data, info, key), name="ei-owned-xkb-compile")
        self.task.add_done_callback(lambda task: task.exception() if not task.cancelled() else None)
        return await asyncio.shield(self.task)

    async def close(self) -> bool:
        self.closed = True
        if self.task is None:
            return True
        if not self.task.done():
            return False
        if self.task.cancelled():
            return True
        error = self.task.exception()
        # Keep the original task/exception and native release obligations.
        # Retrying an ambiguous void unref could release the same object twice.
        return not isinstance(error, BusinessError) or error.code != "portal_xkb_cleanup_pending"
