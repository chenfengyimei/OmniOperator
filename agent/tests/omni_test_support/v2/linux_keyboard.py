"""Reusable linux_keyboard fixtures, extracted without collecting test cases."""

import asyncio
import struct

import pytest

from omni.adapters.platform.desktop.linux.linux_keyboard import SYMBOLS
from omni.adapters.platform.desktop.linux.linux_x11_wire import setup_reply
from omni.domain.contracts import BusinessError
from omni.domain.foreground import KEYS
from omni_test_support.v2.linux_foreground import FocusPeer, prepared
from omni_test_support.v2.v2_x11_helpers import ROOT, reply, setup_bytes
from omni_test_support.v2.v2_xkb_helpers import XkbFixture


class KeyboardPeer(FocusPeer):
    def __init__(self):
        super().__init__()
        self.rows = {40 + index: (symbol, symbol, 0, 0) for index, symbol in enumerate(SYMBOLS.values())}
        self.modifiers = bytes([60, 0, 61, 0, 62, 0, 63, 0])
        self.key_stride = 4
        self.modifier_stride = 1
        self.logical_mask = 0
        self.pointer_root = ROOT
        self.same_screen = 1
        self.map_extra = b""
        self.on_read = None
        self.xkb = XkbFixture()

    async def sock_sendall(self, connection, packet):
        if self.authenticated and (packet[0] == 131 or packet[0] == 98 and b"XKEYBOARD" in packet):
            return await self.xkb.respond(self, packet)
        if not self.authenticated or packet[0] not in {38, 101, 119}:
            return await super().sock_sendall(connection, packet)
        self.sequence += 1
        opcode, minor, length = struct.unpack_from("<BBH", packet)
        assert length * 4 == len(packet) and minor == 0
        body = packet[4:]
        self.sent.append((opcode, minor, body))
        if opcode == 101:
            first, count = struct.unpack("<BBxx", body)
            assert (first, count) == (8, 248)
            mapped = b"".join(struct.pack("<IIII", *self.rows.get(code, (0, 0, 0, 0))) for code in range(8, 256))
            self.incoming.extend(reply(self.sequence, detail=self.key_stride, extra=mapped + self.map_extra))
        elif opcode == 119:
            assert not body
            self.incoming.extend(reply(self.sequence, detail=self.modifier_stride, extra=self.modifiers))
        else:
            assert struct.unpack("<I", body) == (ROOT,)
            self.incoming.extend(
                reply(
                    self.sequence,
                    detail=self.same_screen,
                    fields=struct.pack("<IIhhhhH", self.pointer_root, 0, 10, 20, 10, 20, self.logical_mask),
                )
            )
        if self.on_read is not None:
            self.on_read(opcode)


async def close(inspector, leases, focus):
    assert await focus.close()
    assert not leases.desktop.connection.closed
    assert await leases.desktop.connection.close() and await inspector.close()


__all__ = [
    "BusinessError",
    "FocusPeer",
    "KEYS",
    "KeyboardPeer",
    "ROOT",
    "SYMBOLS",
    "XkbFixture",
    "asyncio",
    "close",
    "prepared",
    "pytest",
    "reply",
    "setup_bytes",
    "setup_reply",
    "struct",
]
