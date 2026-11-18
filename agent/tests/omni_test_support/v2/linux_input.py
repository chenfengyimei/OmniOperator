"""Reusable linux_input fixtures, extracted without collecting test cases."""

import asyncio
import struct

import pytest

from omni.adapters.platform.desktop.linux.linux_input import InputWire, X11Input, devices
from omni.adapters.platform.desktop.linux.linux_session import SessionInspector
from omni.domain.contracts import BusinessError
from omni_test_support.v2.v2_x11_helpers import ROOT, WINDOW, OwnedConnection, Peer, linux_factory, reply


def device_bytes(*, held=False, extra_master=False, touch=False, malformed=False, padding=0):
    # Independent documented XI2 DeviceInfo / ButtonClass / KeyClass layouts.
    button = struct.pack("<HHHHI", 1, 6, 2, 3, int(held)) + struct.pack("<III", 90, 91, 92)
    key = struct.pack("<HHHHI", 0, 3, 3, 1, 38)
    pointer = struct.pack("<HHHHHBx", 2, 1, 3, 1, 3, 1) + b"ptr" + bytes([padding]) + button
    keyboard = struct.pack("<HHHHHBx", 3, 2, 2, 1, 3, 1) + b"key" + bytes([padding]) + key
    data = bytearray(32)
    struct.pack_into("<H", data, 8, 3 if extra_master else 2)
    if touch:
        pointer = bytearray(pointer)
        struct.pack_into("<H", pointer, 16, 8)
    data += pointer + keyboard
    if extra_master:
        data += pointer
    if malformed:
        data = data[:-1]
    return bytes(data)


class InputPeer(Peer):
    def __init__(self):
        super().__init__()
        self.held = self.keys = self.selected = False
        self.event = None
        self.focus = WINDOW
        self.topology = device_bytes()

    async def sock_sendall(self, connection, packet):
        if not self.authenticated:
            return await super().sock_sendall(connection, packet)
        opcode, minor, length = struct.unpack_from("<BBH", packet)
        assert length * 4 == len(packet)
        body = packet[4:]
        self.sent.append((opcode, minor, body))
        self.sequence += 1
        extra, fields = b"", b""
        if opcode == 98:
            assert b"XInputExtension" in body
            fields = bytes([1, 130, 0, 0])
        elif opcode == 130 and minor == 47:
            assert body == struct.pack("<HH", 2, 2)
            fields = struct.pack("<HH", 2, 2)
        elif opcode == 130 and minor == 48:
            assert body == struct.pack("<Hxx", 0)
            fields, extra = self.topology[8:32], self.topology[32:]
        elif opcode == 130 and minor == 46:
            root, count, device, words, mask = struct.unpack("<IHxxHHI", body)
            assert (root, count, device, words) == (ROOT, 1, 0, 1)
            assert mask & (1 << 13) and mask & (1 << 17)
            self.selected = True
            return
        elif opcode == 43:
            fields = struct.pack("<I", self.focus)
        elif opcode == 44:
            fields, extra = bytes([int(self.keys)]) + b"\0" * 23, b"\0" * 8
        elif opcode == 2:
            assert struct.unpack("<III", body) == (WINDOW, 1 << 11, (1 << 21) | (1 << 17))
            return
        else:
            raise AssertionError((opcode, minor, body))
        if self.selected and self.event is not None:
            self.incoming.extend(self.event)
            self.event = None
        self.incoming.extend(reply(self.sequence, fields=fields, extra=extra))


def monitor_factory(peer, opened):
    async def connect(inspector):
        connection = OwnedConnection(inspector)
        opened.append(connection)
        return connection

    def wire(connection, check):
        value = InputWire(connection, check)
        value.loop = peer
        return value

    return lambda inspector: X11Input(inspector, connection_factory=connect, wire_factory=wire)


__all__ = [
    "BusinessError",
    "InputPeer",
    "InputWire",
    "OwnedConnection",
    "Peer",
    "ROOT",
    "SessionInspector",
    "WINDOW",
    "X11Input",
    "asyncio",
    "device_bytes",
    "devices",
    "linux_factory",
    "monitor_factory",
    "pytest",
    "reply",
    "struct",
]
