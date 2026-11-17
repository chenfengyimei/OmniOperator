"""Explicit AT-SPI XID bindings against independent binary XRes/focus peers; no native window or input."""

import asyncio
import struct

import pytest
from test_v2_linux_foreground import CHILD, FocusPeer
from test_v2_linux_input import monitor_factory
from test_v2_linux_window_leases import event
from v2_linux_desktop_helpers import APP, Bus
from v2_linux_desktop_helpers import WINDOW as ATSPI_WINDOW
from v2_x11_helpers import ROOT, WINDOW, OwnedConnection, Peer, linux_factory, reply

from omni.adapters.platform.linux_atspi import AtspiDesktop
from omni.adapters.platform.linux_session import ACCESSIBLE, SessionInspector
from omni.adapters.platform.linux_window_binding import WindowBinding, declared_window, native_desktop
from omni.adapters.platform.linux_window_leases import LeaseWire
from omni.adapters.platform.linux_x11 import X11Desktop
from omni.domain.contracts import BusinessError


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


def test_documented_decimal_xid_does_not_use_window_title_geometry_or_a_generic_attribute():
    assert declared_window({"toolkit": "Avalonia", "avalonia-window-handle": "4294967295"}) == 0xFFFFFFFF
    assert declared_window({"toolkit": "Avalonia", "avalonia-window-handle": "2", "unrelated": "ignored"}) == 2


@pytest.mark.parametrize(
    "value",
    [
        None,
        [],
        {},
        {"toolkit": "GTK", "avalonia-window-handle": "42"},
        {"toolkit": "Avalonia", "window-id": "42"},
        {"toolkit": "Avalonia"},
        *(
            {"toolkit": "Avalonia", "avalonia-window-handle": item}
            for item in (42, "0", "1", "-1", "042", "0x2a", "+42", " 42", "42\n", "４２", "4294967296", "9" * 11)
        ),
        {"toolkit": "Avalonia", "avalonia-window-handle": "42", "large": "x" * 513},
        {"toolkit": "Avalonia", "avalonia-window-handle": "42", 2: "bad"},
        {"toolkit": "Avalonia", "avalonia-window-handle": "42", "bad": "\ud800"},
        {**{str(i): "" for i in range(127)}, "toolkit": "Avalonia", "avalonia-window-handle": "42"},
        {**{str(i): "x" * 512 for i in range(33)}, "toolkit": "Avalonia", "avalonia-window-handle": "42"},
    ],
)
def test_ambiguous_unknown_provider_or_unbounded_attributes_cannot_declare_a_native_window(value):
    with pytest.raises(BusinessError):
        declared_window(value)


def test_real_binary_xres_continuous_lease_and_focus_chain_bind_the_explicit_semantic_root_without_input():
    async def scenario():
        factory, inspector, target, native, focus, connections, monitors, binding = await binding_context()
        try:
            await binding.prepare(target)
            await binding.check()
            assert binding.focus._lineage == (CHILD, WINDOW)
            assert binding.focus._target["native_id"].startswith("x11-live:")
            assert factory.attribute_reads == 5  # initial descriptor plus two pairs around focus/lease barriers
            assert not any(member == "GetExtents" for _, _, _, _, member, _, _, _ in factory.calls)
            assert all(opcode in {2, 3, 14, 43, 98, 128, 129} for opcode, _, _ in native.sent)
            assert all(opcode in {2, 15, 43, 44, 98, 128, 130} for opcode, _, _ in focus.sent)
            # The target object supplied by the caller is no longer authority.
            target["pid"] = 999
            await binding.check()
            with pytest.raises(BusinessError):
                await binding.prepare(target)
            assert await binding.close() and all(item.closed for item in connections + monitors)
            assert not any(bus.closed for bus in inspector.buses)
            with pytest.raises(BusinessError):
                await binding.check()
        finally:
            assert await binding.close() and await inspector.close()

    asyncio.run(scenario())


@pytest.mark.parametrize(
    "fault",
    [
        "root",
        "missing_native_owner",
        "other_native_owner",
        "other_focus_owner",
        "unrelated_same_pid",
        "wayland",
        "unsupported",
        "preopened_monitor",
    ],
)
def test_provider_claim_alone_never_replaces_native_ownership_focus_or_session_proof(fault):
    async def scenario():
        factory, inspector, target, native, focus, connections, _monitors, binding = await binding_context()
        if fault == "root":
            factory.attributes["avalonia-window-handle"] = str(ROOT)
        elif fault == "missing_native_owner":
            native.fault = "remote_pid"
        elif fault == "other_native_owner":
            factory.ops.processes[21] = type(factory.ops.processes[20])(21, 1000, 120.0, "/usr/bin/owned-fixture")
            native.client_pid = 21
        elif fault == "other_focus_owner":
            focus.client_pid = 3
        elif fault == "unrelated_same_pid":
            focus.parents[CHILD] = ROOT
        elif fault == "wayland":
            inspector.session = type(inspector.session)(**{**vars(inspector.session), "kind": "wayland"})
        elif fault == "unsupported":
            factory.attributes["toolkit"] = "Qt"
        else:
            await binding.monitor.open()
        try:
            with pytest.raises(BusinessError):
                await binding.prepare(target)
            with pytest.raises(BusinessError):
                await binding.check()
            if fault in {"wayland", "unsupported", "preopened_monitor"}:
                assert not connections
            assert not any(opcode == 132 for opcode, _, _ in focus.sent)
        finally:
            assert await binding.close() and await inspector.close()

    asyncio.run(scenario())


@pytest.mark.parametrize(
    "fault",
    [
        "changed_handle",
        "changed_toolkit",
        "window_generation",
        "focus",
        "root_reparent",
        "process_reuse",
        "attribute_second_read",
    ],
)
def test_changed_binding_or_native_lifetime_cannot_revive_after_the_old_metadata_is_restored(fault):
    async def scenario():
        factory, inspector, target, native, focus, _connections, _monitors, binding = await binding_context()
        await binding.prepare(target)
        old = dict(factory.attributes)
        if fault == "changed_handle":
            factory.attributes["avalonia-window-handle"] = str(WINDOW + 1)
        elif fault == "changed_toolkit":
            factory.attributes["toolkit"] = "GTK"
        elif fault == "window_generation":
            native.event = event(17, WINDOW)
        elif fault == "focus":
            focus.focus = WINDOW + 2
        elif fault == "root_reparent":
            focus.parents[CHILD] = ROOT
        elif fault == "process_reuse":
            factory.ops.processes[20] = type(factory.ops.processes[20])(20, 1000, 121.0, "/usr/bin/owned-fixture")
        else:
            count = factory.attribute_reads

            def change():
                if factory.attribute_reads == count + 2:
                    factory.attributes["avalonia-window-handle"] = str(WINDOW + 1)

            factory.attribute_hook = change
        try:
            with pytest.raises(BusinessError):
                await binding.check()
            factory.attributes = old
            factory.attribute_hook = None
            before = len(native.sent) + len(focus.sent) + factory.attribute_reads
            with pytest.raises(BusinessError):
                await binding.check()
            assert len(native.sent) + len(focus.sent) + factory.attribute_reads == before
        finally:
            assert await binding.close() and await inspector.close()

    asyncio.run(scenario())


def test_cancelled_preparation_retains_actual_native_connection_and_cannot_become_ready():
    async def scenario():
        factory, inspector, target, _native, _focus, connections, _monitors, binding = await binding_context()

        def cancel():
            if factory.attribute_reads == 2:
                raise asyncio.CancelledError()

        factory.attribute_hook = cancel
        try:
            with pytest.raises(asyncio.CancelledError):
                await binding.prepare(target)
            assert connections and not connections[0].closed
            factory.attribute_hook = None
            with pytest.raises(BusinessError):
                await binding.check()
            assert await binding.close() and connections[0].closed
        finally:
            assert await binding.close() and await inspector.close()

    asyncio.run(scenario())


def test_unsettled_key_release_keeps_both_connections_and_close_revokes_the_focus_object():
    async def scenario():
        _factory, inspector, target, _native, _focus, connections, monitors, binding = await binding_context()
        await binding.prepare(target)

        class Release:
            complete = False

            async def settle(self):
                return self.complete

        release = Release()
        binding.monitor.wire.key_lease = release
        try:
            assert not await binding.close()
            assert not connections[0].closed and not monitors[0].closed
            with pytest.raises(BusinessError):
                await binding.focus.check()
            release.complete = True
            assert await binding.close() and connections[0].closed and monitors[0].closed
            assert not any(bus.closed for bus in inspector.buses)
        finally:
            release.complete = True
            assert await binding.close() and await inspector.close()

    asyncio.run(scenario())


def test_default_native_factory_uses_continuous_structure_wire_without_opening_any_connection():
    marker = object()
    desktop = native_desktop(marker)
    assert desktop.inspector is marker and desktop._wire is LeaseWire and desktop.connection is None
