"""Reusable linux_window_binding fixtures, extracted without collecting test cases."""

import asyncio
import struct

import pytest

from omni.adapters.platform.desktop.linux.linux_atspi import AtspiDesktop
from omni.adapters.platform.desktop.linux.linux_session import ACCESSIBLE, SessionInspector
from omni.adapters.platform.desktop.linux.linux_window_binding import WindowBinding, declared_window, native_desktop
from omni.adapters.platform.desktop.linux.linux_window_leases import LeaseWire
from omni.adapters.platform.desktop.linux.linux_x11 import X11Desktop
from omni.domain.contracts import BusinessError
from omni_test_support.v2.linux_foreground import CHILD, FocusPeer
from omni_test_support.v2.linux_input import monitor_factory
from omni_test_support.v2.linux_window_leases import event
from omni_test_support.v2.v2_linux_desktop_helpers import APP, Bus
from omni_test_support.v2.v2_linux_desktop_helpers import WINDOW as ATSPI_WINDOW
from omni_test_support.v2.v2_x11_helpers import ROOT, WINDOW, OwnedConnection, Peer, linux_factory, reply


class AttributesBus(Bus):
    async def call(self, destination, path, interface, member, signature="", body=None, *, returns):
        if (destination, path, interface, member) == (APP, ATSPI_WINDOW, ACCESSIBLE, "GetAttributes"):
            assert signature == "" and returns == "a{ss}"
            self.check()
            self.factory.attribute_reads += 1
            if self.factory.attribute_hook is not None:
                self.factory.attribute_hook()
            return [dict(self.factory.attributes)]
        return await super().call(destination, path, interface, member, signature, body, returns=returns)


class NativePeer(Peer):
    def __init__(self):
        super().__init__()
        self.event = None
        self.client_pid = 20

    async def sock_sendall(self, connection, packet):
        if self.authenticated and packet[0] in {2, 43}:
            self.sequence += 1
            body = packet[4:]
            self.sent.append((packet[0], packet[1], body))
            if packet[0] == 2:
                assert struct.unpack("<III", body) == (WINDOW, 1 << 11, 1 << 17)
                return
            if self.event is not None:
                self.incoming.extend(self.event)
                self.event = None
            self.incoming.extend(reply(self.sequence, fields=struct.pack("<I", CHILD)))
            return
        await super().sock_sendall(connection, packet)
        if packet[:2] == bytes([128, 4]) and self.fault != "remote_pid":
            struct.pack_into("<I", self.incoming, len(self.incoming) - 4, self.client_pid)


async def binding_context():
    factory = linux_factory()
    factory.attributes = {"toolkit": "Avalonia", "avalonia-window-handle": str(WINDOW)}
    factory.attribute_reads = 0
    factory.attribute_hook = None
    factory.buses["a11y"] = AttributesBus(factory, "a11y")
    inspector = SessionInspector(factory.ops, factory)
    await inspector.open()
    desktop = AtspiDesktop(inspector)
    target = await desktop.target((APP, ATSPI_WINDOW))
    native_peer, focus_peer = NativePeer(), FocusPeer()
    native_connections, monitor_connections = [], []

    async def connect(selected):
        connection = OwnedConnection(selected)
        native_connections.append(connection)
        return connection

    def wire(connection, check):
        value = LeaseWire(connection, check)
        value.loop = native_peer
        return value

    def native(selected):
        return X11Desktop(selected, connection_factory=connect, wire_factory=wire)

    monitor = monitor_factory(focus_peer, monitor_connections)(inspector)
    binding = WindowBinding(desktop, monitor, native_factory=native)
    return factory, inspector, target, native_peer, focus_peer, native_connections, monitor_connections, binding


__all__ = [
    "ACCESSIBLE",
    "APP",
    "ATSPI_WINDOW",
    "AtspiDesktop",
    "AttributesBus",
    "Bus",
    "BusinessError",
    "CHILD",
    "FocusPeer",
    "LeaseWire",
    "NativePeer",
    "OwnedConnection",
    "Peer",
    "ROOT",
    "SessionInspector",
    "WINDOW",
    "WindowBinding",
    "X11Desktop",
    "asyncio",
    "binding_context",
    "declared_window",
    "event",
    "linux_factory",
    "monitor_factory",
    "native_desktop",
    "pytest",
    "reply",
    "struct",
]
