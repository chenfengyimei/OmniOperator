"""Exact native switch notifications and post-copy proof over injected streams; no real display or input."""

import asyncio
import struct

import pytest
from test_v2_linux_keystroke import KeyPeer, cleanup, packet_context
from test_v2_linux_xkb_map import context

from omni.adapters.platform.desktop.linux.linux_input import devices
from omni.domain.contracts import BusinessError


def classes(data):
    position = 32
    for _ in range(struct.unpack_from("<H", data, 8)[0]):
        identity, _kind, _attachment, count, size = struct.unpack_from("<HHHHH", data, position)
        position += 12 + (size + 3) // 4 * 4
        for _ in range(count):
            words = struct.unpack_from("<H", data, position + 2)[0]
            yield identity, position, data[position : position + words * 4]
            position += words * 4
    assert position == len(data)


def key_count(data, count):
    result, previous = bytearray(), 0
    for identity, offset, value in classes(data):
        if identity in {3, 5}:
            result.extend(data[previous:offset])
            result.extend(struct.pack("<HHHH", 0, count + 2, identity, count))
            result.extend(struct.pack("<" + "I" * count, *range(8, 8 + count)))
            previous = offset + len(value)
    return bytes(result) + data[previous:]


def notifications(sequence, source, *, found=True):
    keyboard = bytearray(32)
    keyboard[:2] = bytes([80, 0])
    struct.pack_into("<HI", keyboard, 2, sequence, 1000)
    keyboard[8:16] = bytes([3, 3, 8, 255, 8, 255, 131, 9])
    struct.pack_into("<H", keyboard, 16, 3 if found else 1)
    device = bytearray(32)
    device[:2] = bytes([35, 130])
    struct.pack_into("<HIHHIHHB", device, 2, sequence, len(source) // 4, 1, 3, 1000, 1, 5, 1)
    return keyboard, device


class SwitchPeer(KeyPeer):
    def __init__(self, *, count=1, found=True):
        super().__init__()
        self.topology = key_count(self.topology, count)
        self.xkb.geometry_present = found
        self.switch_fault = None
        self.switched = False
        self.after_copy = None

    def send(self, data):
        start, requests, sequence = len(self.incoming), len(self.input_requests), self.sequence
        sent = super().send(data)
        if self.switched or len(self.input_requests) == requests:
            return sent
        self.switched = True
        emitted = bytes(self.incoming[start:])
        del self.incoming[start:]
        source = next(value for identity, _, value in classes(self.topology) if identity == 5)
        keyboard, device = notifications(sequence + 1, source, found=self.xkb.geometry_present)
        changed = bytearray(self.topology)
        master = next(offset for identity, offset, _ in classes(self.topology) if identity == 3)
        struct.pack_into("<H", changed, master + 4, 5)
        fault = self.switch_fault
        if fault == "keyboard_sequence":
            struct.pack_into("<H", keyboard, 2, sequence)
        elif fault == "keyboard_device":
            keyboard[8] = 5
        elif fault == "keyboard_old_device":
            keyboard[9] = 5
        elif fault == "keyboard_range":
            keyboard[10] = 9
        elif fault == "keyboard_old_range":
            keyboard[12] = 9
        elif fault == "keyboard_major":
            keyboard[14] = 132
        elif fault == "keyboard_minor":
            keyboard[15] = 8
        elif fault == "keyboard_changed":
            keyboard[16] ^= 2
        elif fault == "device_sequence":
            struct.pack_into("<H", device, 2, sequence)
        elif fault == "device_master":
            struct.pack_into("<H", device, 10, 5)
        elif fault == "device_classes":
            struct.pack_into("<H", device, 16, 2)
        elif fault == "device_source":
            struct.pack_into("<H", device, 18, 6)
        elif fault == "device_reason":
            device[20] = 2
        elif fault == "device_payload":
            source = source[:-4] + bytes(4)
        if fault == "missing_keyboard":
            keyboard = b""
        if fault == "missing_device":
            device, source = b"", b""
        prefix = bytes(keyboard) + bytes(device) + source
        if fault == "duplicate_keyboard":
            prefix = bytes(keyboard) + prefix
        elif fault == "duplicate_device":
            prefix += bytes(device) + source
        elif fault == "reverse":
            prefix = bytes(device) + source + bytes(keyboard)
        elif fault == "no_notifications":
            prefix = b""
        elif fault == "topology_other":
            changed[44] ^= 1  # master pointer name, outside the permitted key class
        elif fault == "topology_missing":
            changed = self.topology
        if fault == "late":
            self.incoming.extend(emitted + prefix)
        else:
            self.incoming.extend(prefix + emitted)
        self.topology = bytes(changed)
        if self.after_copy is not None:
            self.after_copy()
        return sent


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
