"""Paired protocol dispatch over fake streams with exact bytes/events; never contacts native devices."""

import asyncio
import struct

import pytest
from test_v2_linux_foreground import prepared
from test_v2_linux_keyboard import KeyboardPeer
from test_v2_linux_xtest import SourcePeer

from omni.adapters.platform.linux_input import KeyEvents
from omni.domain.contracts import BusinessError


def raw(sequence, category, device, *, source=5, code=40, timestamp=1000, words=2, flags=0, values=None):
    data = bytearray(32)
    data[0:2] = bytes([35, 130])
    struct.pack_into("<HIHHIIHHI", data, 2, sequence, words, category, device, timestamp, code, source, words, flags)
    return bytes(data) + (b"\0" * (words * 4) if values is None else values)


class KeyPeer(KeyboardPeer, SourcePeer):
    def __init__(self):
        super().__init__()
        self.input_requests = []
        self.stream = bytearray()
        self.write_plan = []
        self.raw_fault = None
        self.block_query = False
        self.query_started = asyncio.Event()
        self.query_release = asyncio.Event()
        self.query_count = 0
        self.owner = None

    @property
    def closed(self):
        return self.owner is not None and self.owner.closed

    def send(self, data):
        assert not self.closed
        if self.write_plan:
            choice = self.write_plan.pop(0)
            if isinstance(choice, BaseException):
                raise choice
            size = min(choice, len(data))
        else:
            size = len(data)
        self.stream.extend(data[:size])
        while len(self.stream) >= 36:
            request = bytes(self.stream[:36])
            del self.stream[:36]
            # Independently encoded public XTEST FakeInput layout, including
            # zero time/root/coordinates/core-device and exact request length.
            kind = request[4]
            assert request == bytes([132, 2, 9, 0, kind, 40, 0, 0]) + b"\0" * 28
            assert kind in {2, 3}
            self.sequence += 1
            self.input_requests.append(request)
            category = 13 if kind == 2 else 14
            slave = raw(self.sequence, category, 5)
            master = raw(self.sequence, category, 3)
            fault = self.raw_fault
            if fault == "source":
                slave = raw(self.sequence, category, 5, source=6)
            elif fault == "sequence":
                slave = raw(self.sequence - 1, category, 5)
            elif fault == "flags":
                slave = raw(self.sequence, category, 5, flags=1 << 16)
            elif fault == "valuators":
                slave = raw(self.sequence, category, 5, values=b"\1" + b"\0" * 7)
            elif fault == "order":
                slave, master = master, slave
            if fault != "missing":
                self.incoming.extend(slave + master)
            if fault == "other_client" and kind == 2:
                self.incoming.extend(slave + master)
            if fault == "device_change":
                event = bytearray(32)
                event[0:2] = bytes([35, 130])
                struct.pack_into("<H", event, 8, 1)
                self.incoming.extend(event)
        return size

    async def sock_sendall(self, connection, packet):
        if self.block_query and packet[0] == 44:
            self.query_count += 1
            self.query_started.set()
            await self.query_release.wait()
        return await super().sock_sendall(connection, packet)


async def packet_context(peer):
    _factory, inspector, leases, alias, monitor, _connections, focus = await prepared(peer)
    await focus.prepare(alias)
    await focus.prepare_key("Enter")
    await focus.prepare_sources()
    packet = await focus.key_packet()
    peer.owner = monitor.connection
    packet.wire.connection = peer
    return inspector, leases, monitor, focus, packet


async def cleanup(inspector, leases, monitor):
    # A negative settlement assertion must not be turned into success by test
    # cleanup; close the injected resources directly after recording it.
    assert await monitor.connection.close()
    assert await leases.desktop.connection.close()
    assert await inspector.close()


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
