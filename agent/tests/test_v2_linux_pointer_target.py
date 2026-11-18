"""Shape-aware native point proof on real binary readers with injected Xorg/logind, never native input."""

import asyncio
import struct

import pytest
from test_v2_linux_window_leases import event
from v2_linux_pointer_helpers import CHILD, FOREIGN, FRAME, HIT, OVERLAY, ROOT, WINDOW, PointerPeer, pointer_context

from omni.adapters.platform.desktop.linux.linux_bus import Process
from omni.adapters.platform.desktop.linux.linux_pointer_target import PointerTarget, physical_point
from omni.domain.contracts import BusinessError


async def close(context):
    _peer, _factory, inspector, leases, _alias, _monitor, connections, focus = context
    assert await focus.close() and all(item.closed for item in connections)
    assert not leases.desktop.connection.closed  # the pointer proof borrows this owner
    assert await leases.desktop.connection.close() and await inspector.close()


def test_physical_window_point_is_translated_through_wm_frame_and_owned_input_only_recipient():
    async def scenario():
        context = await pointer_context()
        peer, _factory, _inspector, _leases, _alias, monitor, _connections, focus = context
        try:
            point = await focus.prepare_pointer((10, 12))
            assert (point.root, point.window, point.x, point.y) == (ROOT, WINDOW, 110, 212)
            assert point.lineage == (ROOT, FRAME, WINDOW, HIT)
            assert await focus.verify_pointer() == point
            assert monitor.wire.pointer_parents == {ROOT, FRAME, WINDOW}
            assert CHILD in monitor.wire.structures  # original native keyboard focus retained
            watches = [struct.unpack("<III", body) for opcode, _minor, body in peer.sent if opcode == 2]
            assert (ROOT, 1 << 11, (1 << 17) | (1 << 19)) in watches
            assert (HIT, 1 << 11, 1 << 17) in watches
            assert all(opcode in {2, 3, 14, 15, 40, 43, 44, 98, 128, 130} for opcode, _minor, _body in peer.sent)
            assert all(minor in {46, 47, 48} for opcode, minor, _body in peer.sent if opcode == 130)
            with pytest.raises(BusinessError):
                await focus.prepare_pointer((10, 12))
        finally:
            await close(context)
        with pytest.raises(BusinessError):
            await focus.verify_pointer()

    asyncio.run(scenario())


def test_transparent_overlay_is_not_inferred_from_geometry_and_server_hit_selection_is_used():
    async def scenario():
        peer = PointerPeer()
        peer.children[ROOT].append(OVERLAY)
        # QueryTree lists the overlapping topmost InputOnly overlay. Xorg's
        # shape-aware TranslateCoordinates returns the frame below its hole.
        context = await pointer_context(peer)
        try:
            assert (await context[-1].prepare_pointer((10, 12))).lineage[-1] == HIT
            peer.winners[ROOT] = OVERLAY  # same rectangles, input shape now blocks
            with pytest.raises(BusinessError):
                await context[-1].verify_pointer()
            peer.winners[ROOT] = FRAME
            with pytest.raises(BusinessError):
                await context[-1].verify_pointer()
        finally:
            await close(context)

    asyncio.run(scenario())


@pytest.mark.parametrize(
    "point", [None, [1, 2], (), (1,), (1, 2, 3), (True, 2), (1.0, 2), (-1, 0), (8192, 0), (1, "2")]
)
def test_invalid_physical_point_is_rejected_without_native_reads(point):
    with pytest.raises(BusinessError):
        physical_point(point)


@pytest.mark.parametrize(
    "fault",
    [
        "geometry_root",
        "geometry_length",
        "tree_root",
        "tree_parent",
        "tree_length",
        "duplicate_child",
        "many_children",
        "zero_child",
        "different_screen",
        "root_negative",
        "root_outside",
        "cycle",
        "not_child",
        "root_only",
        "roundtrip",
    ],
)
def test_bad_geometry_tree_translation_and_unrelated_native_routes_never_prepare(fault):
    async def scenario():
        context = await pointer_context()
        context[0].point_fault = fault
        try:
            with pytest.raises(BusinessError):
                await context[-1].prepare_pointer((10, 12))
            context[0].point_fault = None
            with pytest.raises(BusinessError):
                await context[-1].verify_pointer()
        finally:
            await close(context)

    asyncio.run(scenario())


@pytest.mark.parametrize(
    "fault",
    [
        "unmapped",
        "invalid_class",
        "override",
        "local_outside",
        "huge_window",
        "huge_root",
        "root_offset",
        "leaf_outside",
        "foreign_child",
        "same_pid_other_window",
        "selected_border",
        "selected_depth",
        "root_depth",
        "huge_depth",
        "bad_override",
    ],
)
def test_nonviewable_unbounded_occluded_or_foreign_recipients_are_rejected(fault):
    async def scenario():
        context = await pointer_context()
        peer, factory, _inspector, _leases, _alias, _monitor, _connections, focus = context
        point = (10, 12)
        if fault == "unmapped":
            peer.nodes[HIT][7] = 0
        elif fault == "invalid_class":
            peer.nodes[HIT][6] = 3
        elif fault == "override":
            peer.nodes[WINDOW][8] = 1
        elif fault == "local_outside":
            point = (640, 12)
        elif fault == "huge_window":
            peer.nodes[WINDOW][2] = 8193
        elif fault == "huge_root":
            peer.nodes[ROOT][2] = 32768
        elif fault == "root_offset":
            peer.nodes[ROOT][0] = 1
        elif fault == "leaf_outside":
            peer.nodes[HIT][0] = 50
        elif fault == "foreign_child":
            peer.winners[WINDOW] = FOREIGN
            peer.children[WINDOW].append(FOREIGN)
            factory.ops.processes[21] = Process(21, 1000, 120.0, "/usr/bin/owned-fixture")
        elif fault == "selected_border":
            peer.nodes[WINDOW][4] = 1
        elif fault == "selected_depth":
            peer.nodes[WINDOW][5] = 0
        elif fault == "root_depth":
            peer.nodes[ROOT][5] = 0
        elif fault == "huge_depth":
            peer.nodes[HIT][5] = 255
        elif fault == "bad_override":
            peer.nodes[HIT][8] = 2
        else:
            peer.owners[0x800000] = 20
            peer.children[ROOT].append(OVERLAY)
            peer.winners[ROOT] = OVERLAY
        try:
            with pytest.raises(BusinessError):
                await focus.prepare_pointer(point)
        finally:
            await close(context)

    asyncio.run(scenario())


@pytest.mark.parametrize("kind", [16, 17, 19, 21, 22, 26])
def test_new_or_reordered_sibling_during_subscription_invalidates_old_hit_proof(kind):
    async def scenario():
        context = await pointer_context()
        peer = context[0]

        async def watch(window, _mask):
            if window == ROOT:
                value = bytearray(event(kind, OVERLAY))
                struct.pack_into("<I", value, 4, ROOT)
                peer.event = bytes(value)

        peer.on_pointer_watch = watch
        try:
            with pytest.raises(BusinessError):
                await context[-1].prepare_pointer((10, 12))
            peer.on_pointer_watch = None
            with pytest.raises(BusinessError):
                await context[-1].verify_pointer()
        finally:
            await close(context)

    asyncio.run(scenario())


@pytest.mark.parametrize("fault", ["point", "stack", "geometry", "physical", "pid_reuse", "hit_child"])
def test_old_point_cannot_revive_after_observation_input_or_process_drift(fault):
    async def scenario():
        context = await pointer_context()
        peer, factory, _inspector, _leases, _alias, monitor, _connections, focus = context
        try:
            await focus.prepare_pointer((10, 12))
            if fault == "point":
                peer.nodes[FRAME][0] += 1
            elif fault == "stack":
                peer.children[WINDOW].reverse()
            elif fault == "geometry":
                peer.nodes[HIT][2] += 1
            elif fault == "physical":
                monitor.wire.changed = True
            elif fault == "pid_reuse":
                factory.ops.processes[20] = Process(20, 1000, 121.0, "/usr/bin/owned-fixture")
            else:
                peer.winners[WINDOW] = 0
            with pytest.raises(BusinessError):
                await focus.verify_pointer()
            # Restore all visible state; the failed preparation stays revoked.
            peer.nodes[FRAME][0] = 98
            peer.nodes[HIT][2] = 30
            peer.children[WINDOW] = [CHILD, HIT]
            peer.winners[WINDOW] = HIT
            monitor.wire.changed = False
            factory.ops.processes[20] = Process(20, 1000, 120.0, "/usr/bin/owned-fixture")
            with pytest.raises(BusinessError):
                await focus.verify_pointer()
        finally:
            await close(context)

    asyncio.run(scenario())


def test_cancellation_latches_failure_and_keeps_actual_monitor_owned_until_caller_closes():
    async def scenario():
        context = await pointer_context()
        peer, _factory, _inspector, _leases, _alias, _monitor, connections, focus = context
        entered, release = asyncio.Event(), asyncio.Event()

        async def blocked(_source, _destination):
            entered.set()
            await release.wait()

        peer.on_translate = blocked
        try:
            task = asyncio.create_task(focus.prepare_pointer((10, 12)))
            await entered.wait()
            task.cancel()
            with pytest.raises(asyncio.CancelledError):
                await task
            assert connections and not connections[0].closed
            peer.on_translate = None
            with pytest.raises(BusinessError):
                await focus.verify_pointer()
        finally:
            release.set()
            await close(context)

    asyncio.run(scenario())


@pytest.mark.parametrize("extra_frames,allowed", [(12, True), (13, False)])
def test_bounded_native_hit_path_accepts_exact_depth_limit_and_rejects_the_next_hop(extra_frames, allowed):
    async def scenario():
        peer = PointerPeer()
        previous = ROOT
        for index in range(extra_frames):
            window = 0x600010 + index
            peer.nodes[window] = [0, 0, 1920, 1080, 0, 24, 1, 2, 0]
            peer.parents[window] = previous
            peer.children[previous] = [window]
            peer.winners[previous] = window
            previous = window
        peer.parents[FRAME] = previous
        peer.children[previous], peer.winners[previous] = [FRAME], FRAME
        context = await pointer_context(peer)
        try:
            if allowed:
                point = await context[-1].prepare_pointer((10, 12))
                assert len(point.lineage) == 16
                assert await context[-1].verify_pointer() == point
            else:
                with pytest.raises(BusinessError):
                    await context[-1].prepare_pointer((10, 12))
        finally:
            await close(context)

    asyncio.run(scenario())


def test_pointer_subscription_preserves_focus_events_when_receiving_child_is_also_keyboard_focus():
    async def scenario():
        peer = PointerPeer()
        peer.focus = HIT
        context = await pointer_context(peer)
        try:
            point = await context[-1].prepare_pointer((10, 12))
            assert point.lineage[-1] == HIT
            watches = [struct.unpack("<III", body) for opcode, _minor, body in peer.sent if opcode == 2]
            assert (HIT, 1 << 11, (1 << 17) | (1 << 21)) in watches
            assert await context[-1].verify_pointer() == point
        finally:
            await close(context)

    asyncio.run(scenario())


def test_another_window_cannot_borrow_an_existing_windows_focus_proof():
    async def scenario():
        context = await pointer_context()
        focus = context[-1]
        try:
            before = len(context[0].sent)
            with pytest.raises(BusinessError):
                PointerTarget(
                    focus, {**focus._original, "native_id": f"x11:{FOREIGN:08x}:" + "a" * 64}, focus._resource, (10, 12)
                )
            assert len(context[0].sent) == before
        finally:
            await close(context)

    asyncio.run(scenario())


def test_full_explicit_atspi_binding_retains_both_native_connections_and_exposes_only_a_read_point(monkeypatch):
    import test_v2_linux_window_binding as bindings
    from v2_x11_helpers import reply

    class NativePointerPeer(bindings.NativePeer):
        async def sock_sendall(self, connection, packet):
            if self.authenticated and packet[0] == 14:
                self.sequence += 1
                self.sent.append((packet[0], packet[1], packet[4:]))
                self.incoming.extend(
                    reply(self.sequence, detail=24, fields=struct.pack("<IhhHHH", ROOT, 2, 2, 640, 480, 0))
                )
                return
            await super().sock_sendall(connection, packet)

    monkeypatch.setattr(bindings, "NativePeer", NativePointerPeer)
    monkeypatch.setattr(bindings, "FocusPeer", PointerPeer)

    async def scenario():
        _factory, inspector, target, _native, peer, connections, monitors, binding = await bindings.binding_context()
        try:
            await binding.prepare(target)
            peer.pointer_mode = True
            point = await binding.focus.prepare_pointer((10, 12))
            assert point.lineage == (ROOT, FRAME, WINDOW, HIT)
            await binding.check()
            assert await binding.focus.verify_pointer() == point
        finally:
            assert await binding.close() and all(item.closed for item in connections + monitors)
            assert not any(bus.closed for bus in inspector.buses)
            assert await inspector.close()

    asyncio.run(scenario())
