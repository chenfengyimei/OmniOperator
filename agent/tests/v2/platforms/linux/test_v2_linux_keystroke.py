"""Paired protocol dispatch over fake streams with exact bytes/events; never contacts native devices."""

from omni_test_support.v2.linux_keystroke import (
    BusinessError as BusinessError,
)
from omni_test_support.v2.linux_keystroke import (
    KeyboardPeer as KeyboardPeer,
)
from omni_test_support.v2.linux_keystroke import (
    KeyEvents as KeyEvents,
)
from omni_test_support.v2.linux_keystroke import (
    KeyPeer as KeyPeer,
)
from omni_test_support.v2.linux_keystroke import (
    SourcePeer as SourcePeer,
)
from omni_test_support.v2.linux_keystroke import (
    asyncio as asyncio,
)
from omni_test_support.v2.linux_keystroke import (
    cleanup as cleanup,
)
from omni_test_support.v2.linux_keystroke import (
    packet_context as packet_context,
)
from omni_test_support.v2.linux_keystroke import (
    prepared as prepared,
)
from omni_test_support.v2.linux_keystroke import (
    pytest as pytest,
)
from omni_test_support.v2.linux_keystroke import (
    raw as raw,
)
from omni_test_support.v2.linux_keystroke import (
    struct as struct,
)


def test_exact_pair_is_not_application_success_and_wire_cannot_replay_or_reuse_an_old_packet():
    async def scenario():
        peer = KeyPeer()
        inspector, leases, monitor, focus, packet = await packet_context(peer)
        old, packet = packet, await focus.key_packet()
        try:
            assert await packet.emit() is None
            assert packet.events.complete and packet.settled and not packet.release_pending
            assert monitor.wire.key_lease is monitor.wire.key_events is None and not monitor.wire.changed
            assert [request[4] for request in peer.input_requests] == [2, 3]
            with pytest.raises(BusinessError):
                await packet.emit()
            with pytest.raises(BusinessError):
                await old.emit()
            assert len(peer.input_requests) == 2
            assert await focus.close()
        finally:
            await cleanup(inspector, leases, monitor)

    asyncio.run(scenario())


@pytest.mark.parametrize(
    "fault", ["source", "sequence", "flags", "valuators", "order", "missing", "other_client", "device_change"]
)
def test_extra_shared_source_physical_or_unexpected_events_never_count_as_own_input(fault):
    async def scenario():
        peer = KeyPeer()
        inspector, leases, monitor, focus, packet = await packet_context(peer)
        peer.raw_fault = fault
        try:
            with pytest.raises(BusinessError) as refused:
                await packet.emit()
            assert refused.value.code == "desktop_user_intervened"
            assert packet.settled and not packet.release_pending
            assert len(peer.input_requests) == 2
            assert await focus.close()
        finally:
            await cleanup(inspector, leases, monitor)

    asyncio.run(scenario())


def test_readback_timeout_keeps_the_exact_uncertain_stream_and_does_not_start_a_replacement_query():
    async def scenario():
        peer = KeyPeer()
        inspector, leases, monitor, focus, packet = await packet_context(peer)
        peer.block_query = True
        try:
            with pytest.raises(BusinessError):
                await packet.emit()
            assert packet.release_pending and not packet.settled
            assert packet.read_started and not packet.read_complete
            retained = packet._settlement
            assert retained is not None and retained.done()
            assert not await focus.close() and not monitor.connection.closed
            assert packet._settlement is retained and peer.query_count == 1
            assert len(peer.input_requests) == 2
        finally:
            peer.query_release.set()
            await cleanup(inspector, leases, monitor)

    asyncio.run(scenario())


def test_close_cannot_write_a_second_release_while_the_initial_sender_is_still_live():
    async def scenario():
        peer = KeyPeer()
        inspector, leases, monitor, focus, packet = await packet_context(peer)
        peer.write_plan = [36, BlockingIOError()]
        waiting, resume = asyncio.Event(), asyncio.Event()

        async def writable():
            waiting.set()
            await resume.wait()

        packet._wait_write = writable
        emitting = asyncio.create_task(packet.emit())
        try:
            await waiting.wait()
            assert not await focus.close()
            assert not monitor.connection.closed and [row[4] for row in peer.input_requests] == [2]
            resume.set()
            await emitting
            assert await focus.close() and [row[4] for row in peer.input_requests] == [2, 3]
        finally:
            resume.set()
            if not emitting.done():
                await emitting
            await cleanup(inspector, leases, monitor)

    asyncio.run(scenario())


@pytest.mark.parametrize("prefix", [0, 1, 18, 35, 36, 40, 71])
def test_cancellation_discards_incomplete_press_or_finishes_only_its_release_suffix(prefix):
    async def scenario():
        peer = KeyPeer()
        inspector, leases, monitor, focus, packet = await packet_context(peer)
        peer.write_plan = ([prefix] if prefix else []) + [BlockingIOError()]

        async def cancel():
            raise asyncio.CancelledError()

        packet._wait_write = cancel
        try:
            with pytest.raises(asyncio.CancelledError):
                await packet.emit()
            assert packet.position == prefix
            assert monitor.wire.key_lease is packet
            assert packet.release_pending == (prefix >= 36)
            assert await focus.close()
            assert monitor.connection.closed and packet.settled
            assert [request[4] for request in peer.input_requests] == ([2, 3] if prefix >= 36 else [])
            assert len(peer.stream) == (prefix if prefix < 36 else 0)
        finally:
            await cleanup(inspector, leases, monitor)

    asyncio.run(scenario())


def test_failed_release_retains_monitor_and_retry_never_repeats_a_press_or_inserts_a_query_into_the_stream():
    async def scenario():
        peer = KeyPeer()
        inspector, leases, monitor, focus, packet = await packet_context(peer)
        peer.write_plan = [36, BlockingIOError(), BlockingIOError()]

        async def blocked():
            raise OSError("injected backpressure failure")

        packet._wait_write = blocked
        try:
            with pytest.raises(OSError):
                await packet.emit()
            before = len(peer.sent)
            with pytest.raises(BusinessError):
                await monitor.wire.request(43)
            assert len(peer.sent) == before
            assert not await focus.close()
            assert packet.release_pending and not monitor.connection.closed
            assert monitor.wire.key_lease is packet and [row[4] for row in peer.input_requests] == [2]
            assert await focus.close()
            assert packet.settled and [row[4] for row in peer.input_requests] == [2, 3]
        finally:
            await cleanup(inspector, leases, monitor)

    asyncio.run(scenario())


def test_held_keyboard_retains_release_proof_until_a_later_complete_read_without_sending_again():
    async def scenario():
        peer = KeyPeer()
        inspector, leases, monitor, focus, packet = await packet_context(peer)
        peer.keys = True
        try:
            with pytest.raises(BusinessError):
                await packet.emit()
            assert packet.release_pending and not await focus.close()
            assert not monitor.connection.closed
            peer.keys = False
            assert await focus.close()
            assert packet.settled and len(peer.input_requests) == 2
        finally:
            await cleanup(inspector, leases, monitor)

    asyncio.run(scenario())


def test_cancelled_receipt_waiter_leaves_the_same_actual_query_owned_until_close_drains_it():
    async def scenario():
        peer = KeyPeer()
        inspector, leases, monitor, focus, packet = await packet_context(peer)
        peer.block_query = True
        emitting = asyncio.create_task(packet.emit())
        try:
            await peer.query_started.wait()
            emitting.cancel()
            with pytest.raises(asyncio.CancelledError):
                await emitting
            retained = packet._settlement
            assert retained is not None and not retained.done() and monitor.wire.key_lease is packet
            closing = asyncio.create_task(focus.close())
            await asyncio.sleep(0)
            assert not closing.done() and not monitor.connection.closed
            peer.query_release.set()
            assert await closing
            assert packet._settlement is retained and packet.settled
            assert peer.query_count == 1 and len(peer.input_requests) == 2
        finally:
            peer.query_release.set()
            if not emitting.done():
                await emitting
            await cleanup(inspector, leases, monitor)

    asyncio.run(scenario())


@pytest.mark.parametrize("fault", ["sequence", "category", "device", "source", "code", "flags", "mask", "timestamp"])
def test_native_raw_pair_layout_has_no_name_time_or_shared_source_shortcut(fault):
    gate = KeyEvents(100, 40, 5, 3)
    first = raw(100, 13, 5)
    assert gate.accept(first[:32], first[32:])
    fields = dict(sequence=100, category=13, device=3)
    payload = {}
    if fault == "mask":
        payload["values"] = b"\0" * 4
    else:
        values = {"sequence": 99, "category": 14, "device": 5, "source": 6, "code": 41, "flags": 1, "timestamp": 1001}
        if fault in fields:
            fields[fault] = values[fault]
        else:
            payload[fault] = values[fault]
    second = raw(**fields, **payload)
    assert not gate.accept(second[:32], second[32:]) and not gate.complete
