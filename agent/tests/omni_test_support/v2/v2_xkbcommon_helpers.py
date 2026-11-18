"""Injected libxkbcommon C ABI with real ctypes symbol buffers; not a native parser or input implementation."""

import ctypes

SYMBOLS = {
    "Return": 0xFF0D,
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
MINIMAL = b"""xkb_keymap {
xkb_keycodes "fixture" { minimum = 8; maximum = 255; <K038> = 38; };
xkb_types "fixture" { type "ONE_LEVEL" { modifiers = None; level_name[Level1] = "Any"; }; };
xkb_compatibility "fixture" {};
xkb_symbols "fixture" { key <K038> { type = "ONE_LEVEL", [ Return ] }; };
};\0"""


class Function:
    def __init__(self, owner, name):
        self.owner, self.name = owner, name
        self.argtypes = self.restype = None

    def __call__(self, *arguments):
        self.owner.calls.append((self.name, arguments))
        return self.owner.invoke(self.name, arguments)


class Library:
    context, keymap, state = (0x123456780001, 0x123456780002, 0x123456780003)

    def __init__(self, *, key="Return", code=38, fault=None):
        self.calls, self.buffers = [], []
        self.compiled_buffers = []
        self.rows = {code: [SYMBOLS[key]]}
        self.fault, self.direction = fault, None
        self.minimum, self.maximum, self.layouts, self.mods = 8, 255, 1, 0
        self.functions = {}

    def __getattr__(self, name):
        if not name.startswith("xkb_") or name == self.fault:
            raise AttributeError(name)
        if name not in self.functions:
            self.functions[name] = Function(self, name)
        return self.functions[name]

    def invoke(self, name, args):
        if name == "xkb_context_new":
            assert args == (3,)
            return None if self.fault == "context" else self.context
        if name == "xkb_keymap_new_from_buffer":
            context, buffer, size, format, flags = args
            assert context == self.context and format == 1 and flags == 0
            assert ctypes.string_at(buffer, size + 1)[-1:] == b"\0"
            self.compiled_buffers.append(ctypes.string_at(buffer, size))
            assert [entry[0] for entry in self.calls].index("xkb_context_include_path_clear") < len(self.calls) - 1
            return None if self.fault == "parse" else self.keymap
        if name == "xkb_keymap_min_keycode":
            return self.minimum
        if name == "xkb_keymap_max_keycode":
            return self.maximum
        if name == "xkb_keymap_num_layouts":
            return self.layouts
        if name == "xkb_keymap_num_mods":
            return self.mods
        if name == "xkb_state_new":
            return None if self.fault == "state" else self.state
        if name == "xkb_state_update_mask":
            assert args == (self.state, 0, 0, 0, 0, 0, 0)
            return 1 if self.fault == "initial_mask" else 0
        if name == "xkb_keysym_from_name":
            assert args[1] == 0
            return 0 if self.fault == "symbol" else SYMBOLS[args[0].decode()]
        if name in {"xkb_state_serialize_mods", "xkb_state_serialize_layout"}:
            if self.fault == "initial_state" or self.fault == "silent_state" and self.direction == 1:
                return 1
            return 0
        if name == "xkb_state_key_get_syms":
            state, code, output = args
            assert state == self.state
            if self.fault == "count":
                return 17
            row = self.rows.get(code, [])
            if self.fault == "after_down" and self.direction == 1:
                row = [SYMBOLS["Escape"]]
            if self.fault == "null" and row:
                return 1
            values = (ctypes.c_uint32 * len(row))(*row)
            self.buffers.append(values)
            ctypes.cast(output, ctypes.POINTER(ctypes.POINTER(ctypes.c_uint32)))[0] = ctypes.cast(
                values, ctypes.POINTER(ctypes.c_uint32)
            )
            return len(row)
        if name == "xkb_state_update_key":
            state, code, self.direction = args
            assert state == self.state and code in self.rows
            return (
                1
                if self.fault == "down_effect"
                and self.direction == 1
                or self.fault == "up_effect"
                and self.direction == 0
                else 0
            )
        if name.endswith("_unref") and self.fault == "cleanup":
            raise OSError("private-canary")
        return None

    def released(self):
        return [name for name, args in self.calls if name.endswith("_unref")]
