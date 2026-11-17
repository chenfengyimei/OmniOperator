"""Exact paired-button dispatch, mapped XKB receipts and actual release ownership with injected peers."""

import asyncio
import struct

import pytest
from test_v2_linux_motion_packet import dispose
from test_v2_linux_pointer_target import close
from v2_linux_button_packet_helpers import ButtonPeer, button_packet_context
from v2_linux_motion_packet_helpers import resize_buttons

from omni.domain.contracts import BusinessError


@pytest.mark.parametrize("mapped,passive", [(False, False), (True, False), (True, True)])
def test_exact_raw_and_both_mapped_xkb_notifications_prove_release_but_not_application_success(mapped, passive):
    async def scenario():
        peer = ButtonPeer()
        if mapped:
            resize_buttons(peer, 7, 7)
            peer.maps[4] = bytes([0, 0, 7, 0, 0, 0, 0])
            peer.maps[2] = bytes([0, 0, 0, 0, 0, 0, 1])
        peer.passive = passive
        context, packet = await button_packet_context(peer)
        try:
            assert await packet.emit() is None
            assert packet.settled and not packet.release_pending and packet.events.complete
            assert [item[4] for item in peer.button_requests] == [4, 5]
            assert all(item[5] == (3 if mapped else 1) for item in peer.button_requests)
            assert context[5].wire.button_lease is context[5].wire.button_events is None
            assert not context[5].wire.changed
            assert await context[-1].verify_motion()
            with pytest.raises(BusinessError):
                await packet.emit()
            assert len(peer.button_requests) == 2
            assert context[5].wire.sequence < 8192
        finally:
            await close(context)

    asyncio.run(scenario())


@pytest.mark.parametrize("fault", ["source", "sequence", "code", "flags", "mask", "time", "order", "missing", "extra"])
def test_raw_button_mismatch_is_not_exempted_and_still_allows_actual_release_cleanup(fault):
    async def scenario():
        context, packet = await button_packet_context()
        context[0].raw_fault = fault
        before = context[5].topology
        try:
            with pytest.raises(BusinessError):
                await packet.emit()
            assert packet.settled and not packet.release_pending and context[5].wire.changed
            assert context[5].topology == before
            assert len(context[0].button_requests) == 2
        finally:
            await close(context)

    asyncio.run(scenario())


@pytest.mark.parametrize(
    "fault",
    ["sequence", "device", "mods", "changed", "mask", "code", "type", "request", "order", "missing", "extra", "early"],
)
def test_xkb_pointer_state_exemption_is_exact_not_a_generic_keyboard_notification_allowlist(fault):
    async def scenario():
        peer = ButtonPeer()
        if fault == "order":
            resize_buttons(peer, 7, 7)
            peer.maps[4] = bytes([0, 0, 7, 0, 0, 0, 0])
            peer.maps[2] = bytes([0, 0, 0, 0, 0, 0, 1])
        context, packet = await button_packet_context(peer)
        context[0].xkb_fault = fault
        try:
            with pytest.raises(BusinessError):
                await packet.emit()
            assert packet.settled and context[5].wire.changed
            assert packet.events.complete is (fault == "extra")
            context[0].xkb_fault = None
            with pytest.raises(BusinessError):
                await context[-1].button_packet()
            assert len(context[0].button_requests) == 2
        finally:
            await close(context)

    asyncio.run(scenario())


@pytest.mark.parametrize("position", [0, 1, 35, 36, 37, 71, 72])
def test_cancel_discards_incomplete_press_or_finishes_only_the_remaining_release_suffix(position):
    async def scenario():
        context, packet = await button_packet_context()
        peer, monitor = context[0], context[5]
        reached = asyncio.Event()

        async def blocked():
            reached.set()
            await asyncio.Event().wait()

        if position < 72:
            peer.write_plan = ([position] if position else []) + [BlockingIOError()]
            packet._wait_write = blocked
        else:
            peer.block_query = True
        try:
            emitting = asyncio.create_task(packet.emit())
            await (peer.query_started.wait() if position == 72 else reached.wait())
            emitting.cancel()
            with pytest.raises(asyncio.CancelledError):
                await emitting
            assert packet.position == position and monitor.wire.changed
            assert packet.release_pending == (position >= 36)
            assert monitor.wire.button_lease is packet
            peer.query_release.set()
            assert await packet.settle()
            assert [item[4] for item in peer.button_requests] == ([] if position < 36 else [4, 5])
            assert not packet.release_pending and packet.settled
            assert monitor.connection.closed == (position < 36)
            assert not context[3].desktop.connection.closed
        finally:
            await close(context)

    asyncio.run(scenario())


@pytest.mark.parametrize("physical", [False, True])
def test_logical_or_physical_held_state_keeps_release_lease_and_retries_only_readback(physical):
    async def scenario():
        context, packet = await button_packet_context()
        peer, monitor = context[0], context[5]
        peer.physical_held, peer.logical_held = physical, not physical
        try:
            with pytest.raises(BusinessError):
                await packet.emit()
            assert packet.release_pending and monitor.wire.button_lease is packet and not monitor.connection.closed
            assert not await monitor.close()
            peer.logical_held = False
            if physical:
                peer.topology = peer.released_topology
            assert await packet.settle() and packet.settled
            assert [item[4] for item in peer.button_requests] == [4, 5]
            assert monitor.wire.changed and monitor.wire.button_lease is None
        finally:
            await close(context)

    asyncio.run(scenario())


def test_cancelled_close_keeps_same_live_barrier_and_forbids_interleaved_queries():
    async def scenario():
        context, packet = await button_packet_context()
        peer, monitor = context[0], context[5]
        peer.block_query = True
        try:
            emitting = asyncio.create_task(packet.emit())
            await peer.query_started.wait()
            original = packet._settlement
            for opcode, body in [(44, b""), (43, b""), (38, struct.pack("<I", packet.point.root)), (130, bytes(4))]:
                with pytest.raises(BusinessError):
                    await monitor.wire.request(opcode, body)
            emitting.cancel()
            with pytest.raises(asyncio.CancelledError):
                await emitting
            closer = asyncio.create_task(context[-1].close())
            await asyncio.sleep(0)
            closer.cancel()
            with pytest.raises(asyncio.CancelledError):
                await closer
            assert original is packet._settlement and not original.done() and not monitor.connection.closed
            peer.query_release.set()
            assert await packet.settle() and await monitor.close()
            assert peer.query_count == 1 and len(peer.button_requests) == 2
        finally:
            peer.query_release.set()
            await dispose(context)

    asyncio.run(scenario())


def test_readback_timeout_retains_exact_unknown_release_stream_without_requery_or_replay():
    async def scenario():
        context, packet = await button_packet_context()
        peer, monitor = context[0], context[5]
        peer.block_query = True
        try:
            with pytest.raises(BusinessError):
                await packet.emit()
            task = packet._settlement
            assert packet.release_pending and not packet.read_complete
            assert not await packet.settle() and task is packet._settlement
            assert not await monitor.close() and not monitor.connection.closed
            assert peer.query_count == 1 and len(peer.button_requests) == 2
        finally:
            await dispose(context)

    asyncio.run(scenario())


@pytest.mark.parametrize("fault", ["position", "root", "screen", "mask"])
def test_nonbutton_pointer_outcome_mismatch_is_unknown_after_release_is_known(fault):
    async def scenario():
        context, packet = await button_packet_context()
        context[0].query_fault = fault
        try:
            with pytest.raises(BusinessError):
                await packet.emit()
            assert packet.settled and not packet.release_pending and context[5].wire.changed
            assert len(context[0].button_requests) == 2
        finally:
            await close(context)

    asyncio.run(scenario())


def test_actual_move_then_separately_prepared_click_share_the_proven_stream_without_replaying_motion():
    async def scenario():
        context, _old = await button_packet_context()
        peer, focus = context[0], context[-1]
        try:
            motion = await focus.motion_packet()
            await motion.emit()
            peer.switch = False
            packet = await focus.button_packet()
            await packet.emit()
            assert [item[4] for item in peer.input_requests] == [6, 4, 5]
            assert motion.settled and packet.settled and not context[5].wire.changed
            with pytest.raises(BusinessError):
                await _old.emit()
            assert len(peer.input_requests) == 3
        finally:
            await close(context)

    asyncio.run(scenario())


def test_preparation_requires_the_actual_cursor_at_the_original_pixel_before_any_button_bytes():
    async def scenario():
        context, _old = await button_packet_context()
        peer = context[0]
        try:
            original = peer.sock_sendall

            async def elsewhere(connection, packet):
                await original(connection, packet)
                if packet[0] == 38:
                    struct.pack_into("<h", peer.incoming, len(peer.incoming) - 16, 111)

            peer.sock_sendall = elsewhere
            with pytest.raises(BusinessError):
                await context[-1].button_packet()
            assert not peer.button_requests
        finally:
            await close(context)

    asyncio.run(scenario())


def test_second_barrier_cancellation_keeps_the_same_device_read_and_does_not_repeat_the_first_query():
    async def scenario():
        context, packet = await button_packet_context()
        peer, monitor = context[0], context[5]
        peer.block_device_query = True
        try:
            emitting = asyncio.create_task(packet.emit())
            await peer.device_query_started.wait()
            task = packet._settlement
            emitting.cancel()
            with pytest.raises(asyncio.CancelledError):
                await emitting
            assert packet.release_pending and not packet.read_complete and not task.done()
            assert monitor.wire.button_lease is packet and not monitor.connection.closed
            peer.device_query_release.set()
            assert await packet.settle() and task is packet._settlement
            assert peer.query_count == peer.device_query_count == 1 and len(peer.button_requests) == 2
        finally:
            peer.device_query_release.set()
            await dispose(context)

    asyncio.run(scenario())


def test_release_write_failure_can_be_settled_later_without_resending_the_complete_press():
    async def scenario():
        context, packet = await button_packet_context()
        peer = context[0]
        peer.write_plan = [36, OSError("injected write failure"), OSError("still unavailable")]
        try:
            with pytest.raises(OSError):
                await packet.emit()
            assert packet.position == 36 and packet.release_pending and not packet.settled
            assert not await packet.settle() and packet.position == 36
            assert await packet.settle() and not packet.release_pending
            assert [item[4] for item in peer.button_requests] == [4, 5]
            assert context[5].wire.changed
        finally:
            await close(context)

    asyncio.run(scenario())


@pytest.mark.parametrize("change", ["feedback", "xkb", "focus"])
def test_postrelease_native_configuration_change_cannot_publish_the_precomputed_source_copy(change):
    async def scenario():
        context, packet = await button_packet_context()
        peer, monitor = context[0], context[5]
        before = monitor.topology

        def changed():
            if change == "feedback":
                peer.feedbacks[2] = [(7, 1, 1, 0)]
            elif change == "xkb":
                peer.pointer_actions[2] = [1, 0, 0]
            else:
                peer.focus = 0

        peer.after_move = changed
        try:
            with pytest.raises(BusinessError):
                await packet.emit()
            assert packet.settled and packet.events.complete and monitor.wire.changed
            assert monitor.topology == before and len(peer.button_requests) == 2
        finally:
            await close(context)

    asyncio.run(scenario())
