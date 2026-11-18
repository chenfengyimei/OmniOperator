"""Exact native switch notifications and post-copy proof over injected streams; no real display or input."""

from omni_test_support.v2.linux_key_switch import (
    BusinessError as BusinessError,
)
from omni_test_support.v2.linux_key_switch import (
    KeyPeer as KeyPeer,
)
from omni_test_support.v2.linux_key_switch import (
    SwitchPeer as SwitchPeer,
)
from omni_test_support.v2.linux_key_switch import (
    asyncio as asyncio,
)
from omni_test_support.v2.linux_key_switch import (
    classes as classes,
)
from omni_test_support.v2.linux_key_switch import (
    cleanup as cleanup,
)
from omni_test_support.v2.linux_key_switch import (
    context as context,
)
from omni_test_support.v2.linux_key_switch import (
    devices as devices,
)
from omni_test_support.v2.linux_key_switch import (
    key_count as key_count,
)
from omni_test_support.v2.linux_key_switch import (
    notifications as notifications,
)
from omni_test_support.v2.linux_key_switch import (
    packet_context as packet_context,
)
from omni_test_support.v2.linux_key_switch import (
    pytest as pytest,
)
from omni_test_support.v2.linux_key_switch import (
    struct as struct,
)


@pytest.mark.parametrize("count,found", [(1, True), (248, True), (248, False)])
def test_expected_switch_adopts_only_keyclass_source_after_full_postflight_and_keeps_window_lease(count, found):
    async def scenario():
        peer = SwitchPeer(count=count, found=found)
        inspector, leases, monitor, focus, packet = await packet_context(peer)
        before = monitor.topology
        try:
            assert await packet.emit() is None  # transport proof is not application success
            assert packet.events.transition.stage == 2 and packet.events.complete and packet.settled
            assert before != monitor.topology == devices(peer.topology) and not monitor.wire.changed
            assert peer.xkb.full_reads == [256, 5, 256, 5] * 2
            await focus.check()
            assert await focus.verify_key() == 40
            assert (await focus.verify_sources()).keyboard == 5
            assert not leases.desktop.connection.closed
            # A subsequent pair on the already-selected source has no switch.
            another = await focus.key_packet()
            another.wire.connection = peer
            await another.emit()
            assert another.events.transition.stage == 0 and another.events.complete
            assert len(peer.input_requests) == 4 and await focus.close()
        finally:
            await cleanup(inspector, leases, monitor)

    asyncio.run(scenario())


@pytest.mark.parametrize(
    "fault",
    [
        "keyboard_sequence",
        "keyboard_device",
        "keyboard_old_device",
        "keyboard_range",
        "keyboard_old_range",
        "keyboard_major",
        "keyboard_minor",
        "keyboard_changed",
        "device_sequence",
        "device_master",
        "device_classes",
        "device_source",
        "device_reason",
        "device_payload",
        "missing_keyboard",
        "missing_device",
        "duplicate_keyboard",
        "duplicate_device",
        "reverse",
        "no_notifications",
        "late",
        "topology_other",
        "topology_missing",
    ],
)
def test_unplanned_or_incomplete_switch_never_resets_the_intervention_latch_or_replays(fault):
    async def scenario():
        peer = SwitchPeer()
        inspector, leases, monitor, focus, packet = await packet_context(peer)
        before = monitor.topology
        peer.switch_fault = fault
        try:
            with pytest.raises(BusinessError):
                await packet.emit()
            assert packet.settled and not packet.release_pending and monitor.wire.changed
            assert monitor.topology == before and len(peer.input_requests) == 2
            with pytest.raises(BusinessError):
                await focus.check()
            with pytest.raises(BusinessError):
                await packet.emit()
            assert len(peer.input_requests) == 2 and await focus.close()
        finally:
            await cleanup(inspector, leases, monitor)

    asyncio.run(scenario())


@pytest.mark.parametrize("minor", [10, 13, 17, 19])
def test_equal_common_copy_change_after_owned_raw_events_cannot_rebind_original_configuration(minor):
    async def scenario():
        peer = SwitchPeer()
        inspector, leases, monitor, focus, packet = await packet_context(peer)
        peer.after_copy = lambda: setattr(peer.xkb, "copy_common", minor)
        before = monitor.topology
        try:
            with pytest.raises(BusinessError):
                await packet.emit()
            assert packet.events.complete and packet.settled and monitor.wire.changed
            assert monitor.topology == before
            peer.xkb.copy_common = None
            with pytest.raises(BusinessError):
                await focus.key_packet()
            assert len(peer.input_requests) == 2 and await focus.close()
        finally:
            await cleanup(inspector, leases, monitor)

    asyncio.run(scenario())


def test_different_keyclass_is_rejected_before_input_even_when_the_selected_symbol_matches():
    async def scenario():
        peer = SwitchPeer()
        changed = bytearray(peer.topology)
        offset = next(offset for identity, offset, _ in classes(peer.topology) if identity == 5)
        struct.pack_into("<I", changed, offset + 8, 41)
        peer.topology = bytes(changed)
        inspector, leases, monitor, focus = await context(peer)
        try:
            with pytest.raises(BusinessError):
                await focus.key_packet()
            assert not peer.input_requests and monitor.wire.key_lease is None
            with pytest.raises(BusinessError):
                await focus.key_packet()
        finally:
            await cleanup(inspector, leases, monitor)

    asyncio.run(scenario())


def test_second_topology_read_rejects_a_change_during_copied_configuration_readback():
    async def scenario():
        peer = SwitchPeer()
        inspector, leases, monitor, focus, packet = await packet_context(peer)
        before = monitor.topology

        def change(_peer, selected):
            if len(peer.xkb.full_reads) == 8 and selected == 5:
                changed = bytearray(peer.topology)
                changed[44] ^= 1
                peer.topology = bytes(changed)

        peer.xkb.full_hook = change
        try:
            with pytest.raises(BusinessError):
                await packet.emit()
            assert packet.settled and packet.events.complete and monitor.wire.changed
            assert monitor.topology == before and len(peer.input_requests) == 2
            assert await focus.close()
        finally:
            await cleanup(inspector, leases, monitor)

    asyncio.run(scenario())


def test_cancelled_postflight_latches_the_plan_after_release_without_replaying_or_dropping_window_ownership():
    async def scenario():
        peer = SwitchPeer()
        inspector, leases, monitor, focus, packet = await packet_context(peer)
        before = monitor.topology

        def cancel(_peer, _selected):
            raise asyncio.CancelledError()

        peer.xkb.full_hook = cancel
        try:
            with pytest.raises(asyncio.CancelledError):
                await packet.emit()
            assert packet.settled and packet.events.complete and not packet.release_pending
            assert monitor.topology == before and monitor.wire.changed
            assert monitor.wire.key_lease is None and not leases.desktop.connection.closed
            peer.xkb.full_hook = None
            with pytest.raises(BusinessError):
                await focus.key_packet()
            assert len(peer.input_requests) == 2 and await focus.close()
        finally:
            await cleanup(inspector, leases, monitor)

    asyncio.run(scenario())
