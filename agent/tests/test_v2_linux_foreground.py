"""Native focus ancestry and physical-input guards on injected binary peers; no actual desktop or input dispatch."""

import asyncio
import struct
from types import SimpleNamespace

import pytest
from test_v2_linux_input import InputPeer, monitor_factory
from test_v2_linux_window_leases import Wire, event
from v2_x11_helpers import ROOT, WINDOW, OwnedConnection, Peer, linux_factory, reply

from omni.adapters.platform.desktop.linux.linux_bus import Process, failure
from omni.adapters.platform.desktop.linux.linux_foreground import WindowFocus
from omni.adapters.platform.desktop.linux.linux_session import SessionInspector
from omni.adapters.platform.desktop.linux.linux_window_leases import WindowLeases
from omni.adapters.platform.desktop.linux.linux_x11 import X11Desktop
from omni.domain.contracts import BusinessError

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


@pytest.mark.parametrize("native_focus", [WINDOW, CHILD, CHILD + 1])
def test_selected_window_owns_actual_focus_or_its_bounded_native_descendant(native_focus):
    async def scenario():
        peer = FocusPeer(native_focus)
        peer.parents[CHILD + 1] = CHILD
        _factory, inspector, leases, alias, monitor, connections, focus = await prepared(peer)
        try:
            await focus.prepare(alias)
            await focus.check()
            assert monitor.wire.structures == set(focus._lineage)
            assert focus._lineage[-1] == WINDOW and focus._lineage[0] == native_focus
            assert all(opcode in {2, 15, 43, 44, 98, 128, 130} for opcode, _minor, _body in peer.sent)
            assert not any(opcode == 130 and minor not in {46, 47, 48} for opcode, minor, _body in peer.sent)
            # The proof cannot authorize another preparation or survive close.
            with pytest.raises(BusinessError):
                await focus.prepare(alias)
        finally:
            assert await focus.close() and await leases.desktop.connection.close() and await inspector.close()
            assert connections and all(connection.closed for connection in connections)
        with pytest.raises(BusinessError):
            await focus.check()

    asyncio.run(scenario())


@pytest.mark.parametrize(
    "fault", ["outside_window", "cycle", "depth", "root", "length", "different_pid", "wrong_server"]
)
def test_same_pid_without_real_window_ancestry_and_bad_native_proofs_are_rejected(fault):
    async def scenario():
        peer = FocusPeer()
        factory, inspector, leases, alias, _monitor, _connections, focus = await prepared(peer)
        if fault == "outside_window":
            peer.parents[CHILD] = ROOT
        elif fault == "cycle":
            peer.parents[CHILD] = CHILD
        elif fault == "depth":
            peer.parents = {CHILD + index: CHILD + index + 1 for index in range(17)}
        elif fault in {"root", "length"}:
            peer.tree_fault = fault
        elif fault == "different_pid":
            peer.client_pid = 21
            factory.ops.processes[21] = Process(21, 1000, 120.0, "/usr/bin/owned-fixture")
        else:
            leases.desktop.connection.inode = (5, 10)
        try:
            with pytest.raises(BusinessError):
                await focus.prepare(alias)
            with pytest.raises(BusinessError):
                await focus.check()
        finally:
            assert await focus.close() and await leases.desktop.connection.close() and await inspector.close()

    asyncio.run(scenario())


@pytest.mark.parametrize(
    "fault", ["unfocused_ancestor_event", "focus", "held_key", "process_reused", "lease_destroyed", "ancestry_changed"]
)
def test_prepared_proof_latches_changes_without_rebinding_or_sending_input(fault):
    async def scenario():
        peer = FocusPeer()
        factory, inspector, leases, alias, _monitor, _connections, focus = await prepared(peer)
        try:
            await focus.prepare(alias)
            if fault == "unfocused_ancestor_event":
                peer.event = event(21, WINDOW)
            elif fault == "focus":
                peer.focus = WINDOW
            elif fault == "held_key":
                peer.keys = True
            elif fault == "process_reused":
                factory.ops.processes[20] = Process(20, 1000, 121.0, "/usr/bin/owned-fixture")
            elif fault == "lease_destroyed":
                await leases.wire.event(event(17, WINDOW))
            else:
                peer.parents[CHILD] = ROOT
            with pytest.raises(BusinessError):
                await focus.check()
            # Even restoration to identical metadata cannot revive a failed hold.
            peer.focus, peer.keys, peer.parents[CHILD] = CHILD, False, WINDOW
            factory.ops.processes[20] = Process(20, 1000, 120.0, "/usr/bin/owned-fixture")
            with pytest.raises(BusinessError) as denied:
                await focus.check()
            assert denied.value.code == "desktop_input_unverified"
        finally:
            assert await focus.close() and await leases.desktop.connection.close() and await inspector.close()

    asyncio.run(scenario())


def test_reparent_during_lineage_subscription_and_cross_session_monitor_never_prepare():
    async def scenario(cross_session):
        peer = FocusPeer()
        peer.during_watch = event(21, WINDOW)
        _factory, inspector, leases, alias, monitor, connections, focus = await prepared(peer)
        if cross_session:
            monitor.inspector = SimpleNamespace()
        try:
            with pytest.raises(BusinessError):
                await focus.prepare(alias)
            with pytest.raises(BusinessError):
                await focus.check()
            if cross_session:
                assert not connections and not peer.sent
        finally:
            assert await focus.close() and await leases.desktop.connection.close() and await inspector.close()

    asyncio.run(scenario(False))
    asyncio.run(scenario(True))


def test_lineage_subscription_is_armed_once_and_rejects_root_duplicates_or_unbounded_paths():
    async def scenario():
        peer = FocusPeer()
        _factory, inspector, leases, _alias, monitor, _connections, focus = await prepared(peer)
        try:
            await monitor.open()
            with pytest.raises(BusinessError):
                await monitor.watch_lineage((CHILD, WINDOW))
            await monitor.arm()
            for windows in ((CHILD, ROOT), (CHILD, CHILD), (WINDOW,), tuple(range(CHILD, CHILD + 17))):
                before = list(peer.sent)
                with pytest.raises(BusinessError):
                    await monitor.watch_lineage(windows)
                assert peer.sent == before and not monitor.lineage_watched
            await monitor.watch_lineage((CHILD, WINDOW))
            with pytest.raises(BusinessError):
                await monitor.watch_lineage((CHILD, WINDOW))
            await monitor.check()
        finally:
            assert await focus.close() and await leases.desktop.connection.close() and await inspector.close()

    asyncio.run(scenario())
