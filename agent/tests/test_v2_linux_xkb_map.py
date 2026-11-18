"""Whole source/core maps on independent binary peers; no native Xorg or input."""

import asyncio
import struct

import pytest
from test_v2_linux_foreground import prepared
from test_v2_linux_keystroke import KeyPeer, cleanup
from v2_xkb_map_helpers import complete_map

from omni.adapters.platform.desktop.linux.linux_xkb_map import map_identity
from omni.domain.contracts import BusinessError


@pytest.mark.parametrize("maximum", [254, 255])
def test_all_components_have_stable_identity_across_response_ids_sequence_padding_and_no_action_unused_bytes(maximum):
    first = complete_map(10, 3, maximum=maximum)
    second = complete_map(11, 5, maximum=maximum, padding=233)
    assert map_identity(first, 3, 8, maximum) == map_identity(second, 5, 8, maximum)


@pytest.mark.parametrize(
    "fault", ["device", "bounds", "parts", "types", "range", "symbols", "actions", "truncated", "trailing"]
)
def test_full_map_requires_exact_ranges_counts_components_and_complete_payload(fault):
    value = bytearray(complete_map(10, 3))
    if fault == "device":
        value[1] = 4
    elif fault == "bounds":
        value[10] = 9
    elif fault == "parts":
        value[12] = 190
    elif fault == "types":
        value[16] = 3
    elif fault == "range":
        value[20] = 247
    elif fault == "symbols":
        struct.pack_into("<H", value, 18, 0)
    elif fault == "actions":
        struct.pack_into("<H", value, 22, 0)
    elif fault == "truncated":
        del value[-4:]
        struct.pack_into("<I", value, 4, (len(value) - 32) // 4)
    else:
        value += b"four"
        struct.pack_into("<I", value, 4, (len(value) - 32) // 4)
    with pytest.raises(BusinessError):
        map_identity(bytes(value), 3, 8, 255)


@pytest.mark.parametrize("offset", [40, 50, 58, -2, -7, -16, -19, -23, -40, -32, -31, -30, -12, -11, -8, -4])
def test_nonpadding_type_preserve_action_behavior_and_modifier_changes_remain_in_identity(offset):
    before = complete_map(10, 3)
    after = bytearray(before)
    after[offset] ^= 2
    try:
        changed = map_identity(bytes(after), 3, 8, 255)
    except BusinessError:
        return
    assert changed != map_identity(before, 3, 8, 255)


async def context(peer):
    _factory, inspector, leases, alias, monitor, _connections, focus = await prepared(peer)
    await focus.prepare(alias)
    await focus.prepare_key("Enter")
    await focus.prepare_sources()
    return inspector, leases, monitor, focus


def test_same_full_source_map_controls_and_state_are_fenced_twice_before_a_packet_can_exist():
    async def scenario():
        peer = KeyPeer()
        inspector, leases, monitor, focus = await context(peer)
        try:
            packet = await focus.key_packet()
            assert peer.xkb.full_reads == [256, 5, 256, 5]
            assert not peer.input_requests and monitor.wire.key_lease is None
            peer.owner = monitor.connection
            packet.wire.connection = peer
            await packet.emit()
            assert len(peer.input_requests) == 2 and packet.settled
        finally:
            await cleanup(inspector, leases, monitor)

    asyncio.run(scenario())


@pytest.mark.parametrize("fault", ["symbol", "type", "modifier", "controls", "state", "device"])
def test_source_changes_outside_the_selected_key_are_rejected_before_any_input_and_cannot_revive(fault):
    async def scenario():
        peer = KeyPeer()
        inspector, leases, monitor, focus = await context(peer)
        peer.xkb.clone_fault = fault
        try:
            with pytest.raises(BusinessError):
                await focus.key_packet()
            assert not peer.input_requests and monitor.wire.key_lease is None
            peer.xkb.clone_fault = None
            before = len(peer.sent)
            with pytest.raises(BusinessError):
                await focus.key_packet()
            with pytest.raises(BusinessError):
                await focus.check()
            assert len(peer.sent) == before
        finally:
            await cleanup(inspector, leases, monitor)

    asyncio.run(scenario())


def test_source_map_change_between_the_four_reads_is_not_hidden_by_equal_final_core_rows():
    async def scenario():
        peer = KeyPeer()
        inspector, leases, monitor, focus = await context(peer)

        def change(_peer, selected):
            if selected == 5:
                peer.xkb.clone_fault = "symbol"

        peer.xkb.full_hook = change
        try:
            with pytest.raises(BusinessError):
                await focus.key_packet()
            assert peer.xkb.full_reads == [256, 5, 256, 5] and not peer.input_requests
        finally:
            await cleanup(inspector, leases, monitor)

    asyncio.run(scenario())


def test_an_old_full_map_proof_cannot_rebind_to_equal_but_changed_core_and_source_types():
    async def scenario():
        peer = KeyPeer()
        inspector, leases, monitor, focus = await context(peer)
        try:
            await focus.key_packet()
            peer.xkb.clone_version = 2
            with pytest.raises(BusinessError):
                await focus.key_packet()
            peer.xkb.clone_version = 0
            with pytest.raises(BusinessError):
                await focus.key_packet()
            assert not peer.input_requests and monitor.wire.key_lease is None
        finally:
            await cleanup(inspector, leases, monitor)

    asyncio.run(scenario())


def test_cancelled_source_read_latches_the_proof_without_creating_a_dispatch_or_release_lease():
    async def scenario():
        peer = KeyPeer()
        inspector, leases, monitor, focus = await context(peer)

        def cancel(_peer, selected):
            if selected == 5:
                raise asyncio.CancelledError()

        peer.xkb.full_hook = cancel
        try:
            with pytest.raises(asyncio.CancelledError):
                await focus.key_packet()
            peer.xkb.full_hook = None
            with pytest.raises(BusinessError):
                await focus.key_packet()
            assert not peer.input_requests and monitor.wire.key_lease is None
        finally:
            await cleanup(inspector, leases, monitor)

    asyncio.run(scenario())
