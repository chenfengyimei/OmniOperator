"""Owned pointer motion and source-copy/cleanup integration with isolated protocol peers."""

import asyncio
import struct

import pytest
from test_v2_linux_pointer_target import close
from v2_linux_motion_helpers import motion_context
from v2_linux_motion_packet_helpers import PacketPeer, packet_context, resize_buttons
from v2_linux_pointer_config_helpers import IDENTITY

from omni.adapters.platform.desktop.linux.linux_input import devices
from omni.domain.contracts import BusinessError


async def dispose(context):
    # Preserve assertions about an unacknowledged lease; fixture disposal does
    # not turn an uncertain outcome into a successful settlement.
    assert await context[5].connection.close()
    assert await context[3].desktop.connection.close()
    assert await context[2].close()


@pytest.mark.parametrize("switch,words", [(False, 1), (True, 2), (True, 8)])
def test_exact_move_and_source_copy_are_adopted_only_after_actual_native_postchecks(switch, words):
    async def scenario():
        peer = PacketPeer()
        peer.switch, peer.words = switch, words
        peer.feedbacks[4] = [(3, 5, 2, 7)]
        peer.matrices[4] = (1.0, 0.0, -0.25, 0.0, 1.0, 0.5, 0.0, 0.0, 1.0)
        context, packet = await packet_context(peer)
        focus, monitor = context[-1], context[5]
        before = monitor.topology
        try:
            assert await packet.emit() is None
            assert packet.settled and packet.events.complete
            assert monitor.wire.motion_lease is monitor.wire.motion_events is None and not monitor.wire.changed
            assert len(peer.input_requests) == 1 and packet.position == 36
            assert monitor.topology == devices(peer.topology)
            assert (monitor.topology != before) == switch
            state = await focus.verify_pointer_configuration()
            assert state.master_feedback == tuple(peer.feedbacks[4] if switch else [(0, 2, 1, 4)])
            assert state.master_transform == IDENTITY  # CTM is not copied
            assert await focus.verify_motion() == packet.plan
            with pytest.raises(BusinessError):
                await packet.emit()
            assert len(peer.input_requests) == 1
            assert monitor.wire.sequence < 8192
        finally:
            await close(context)

    asyncio.run(scenario())


@pytest.mark.parametrize(
    "fault",
    [
        "sequence",
        "source",
        "flags",
        "detail",
        "mask",
        "padding",
        "processed",
        "raw",
        "order",
        "time",
        "missing",
        "extra",
    ],
)
def test_wrong_missing_or_shared_source_raw_events_never_prove_this_move(fault):
    async def scenario():
        context, packet = await packet_context()
        peer, monitor = context[0], context[5]
        peer.raw_fault = fault
        before = monitor.topology
        try:
            with pytest.raises(BusinessError):
                await packet.emit()
            assert packet.settled and monitor.wire.motion_lease is None
            assert monitor.wire.changed and monitor.topology == before
            with pytest.raises(BusinessError):
                await context[-1].motion_packet()
            assert len(peer.input_requests) == 1
        finally:
            await close(context)

    asyncio.run(scenario())


@pytest.mark.parametrize(
    "fault", ["sequence", "master", "source", "reason", "classes", "labels", "held", "missing", "duplicate", "late"]
)
def test_slave_switch_receipt_must_be_the_original_source_classes_before_any_raw_motion(fault):
    async def scenario():
        context, packet = await packet_context()
        peer, monitor = context[0], context[5]
        peer.switch_fault = fault
        before = monitor.topology
        try:
            with pytest.raises(BusinessError):
                await packet.emit()
            assert monitor.wire.changed and monitor.topology == before and packet.settled
            assert len(peer.input_requests) == 1
        finally:
            await close(context)

    asyncio.run(scenario())


@pytest.mark.parametrize("fault", ["padding", "value"])
def test_copy_receipt_ignores_only_dynamic_axis_values_and_protocol_padding(fault):
    async def scenario():
        context, packet = await packet_context()
        context[0].switch_fault = fault
        try:
            await packet.emit()
            assert packet.events.complete and not context[5].wire.changed
        finally:
            await close(context)

    asyncio.run(scenario())


@pytest.mark.parametrize("fault", ["root", "screen", "position", "mask"])
def test_acknowledged_move_with_wrong_pointer_state_is_settled_but_never_verified(fault):
    async def scenario():
        context, packet = await packet_context()
        context[0].query_fault = fault
        try:
            with pytest.raises(BusinessError):
                await packet.emit()
            assert packet.settled and context[5].wire.motion_lease is None and context[5].wire.changed
            assert len(context[0].input_requests) == context[0].query_count == 1
        finally:
            await close(context)

    asyncio.run(scenario())


def test_invalid_barrier_retains_exact_connection_lease_and_does_not_start_a_replacement_query():
    async def scenario():
        context, packet = await packet_context()
        peer, monitor = context[0], context[5]
        peer.query_fault = "bool"
        try:
            with pytest.raises(BusinessError):
                await packet.emit()
            task = packet._settlement
            assert not packet.settled and monitor.wire.motion_lease is packet and not monitor.connection.closed
            assert not await packet.settle() and not await context[-1].close()
            assert packet._settlement is task and peer.query_count == len(peer.input_requests) == 1
        finally:
            await dispose(context)

    asyncio.run(scenario())


@pytest.mark.parametrize("position", [0, 1, 35])
def test_cancelling_partial_request_drops_the_stream_instead_of_completing_new_motion(position):
    async def scenario():
        context, packet = await packet_context()
        peer, monitor = context[0], context[5]
        reached = asyncio.Event()

        async def blocked():
            reached.set()
            await asyncio.Event().wait()

        peer.write_plan = ([position] if position else []) + [BlockingIOError()]
        packet._wait_write = blocked
        try:
            emitting = asyncio.create_task(packet.emit())
            await reached.wait()
            emitting.cancel()
            with pytest.raises(asyncio.CancelledError):
                await emitting
            assert packet.position == position and not packet.settled and monitor.wire.changed
            assert await packet.settle() and monitor.connection.closed
            assert len(peer.input_requests) == peer.query_count == 0
            assert len(peer.stream) == position
            assert not context[3].desktop.connection.closed
        finally:
            await close(context)

    asyncio.run(scenario())


def test_complete_write_cancel_keeps_the_original_query_alive_and_blocks_interleaved_requests():
    async def scenario():
        context, packet = await packet_context()
        peer, monitor = context[0], context[5]
        peer.block_query = True
        try:
            emitting = asyncio.create_task(packet.emit())
            await peer.query_started.wait()
            task = packet._settlement
            for opcode, body in [(44, b""), (43, b""), (38, struct.pack("<I", packet.plan.point.root))]:
                with pytest.raises(BusinessError):
                    await monitor.wire.request(opcode, body)
            emitting.cancel()
            with pytest.raises(asyncio.CancelledError):
                await emitting
            assert packet._settlement is task and not task.done() and monitor.wire.motion_lease is packet
            closer = asyncio.create_task(context[-1].close())
            await asyncio.sleep(0)
            closer.cancel()
            with pytest.raises(asyncio.CancelledError):
                await closer
            assert not task.done() and not monitor.connection.closed
            peer.query_release.set()
            assert await packet.settle() and await context[-1].close()
            assert monitor.wire.changed and peer.query_count == len(peer.input_requests) == 1
        finally:
            peer.query_release.set()
            await dispose(context)

    asyncio.run(scenario())


@pytest.mark.parametrize("change", ["feedback", "matrix", "map", "xkb", "layout", "focus"])
def test_unnotified_postcopy_drift_cannot_be_adopted_even_when_raw_receipts_are_exact(change):
    async def scenario():
        context, packet = await packet_context()
        peer, monitor = context[0], context[5]
        before = monitor.topology

        def drift():
            if change == "feedback":
                peer.feedbacks[2] = [(7, 1, 1, 0)]
            elif change == "matrix":
                peer.matrices[2] = (1.0, 0.0, 0.25, *IDENTITY[3:])
            elif change == "map":
                peer.maps[4] = bytes([2, 1, 3])
            elif change == "xkb":
                peer.pointer_actions[2] = [1, 0, 0]
            elif change == "layout":
                peer.mode_clock += 1
            else:
                peer.focus = 0

        peer.after_move = drift
        try:
            with pytest.raises(BusinessError):
                await packet.emit()
            assert packet.settled and packet.events.complete and monitor.wire.changed
            assert monitor.topology == before
            peer.after_move = None
            with pytest.raises(BusinessError):
                await context[-1].verify_motion()
        finally:
            await close(context)

    asyncio.run(scenario())


def test_cancel_during_postcopy_read_does_not_publish_provisional_topology_or_configuration():
    async def scenario():
        context, packet = await packet_context()
        peer, monitor = context[0], context[5]
        before = monitor.topology
        reached = asyncio.Event()

        async def blocked(_identity, _category):
            reached.set()
            await asyncio.Event().wait()

        peer.on_config_read = blocked
        try:
            emitting = asyncio.create_task(packet.emit())
            await reached.wait()
            assert packet.settled and packet.events.complete and monitor.topology == before
            emitting.cancel()
            with pytest.raises(asyncio.CancelledError):
                await emitting
            assert monitor.wire.changed and monitor.topology == before
            peer.on_config_read = None
            with pytest.raises(BusinessError):
                await context[-1].verify_motion()
        finally:
            await close(context)

    asyncio.run(scenario())


def test_an_old_packet_cannot_send_after_a_later_preparation_advances_the_connection():
    async def scenario():
        context, old = await packet_context()
        try:
            packet = await context[-1].motion_packet()
            with pytest.raises(BusinessError):
                await old.emit()
            assert context[0].input_requests == []
            await packet.emit()
            assert len(context[0].input_requests) == 1
        finally:
            await close(context)

    asyncio.run(scenario())


@pytest.mark.parametrize("extra_axis", [False, True])
def test_source_description_does_not_replace_master_button_count_tail_labels_or_tail_feedback(extra_axis):
    async def scenario():
        peer = PacketPeer()
        if extra_axis:
            peer.axes[4].append(dict(peer.axes[4][0], number=2, label=202))
            peer.refresh_topology()
        resize_buttons(peer, 5, 3)
        peer.feedbacks[2] = [(0, 2, 1, 4), (5, 3, 1, 8)]
        peer.feedbacks[4] = [(3, 5, 2, 7)]
        context, packet = await packet_context(peer)
        try:
            await packet.emit()
            state = await context[-1].verify_pointer_configuration()
            assert state.master.labels == (90, 91, 92, 193, 194)
            assert state.source.labels == (90, 91, 92)
            assert state.master_feedback == ((3, 5, 2, 7), (5, 3, 1, 8))
            assert len(state.master.axes) == len(state.source.axes) == (3 if extra_axis else 2)
            assert state.plan.master_buttons == 5 and state.plan.source_buttons == 3
            assert peer.maps[2] == bytes([1, 2, 3, 4, 5])
            assert state.master.button_source == 4
        finally:
            await close(context)

    asyncio.run(scenario())


def test_unpredictable_duplicate_feedback_ids_are_refused_before_any_motion_bytes():
    async def scenario():
        peer = PacketPeer()
        peer.feedbacks[2] = [(0, 2, 1, 4), (5, 3, 1, 8)]
        peer.feedbacks[4] = [(5, 5, 2, 7)]
        context = await motion_context(peer)
        try:
            await context[-1].prepare_motion()
            with pytest.raises(BusinessError):
                await context[-1].motion_packet()
            assert not peer.input_requests
        finally:
            await close(context)

    asyncio.run(scenario())


def test_scroll_metadata_on_an_unsupplied_axis_is_copied_and_read_back_without_inventing_raw_values():
    async def scenario():
        peer = PacketPeer()
        for identity in (2, 4):
            peer.axes[identity].append(dict(peer.axes[identity][0], number=2, label=202))
        peer.scroll = True
        peer.refresh_topology()
        context, packet = await packet_context(peer)
        try:
            await packet.emit()
            state = await context[-1].verify_pointer_configuration()
            assert state.master.scrolls == state.source.scrolls and len(state.source.scrolls) == 1
            assert packet.events.seen == 2 and not context[5].wire.changed
        finally:
            await close(context)

    asyncio.run(scenario())


@pytest.mark.parametrize("broken", [False, True])
def test_partial_writes_can_resume_and_a_zero_write_is_discarded_without_new_motion(broken):
    async def scenario():
        context, packet = await packet_context()
        peer = context[0]
        peer.write_plan = [1, 0] if broken else [1, BlockingIOError(), 2, 33]

        async def ready():
            await asyncio.sleep(0)

        packet._wait_write = ready
        try:
            if broken:
                with pytest.raises(BusinessError):
                    await packet.emit()
                assert await packet.settle() and context[5].connection.closed
                assert not peer.input_requests and not peer.query_count
            else:
                await packet.emit()
                assert packet.settled and packet.events.complete and len(peer.input_requests) == 1
        finally:
            await close(context)

    asyncio.run(scenario())


def test_query_timeout_keeps_the_completed_request_unacknowledged_without_replay_or_requery():
    async def scenario():
        context, packet = await packet_context()
        peer, monitor = context[0], context[5]
        peer.block_query = True
        try:
            with pytest.raises(BusinessError):
                await packet.emit()
            task = packet._settlement
            assert packet.position == 36 and not packet.settled and monitor.wire.motion_lease is packet
            assert not await packet.settle() and packet._settlement is task
            assert not await monitor.close() and not monitor.connection.closed
            assert peer.query_count == len(peer.input_requests) == 1
        finally:
            await dispose(context)

    asyncio.run(scenario())
