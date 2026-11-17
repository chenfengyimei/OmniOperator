"""Copied XKB records over independent binary layouts; no X server or real input is used."""

import asyncio
import struct

import pytest
from test_v2_linux_keystroke import KeyPeer, cleanup
from test_v2_linux_xkb_map import context
from v2_xkb_copy_helpers import copied_reply, reply

from omni.adapters.platform.linux_xkb_copy import compatibility, geometry, indicators, names
from omni.domain.contracts import BusinessError


def identity(minor, data, device=3):
    if minor == 17:
        return names(data, device, 8, 255)
    return {10: compatibility, 13: indicators, 19: geometry}[minor](data, device)


@pytest.mark.parametrize("minor", [10, 13, 17, 19])
def test_copied_configuration_is_equal_across_device_sequence_and_documented_padding(minor):
    first = copied_reply(minor, 10, 3)
    second = copied_reply(minor, 11, 5, padding=233)
    assert identity(minor, first) == identity(minor, second, 5)


@pytest.mark.parametrize("minor", [10, 13, 17, 19])
@pytest.mark.parametrize("fault", ["device", "length", "truncated", "trailing", "count"])
def test_each_native_record_requires_bound_identity_lengths_counts_and_exact_tail(minor, fault):
    data = bytearray(copied_reply(minor, 10, 3))
    if fault == "device":
        data[1] = 4
    elif fault == "length":
        struct.pack_into("<I", data, 4, 65536)
    elif fault == "truncated":
        del data[-4:]
        struct.pack_into("<I", data, 4, (len(data) - 32) // 4)
    elif fault == "trailing":
        data += b"four"
        struct.pack_into("<I", data, 4, (len(data) - 32) // 4)
    elif minor == 10:
        struct.pack_into("<H", data, 12, 2)
    elif minor == 13:
        data[16] = 31
    elif minor == 17:
        struct.pack_into("<H", data, 26, 8)
    else:
        struct.pack_into("<H", data, 18, 257)
    with pytest.raises(BusinessError):
        identity(minor, bytes(data))


@pytest.mark.parametrize(
    "minor,offset", [(10, 32), (10, 48), (13, 12), (13, 32), (17, 32), (17, -1), (19, 8), (19, -1)]
)
def test_meaningful_copied_data_cannot_be_discarded_as_padding(minor, offset):
    before = copied_reply(minor, 10, 3)
    after = bytearray(before)
    after[offset] ^= 2
    assert identity(minor, before) != identity(minor, bytes(after))


def test_absent_current_geometry_is_supported_but_cannot_hide_a_payload_or_present_fields():
    empty = copied_reply(19, 10, 3, found=False)
    assert geometry(empty, 3) == geometry(copied_reply(19, 11, 5, found=False, padding=233), 5)
    changed = bytearray(empty)
    changed[18] = 1
    with pytest.raises(BusinessError):
        geometry(bytes(changed), 3)
    changed = bytearray(empty) + bytes(4)
    struct.pack_into("<I", changed, 4, 1)
    with pytest.raises(BusinessError):
        geometry(bytes(changed), 3)


def test_absent_optional_key_names_do_not_turn_the_retained_full_range_into_a_payload_count():
    data = reply(
        10, 3, {8: struct.pack("<I", 0), 12: bytes([8, 255, 0, 0]), 16: bytes([0, 0, 8, 248]), 20: bytes(8)}, b""
    )
    changed = bytearray(data)
    changed[18:20] = bytes(2)
    assert names(data, 3, 8, 255) == names(bytes(changed), 3, 8, 255)


@pytest.mark.parametrize("minor", [10, 13, 17, 19])
def test_source_copy_difference_is_refused_before_a_key_packet_or_release_lease_exists(minor):
    async def scenario():
        peer = KeyPeer()
        inspector, leases, monitor, focus = await context(peer)
        peer.xkb.copy_fault = minor
        try:
            with pytest.raises(BusinessError):
                await focus.key_packet()
            assert not peer.input_requests and monitor.wire.key_lease is None
            peer.xkb.copy_fault = None
            before = len(peer.sent)
            with pytest.raises(BusinessError):
                await focus.key_packet()
            assert len(peer.sent) == before
        finally:
            await cleanup(inspector, leases, monitor)

    asyncio.run(scenario())


def test_every_copy_query_is_read_only_bound_to_current_geometry_and_repeated_for_both_devices():
    async def scenario():
        peer = KeyPeer()
        inspector, leases, monitor, focus = await context(peer)
        try:
            await focus.key_packet()
            assert peer.xkb.copy_reads == [(device, minor) for device in (256, 5, 256, 5) for minor in (10, 13, 17, 19)]
            assert all(minor in {0, 1, 4, 6, 8, 10, 13, 17, 19} for opcode, minor, _ in peer.sent if opcode == 131)
            assert not peer.input_requests and monitor.wire.key_lease is None
        finally:
            await cleanup(inspector, leases, monitor)

    asyncio.run(scenario())


@pytest.mark.parametrize("minor", [10, 13, 17, 19])
def test_a_later_equal_change_in_both_keyboards_cannot_rebind_the_original_copy_identity(minor):
    async def scenario():
        peer = KeyPeer()
        inspector, leases, monitor, focus = await context(peer)
        try:
            await focus.key_packet()
            peer.xkb.copy_common = minor
            with pytest.raises(BusinessError):
                await focus.key_packet()
            peer.xkb.copy_common = None
            with pytest.raises(BusinessError):
                await focus.key_packet()
            assert not peer.input_requests
        finally:
            await cleanup(inspector, leases, monitor)

    asyncio.run(scenario())


@pytest.mark.parametrize("subtype", [5, 6])
def test_new_indicator_map_or_name_notifications_invalidate_the_plan_without_a_keyboard_write(subtype):
    async def scenario():
        peer = KeyPeer()
        inspector, leases, monitor, focus = await context(peer)
        event = bytearray(32)
        event[0:2] = bytes([80, subtype])
        event[8] = 5
        peer.event = bytes(event)
        try:
            with pytest.raises(BusinessError):
                await focus.key_packet()
            assert monitor.wire.changed and not peer.input_requests
        finally:
            await cleanup(inspector, leases, monitor)

    asyncio.run(scenario())
