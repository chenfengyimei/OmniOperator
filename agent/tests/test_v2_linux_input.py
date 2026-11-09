"""Input protocol on injected Xorg/logind peers; no keyboard, pointer, X socket or cookie accessed."""

import asyncio
import struct

import pytest
from v2_x11_helpers import ROOT, WINDOW, OwnedConnection, Peer, linux_factory, reply

from omni.adapters.platform.linux_input import InputWire, X11Input, devices
from omni.adapters.platform.linux_session import SessionInspector
from omni.domain.contracts import BusinessError


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


@pytest.mark.parametrize("fault", [None, "held", "extra_master", "touch", "malformed"])
def test_complete_released_device_state_is_required_and_names_or_padding_do_not_grant_control(fault):
    options = {} if fault is None else {fault: True}
    if fault is None:
        assert devices(device_bytes()) == devices(device_bytes(padding=233))
    else:
        with pytest.raises(BusinessError):
            devices(device_bytes(**options))


@pytest.mark.parametrize(
    "fault",
    [
        None,
        "raw_key",
        "raw_motion",
        "hierarchy",
        "focus_event",
        "focus_changed",
        "held_key",
        "synthetic",
        "wrong_extension",
        "oversize",
    ],
)
def test_actual_guard_parses_events_at_barriers_and_latches_user_changes(fault):
    async def scenario():
        f, peer, opened = linux_factory(), InputPeer(), []
        inspector = SessionInspector(f.ops, f)
        await inspector.open(accessibility=False)
        monitor = monitor_factory(peer, opened)(inspector)
        try:
            await monitor.open()
            await monitor.arm()
            if fault in {"raw_key", "raw_motion", "hierarchy", "synthetic", "wrong_extension", "oversize"}:
                event = bytearray(32)
                event[0], event[1] = (163 if fault == "synthetic" else 35), (131 if fault == "wrong_extension" else 130)
                struct.pack_into("<H", event, 8, {"raw_key": 13, "raw_motion": 17, "hierarchy": 11}.get(fault, 13))
                struct.pack_into("<I", event, 4, 1025 if fault == "oversize" else 0)
                peer.event = bytes(event)
            elif fault == "focus_event":
                event = bytearray(32)
                event[0] = 10
                struct.pack_into("<I", event, 4, WINDOW)
                peer.event = bytes(event)
            elif fault == "focus_changed":
                peer.focus += 1
            elif fault == "held_key":
                peer.keys = True
            if fault is None:
                await monitor.check()
            else:
                with pytest.raises(BusinessError):
                    await monitor.check()
            assert all(opcode not in {73, 100, 101, 104} for opcode, _, _ in peer.sent)
            assert not any(opcode == 130 and minor not in {47, 48, 46} for opcode, minor, _ in peer.sent)
        finally:
            assert await monitor.close() and await inspector.close()
            assert all(c.closed for c in opened)

    asyncio.run(scenario())
