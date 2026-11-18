"""Reusable linux_foreground fixtures, extracted without collecting test cases."""

import asyncio
import struct
from types import SimpleNamespace

import pytest

from omni.adapters.platform.desktop.linux.linux_bus import Process, failure
from omni.adapters.platform.desktop.linux.linux_foreground import WindowFocus
from omni.adapters.platform.desktop.linux.linux_session import SessionInspector
from omni.adapters.platform.desktop.linux.linux_window_leases import WindowLeases
from omni.adapters.platform.desktop.linux.linux_x11 import X11Desktop
from omni.domain.contracts import BusinessError
from omni_test_support.v2.linux_input import InputPeer, monitor_factory
from omni_test_support.v2.linux_window_leases import Wire, event
from omni_test_support.v2.v2_x11_helpers import ROOT, WINDOW, OwnedConnection, Peer, linux_factory, reply

CHILD = WINDOW + 1


class FocusPeer(InputPeer):
    def __init__(self, focus=CHILD):
        super().__init__()
        self.focus = focus
        self.parents = {CHILD: WINDOW, WINDOW: ROOT}
        self.tree_fault = None
        self.during_watch = None
        self.client_pid = 20

    async def sock_sendall(self, connection, packet):
        if not self.authenticated:
            return await super().sock_sendall(connection, packet)
        opcode, minor = packet[:2]
        body = packet[4:]
        if opcode == 98 and b"X-Resource" in body or opcode == 128:
            await Peer.sock_sendall(self, connection, packet)
            if opcode == 128 and minor == 4:
                struct.pack_into("<I", self.incoming, len(self.incoming) - 4, self.client_pid)
            return
        if opcode in {2, 15}:
            self.sequence += 1
            self.sent.append((opcode, minor, body))
            if opcode == 2:
                window, mask, value = struct.unpack("<III", body)
                assert mask == 1 << 11
                assert value == ((1 << 21) | (1 << 17) if window == self.focus else 1 << 17)
                if window != self.focus and self.during_watch:
                    self.event = self.during_watch
                return
            window = struct.unpack("<I", body)[0]
            fields = struct.pack("<IIH", ROOT + (self.tree_fault == "root"), self.parents[window], 0)
            result = reply(self.sequence, fields=fields)
            if self.tree_fault == "length":
                result += b"extra"
            self.incoming.extend(result)
            return
        await super().sock_sendall(connection, packet)


async def prepared(peer):
    factory = linux_factory()
    inspector = SessionInspector(factory.ops, factory)
    await inspector.open(accessibility=False)
    original = {
        "platform": "linux",
        "backend": "x11",
        "native_id": f"x11:{WINDOW:08x}:" + "a" * 64,
        "pid": 20,
        "process_created": 120.0,
        "application_id": "/usr/bin/owned-fixture",
        "session_id": "linux:x11:fixture",
    }

    class Desktop:
        window = staticmethod(X11Desktop.window)

        def __init__(self):
            self.inspector = inspector
            self.connection = OwnedConnection(inspector)
            self.wire = Wire()
            self.wire.setup = SimpleNamespace(root=ROOT)

        async def verified(self, supplied):
            await inspector.check()
            if supplied != original:
                raise failure("desktop_target_changed")

    desktop = Desktop()
    leases = WindowLeases(desktop)
    alias = (await leases.bind([original]))[0]
    connections = []
    monitor = monitor_factory(peer, connections)(inspector)
    focus = WindowFocus(leases, monitor)
    return factory, inspector, leases, alias, monitor, connections, focus


__all__ = [
    "BusinessError",
    "CHILD",
    "FocusPeer",
    "InputPeer",
    "OwnedConnection",
    "Peer",
    "Process",
    "ROOT",
    "SessionInspector",
    "SimpleNamespace",
    "WINDOW",
    "WindowFocus",
    "WindowLeases",
    "Wire",
    "X11Desktop",
    "asyncio",
    "event",
    "failure",
    "linux_factory",
    "monitor_factory",
    "prepared",
    "pytest",
    "reply",
    "struct",
]
