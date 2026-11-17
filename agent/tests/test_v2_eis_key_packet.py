"""EI paired-key framing and original write/query/release ownership; all native ports are injected."""

import asyncio
import struct

import pytest
from v2_eis_helpers import ORDER, word
from v2_eis_key_packet_helpers import guard, prepared

from omni.domain.contracts import BusinessError


@pytest.mark.parametrize("partial", [1, 7, 48, 4096])
def test_distinct_frames_remapped_evdev_serial_time_and_one_consumed_packet(monkeypatch, tmp_path, partial):
    async def scenario():
        async with prepared(monkeypatch, tmp_path, partial=partial) as (peer, wire, packet):
            before = wire._sent
            guards = []

            async def permit():
                assert not peer.started
                guards.append("permit")

            await packet.emit(permit)
            assert guards == ["permit"] and packet.transport_verified and packet.settled
            assert packet.position == len(packet.packet) == 176 and packet.press_end == 48
            assert bytes(peer.stream[:176]) == packet.packet
            assert peer.frames == [((159, 1),), ((159, 0),)]
            assert peer.effects == [(159, 1), (159, 0)] and not peer.logical and not peer.emulating
            assert not packet.release_pending and wire._input_lease is None and not wire._busy
            assert wire._sent - before == 176 + 28 * 2  # pre/post inventory barriers, cumulative accounting
            with pytest.raises(BusinessError):
                await packet.emit(permit)
            with pytest.raises(BusinessError):
                await wire.key_packet()
            assert peer.keys == [(159, 1), (159, 0)]

    asyncio.run(scenario())


@pytest.mark.parametrize("failure_at", ["permit", "epoch", "map", "budget", "clock", "callback_id"])
def test_refusal_before_first_input_has_no_key_request(monkeypatch, tmp_path, failure_at):
    async def scenario():
        async with prepared(monkeypatch, tmp_path) as (peer, wire, packet):

            async def permit():
                if failure_at == "permit":
                    raise BusinessError("fixture_denied", "denied")
                if failure_at == "epoch":
                    device = peer.device_ids[0]
                    peer.emit(device, 8, word(peer.serial + 1))
                    peer.emit(device, 7, word(peer.serial + 2))
                elif failure_at == "map":
                    wire._keymaps[0].data = b"!" + wire._keymaps[0].data[1:]
                elif failure_at == "budget":
                    wire._sent = 65536 - 100
                elif failure_at == "clock":
                    packet.clock = lambda: True
                elif failure_at == "callback_id":
                    wire._client_id = 0xFF00000000000000 - 2

            with pytest.raises(BusinessError):
                await packet.emit(permit)
            assert packet.attempted and not peer.started and not peer.keys
            assert wire._input_lease is None

    asyncio.run(scenario())


@pytest.mark.parametrize("boundary", [0, 17, 24, 47, 48, 55, 76, 93, 100, 127, 148, 175])
def test_cancel_preserves_cursor_and_only_settles_a_completed_press(monkeypatch, tmp_path, boundary):
    async def scenario():
        async with prepared(monkeypatch, tmp_path) as (peer, _wire, packet):
            peer.boundary = boundary
            task = asyncio.create_task(packet.emit(guard))
            await peer.write_entered.wait()
            assert packet.position == boundary
            task.cancel()
            with pytest.raises(asyncio.CancelledError):
                await task
            assert not packet.transport_verified and packet.abort
            peer.write_release.set()
            await packet._writer
            assert await packet.settle()
            assert packet.settled and not packet.release_pending
            if boundary < 48:
                assert packet.dropped and peer.closed and not peer.keys and not peer.effects
                assert packet.position == boundary
            else:
                assert not packet.dropped and not peer.closed
                assert peer.keys == [(159, 1), (159, 0)] and not peer.logical
                assert bytes(peer.stream[:176]) == packet.packet

    asyncio.run(scenario())


def test_actual_write_timeout_keeps_completed_down_and_close_finishes_only_suffix(monkeypatch, tmp_path):
    async def scenario():
        async with prepared(monkeypatch, tmp_path) as (peer, wire, packet):
            peer.boundary = 76
            task = asyncio.create_task(packet.emit(guard))
            await peer.write_entered.wait()
            with pytest.raises(BusinessError):
                await task
            assert packet.release_pending and not packet.transport_verified and not peer.closed
            assert peer.effects == [(159, 1)] and peer.logical == {159}
            assert not await wire.close() and wire.descriptor.descriptor >= 0
            assert wire._input_lease is packet and not wire._keymaps[0].closed and wire._keymaps[0].data
            peer.write_release.set()
            assert await wire.close()
            assert packet.settled and peer.effects == [(159, 1), (159, 0)] and not peer.logical and peer.closed
            assert bytes(peer.stream[:176]) == packet.packet

    asyncio.run(scenario())


def test_partial_original_barrier_survives_emit_timeout_close_waiter_cancel_and_late_reply(monkeypatch, tmp_path):
    async def scenario():
        async with prepared(monkeypatch, tmp_path) as (peer, wire, packet):
            peer.hold_barrier = True
            with pytest.raises(BusinessError):
                await packet.emit(guard)
            query = packet._query
            assert query is not None and not query.done() and packet.release_pending
            assert packet.position == 176 and peer.syncs_after_input == 1 and not peer.closed
            with pytest.raises(BusinessError):
                await wire.verify_key()
            close = asyncio.create_task(wire.close())
            await asyncio.sleep(0)
            close.cancel()
            with pytest.raises(asyncio.CancelledError):
                await close
            assert packet._query is query and not query.cancelled() and not peer.closed
            peer.finish_barrier()
            assert await wire.close() and packet.settled
            assert packet._query is query and query.result() is True and peer.syncs_after_input == 1
            assert peer.keys == [(159, 1), (159, 0)] and not packet.transport_verified

    asyncio.run(scenario())


@pytest.mark.parametrize("fault", ["value", "object", "disconnect"])
def test_failed_original_barrier_never_replaced_or_replayed(monkeypatch, tmp_path, fault):
    async def scenario():
        async with prepared(monkeypatch, tmp_path) as (peer, wire, packet):
            peer.barrier_fault = fault
            with pytest.raises(BusinessError):
                await packet.emit(guard)
            query = packet._query
            assert query.done() and query.exception() is not None
            peer.barrier_fault = None
            assert not await wire.close() and not await packet.settle()
            assert packet._query is query and packet.release_pending and not peer.closed
            assert peer.syncs_after_input == 1 and peer.keys == [(159, 1), (159, 0)]

    asyncio.run(scenario())


@pytest.mark.parametrize("change", ["pause", "pause_resume", "destroy", "keyboard_remove", "modifiers"])
def test_lifecycle_change_is_unknown_and_only_pause_proves_source_reset(monkeypatch, tmp_path, change):
    async def scenario():
        async with prepared(monkeypatch, tmp_path) as (peer, wire, packet):

            def changed(value):
                value.barrier_hook = None
                device, keyboard = packet.plan.device, packet.plan.keyboard
                serial = word(peer.serial + 1)
                if change.startswith("pause"):
                    value.emit(device, 8, serial)
                    if change == "pause_resume":
                        value.emit(device, 7, word(peer.serial + 2))
                elif change == "destroy":
                    value.emit(device, 0, serial)
                elif change == "keyboard_remove":
                    value.emit(keyboard, 0, serial)
                else:
                    value.emit(keyboard, 3, serial + struct.pack(ORDER + "IIII", 1, 0, 0, 0))

            peer.barrier_hook = changed
            with pytest.raises(BusinessError):
                await packet.emit(guard)
            assert not packet.transport_verified
            if change.startswith("pause"):
                assert packet.settled and packet.pause_seen and await wire.close()
            else:
                assert packet.release_pending and not packet.settled and not await wire.close()
                assert wire._input_lease is packet and not peer.closed
            assert peer.keys == [(159, 1), (159, 0)]

    asyncio.run(scenario())


def test_valid_barrier_with_nonzero_modifiers_can_refresh_read_only_without_repeating_key(monkeypatch, tmp_path):
    async def scenario():
        async with prepared(monkeypatch, tmp_path) as (peer, _wire, packet):
            keyboard = packet.plan.keyboard

            def modified(value):
                value.barrier_hook = None
                value.emit(keyboard, 3, word(peer.serial + 1) + struct.pack(ORDER + "IIII", 1, 0, 0, 0))

            peer.barrier_hook = modified
            with pytest.raises(BusinessError):
                await packet.emit(guard)
            first = packet._query
            peer.emit(keyboard, 3, word(peer.serial + 2) + bytes(16))
            assert await packet.settle() and packet._query is not first and packet.settled
            assert peer.syncs_after_input == 2 and peer.keys == [(159, 1), (159, 0)]
            assert not packet.transport_verified

    asyncio.run(scenario())


@pytest.mark.parametrize("boundary", [0, 76])
def test_invalid_write_count_is_uncertain_and_never_discarded_or_retried(monkeypatch, tmp_path, boundary):
    async def scenario():
        async with prepared(monkeypatch, tmp_path) as (peer, wire, packet):
            peer.boundary, peer.write_mode = boundary, "invalid"
            with pytest.raises(BusinessError):
                await packet.emit(guard)
            assert packet.unknown_write and not packet.dropped and not packet.settled
            original = bytes(peer.stream)
            assert not await packet.settle() and not await wire.close() and not peer.closed
            assert bytes(peer.stream) == original

    asyncio.run(scenario())


@pytest.mark.parametrize("boundary", [0, 76])
def test_zero_write_discards_incomplete_down_or_retains_release_for_completed_down(monkeypatch, tmp_path, boundary):
    async def scenario():
        async with prepared(monkeypatch, tmp_path) as (peer, wire, packet):
            peer.boundary, peer.write_mode = boundary, "zero"
            with pytest.raises(BusinessError):
                await packet.emit(guard)
            if boundary == 0:
                assert packet.dropped and packet.settled and peer.closed and not peer.keys
            else:
                assert packet.release_pending and not packet.settled and not peer.closed
                peer.write_release.set()
                assert await wire.close()
                assert peer.keys == [(159, 1), (159, 0)] and not peer.logical

    asyncio.run(scenario())


def test_close_before_owned_writer_starts_aborts_new_down_and_preserves_no_input_result(monkeypatch, tmp_path):
    async def scenario():
        async with prepared(monkeypatch, tmp_path) as (peer, wire, packet):
            original_build = packet._build
            closes = []

            def build():
                original_build()
                # Scheduled before emit creates the writer. This reproduces
                # owner shutdown arriving before the first owned I/O task.
                closes.append(asyncio.create_task(wire.close()))

            packet._build = build
            with pytest.raises(BusinessError):
                await packet.emit(guard)
            assert await closes[0]
            assert packet.settled and packet.dropped and packet.position == 0
            assert not peer.started and not peer.keys and peer.closed and not packet.transport_verified

    asyncio.run(scenario())


def test_concurrent_settlement_waiters_keep_one_original_query_and_one_key_pair(monkeypatch, tmp_path):
    async def scenario():
        async with prepared(monkeypatch, tmp_path) as (peer, _wire, packet):
            peer.hold_barrier = True
            emit = asyncio.create_task(packet.emit(guard))
            await peer.read_entered.wait()
            original = packet._query
            waiters = [asyncio.create_task(packet.settle()) for _ in range(4)]
            await asyncio.sleep(0)
            waiters[0].cancel()
            with pytest.raises(asyncio.CancelledError):
                await waiters[0]
            peer.finish_barrier()
            assert all(await asyncio.gather(*waiters[1:]))
            await emit
            assert packet._query is original and packet.transport_verified and packet.settled
            assert peer.keys == [(159, 1), (159, 0)] and peer.syncs_after_input == 2

    asyncio.run(scenario())


def test_incomplete_packet_detach_failure_keeps_original_descriptor_until_actual_detach(monkeypatch, tmp_path):
    async def scenario():
        async with prepared(monkeypatch, tmp_path) as (peer, wire, packet):
            peer.boundary, peer.write_mode = 17, "zero"
            original = peer.detach

            def failed_detach():
                raise OSError("fixture-borrower-retains-fd")

            peer.detach = failed_detach
            with pytest.raises(BusinessError):
                await packet.emit(guard)
            assert packet.dropped and not packet.settled and wire._input_lease is packet
            assert not peer.closed and wire.descriptor.descriptor >= 0 and not peer.keys
            assert not await wire.close()
            peer.detach = original
            assert await wire.close() and packet.settled and peer.closed
            assert packet.position == 17 and not peer.keys

    asyncio.run(scenario())


def test_invalid_count_during_release_continuation_keeps_ambiguous_original_cursor(monkeypatch, tmp_path):
    async def scenario():
        async with prepared(monkeypatch, tmp_path) as (peer, wire, packet):
            peer.boundary, peer.write_mode = 76, "zero"
            with pytest.raises(BusinessError):
                await packet.emit(guard)
            assert packet.release_pending and not packet.unknown_write
            peer.write_mode = "invalid"
            assert not await packet.settle()
            assert packet.unknown_write and packet.position == 76 and not packet.settled
            peer.write_release.set()
            assert not await wire.close() and not peer.closed
            assert peer.keys == [(159, 1)] and peer.effects == [(159, 1)] and peer.syncs_after_input == 0

    asyncio.run(scenario())


@pytest.mark.parametrize("pending", ["barrier", "up"])
def test_actual_control_session_keeps_original_portal_and_rules_until_key_release_settles(
    monkeypatch, tmp_path, pending
):
    from test_v2_remote_desktop import factory
    from v2_eis_device_helpers import ports
    from v2_eis_key_packet_helpers import KeyPeer
    from v2_xkbcommon_helpers import Library

    from omni.adapters.platform.linux_portal import SESSION
    from omni.adapters.platform.linux_remote_desktop import ControlSession
    from omni.adapters.platform.linux_session import SessionInspector
    from omni.adapters.platform.linux_xkbcommon import NativeXkb, XkbCompiler

    async def scenario():
        ports(monkeypatch)
        peer, platform = KeyPeer(tmp_path), factory()
        bus = platform.buses["user"]
        connections = []

        async def connect(owner, session):
            connections.append((owner, session))
            return peer.owned

        bus.connect_eis = connect
        native = ControlSession(
            SessionInspector(platform.ops, platform),
            1,
            device_factory=lambda descriptor, check, mask: peer.connection(check),
        )

        async def phase(value):
            pass

        wire = packet = None
        try:
            await native.prepare()
            await native.authorize(phase)
            wire = await native.prepare_eis(with_devices=True)
            await wire.bind(wire.seats()[0].identity)
            await wire.prepare_key(
                peer.device_ids[0],
                "Enter",
                compiler_factory=lambda: XkbCompiler(lambda: NativeXkb(loader=lambda name: Library(code=167))),
            )
            packet = await wire.key_packet(clock=lambda: 123456789)
            peer.armed = True
            peer.hold_barrier = pending == "barrier"
            if pending == "up":
                peer.boundary, peer.write_mode = 76, "zero"
            with pytest.raises(BusinessError):
                await packet.emit(guard)
            assert packet.release_pending and not await native.close()
            assert native._eis is wire and len(connections) == 1 and bus.rules and not peer.closed
            assert native._granted == 0 and not packet.transport_verified
            assert not any(item[1:3] == (SESSION, "Close") for item in bus.requests)
            peer.write_release.set()
            peer.finish_barrier()
            assert await native.close() and peer.closed and packet.settled and not bus.rules
            assert any(item[1:3] == (SESSION, "Close") for item in bus.requests)
            assert peer.keys == [(159, 1), (159, 0)] and not peer.logical
            assert await native.inspector.close()
        finally:
            peer.write_release.set()
            peer.finish_barrier()
            if not await native.close() and wire is not None:
                # Do not mask the original assertion with cleanup. Only fake
                # pipe/socket resources are forcibly reclaimed on test failure.
                if packet is not None:
                    for task in (packet._writer, packet._query):
                        if task is not None:
                            task.cancel()
                            await asyncio.gather(task, return_exceptions=True)
                if wire._keyboard is not None:
                    await wire._keyboard.close()
                for keymap in wire._keymaps:
                    await keymap.close()
                wire._close_handles()
            await native.inspector.close()
            peer.release_files()

    asyncio.run(scenario())
