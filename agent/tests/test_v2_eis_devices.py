"""Real device protocol bytes, sparse masks and temporary keymap FDs on injected EI peers; no native input."""

import asyncio
import hashlib
import os
import struct
import threading

import pytest
from test_v2_remote_desktop import factory
from v2_eis_device_helpers import KEYMAP, DevicePeer, ports
from v2_eis_helpers import ORDER, string, word

from omni.adapters.platform.desktop.linux import linux_eis_keymap as keymap_module
from omni.adapters.platform.desktop.linux.linux_eis_devices import EisDevices
from omni.adapters.platform.desktop.linux.linux_remote_desktop import ControlSession
from omni.adapters.platform.desktop.linux.linux_session import SessionInspector
from omni.domain.contracts import BusinessError


async def guard():
    pass


@pytest.mark.parametrize("requested,split,version", [(1, False, 1), (2, False, 1), (3, False, 2), (3, True, 2)])
def test_real_negotiated_devices_sparse_masks_regions_keymap_and_unchanged_inventory(
    monkeypatch, tmp_path, requested, split, version
):
    async def scenario():
        ports(monkeypatch)
        peer = DevicePeer(tmp_path, requested=requested, split=split, device_version=version, partial=3)
        wire = peer.connection(guard)
        try:
            await wire.prepare()
            assert not peer.bindings and not wire._devices
            assert len(wire.seats()) == 1 and wire.seats()[0].done
            original = await wire.bind(wire.seats()[0].identity)
            assert (await wire.inventory(original)) == original
            assert len(original.devices) == (2 if split else 1)
            assert all(item.done and item.resumed and not item.destroyed for item in original.devices)
            for item in original.devices:
                if item.keymap is not None:
                    assert item.keymap.size == len(KEYMAP) and item.keymap.sha256 == hashlib.sha256(KEYMAP).hexdigest()
                if item.regions:
                    assert item.regions[0][:5] == (1920, 0, 1920, 1080, 2.0)
                    assert item.regions[0][5] == ("mapping-display-1" if version == 2 else None)
            assert all(value.tell() == 7 for value in peer.files)
            for descriptor in peer.delivered:
                with pytest.raises(OSError):
                    os.fstat(descriptor)
            assert len(peer.bindings) == 1 and original.mask == peer.bindings[0][1]
            assert ("ei_keyboard" in peer.offers) == bool(requested & 1)
            assert ("ei_button" in peer.offers) == bool(requested & 2)
            with pytest.raises(BusinessError):
                await wire.bind(original.seat)
        finally:
            assert await wire.close()
            peer.release_files()

    asyncio.run(scenario())


def test_explicit_choice_among_multiple_seats_never_binds_the_first_by_name_or_default(monkeypatch, tmp_path):
    async def scenario():
        ports(monkeypatch)
        peer = DevicePeer(tmp_path, requested=2, seats=4)
        wire = peer.connection(guard)
        try:
            await wire.prepare()
            assert len(wire.seats()) == 4 and not peer.bindings
            selected = wire.seats()[2].identity
            value = await wire.bind(selected)
            assert value.seat == selected and peer.bindings == [(selected, value.mask)]
            assert all(device.seat == selected for device in value.devices)
        finally:
            assert await wire.close()
            peer.release_files()

    asyncio.run(scenario())


def test_four_batched_keymap_rights_before_device_headers_are_consumed_in_fifo_order(monkeypatch, tmp_path):
    async def scenario():
        ports(monkeypatch)
        peer = DevicePeer(tmp_path, requested=1, keyboards=4)
        wire = peer.connection(guard)
        try:
            await wire.prepare()
            value = await wire.bind(wire.seats()[0].identity)
            assert len(value.devices) == 4 and wire._rights.received == 4 and not wire._rights.owned
            assert [item.keymap.sha256 for item in value.devices] == [
                hashlib.sha256(("distinct-keymap-" + str(index)).encode() + b"\0").hexdigest() for index in range(4)
            ]
            assert len(peer.delivered) == 4
            for descriptor in peer.delivered:
                with pytest.raises(OSError):
                    os.fstat(descriptor)
        finally:
            assert await wire.close()
            peer.release_files()

    asyncio.run(scenario())


@pytest.mark.parametrize(
    "fault",
    [
        "zero_mask",
        "overlap",
        "duplicate_capability",
        "repeat_seat_done",
        "device_version",
        "missing_type",
        "physical",
        "missing_region",
        "scale_nan",
        "duplicate_interface",
        "repeat_device_done",
        "keymap_utf8",
        "keymap_size",
        "missing_fd",
    ],
)
def test_invalid_metadata_or_keymap_never_creates_a_reusable_inventory(monkeypatch, tmp_path, fault):
    async def scenario():
        ports(monkeypatch)
        peer = DevicePeer(tmp_path, fault=fault)
        wire = peer.connection(guard)
        try:
            with pytest.raises(BusinessError):
                await wire.prepare()
                await wire.bind(wire.seats()[0].identity)
            assert not wire.alive
            with pytest.raises(BusinessError):
                await wire.inventory()
        finally:
            assert await wire.close()
            for descriptor in peer.delivered:
                with pytest.raises(OSError):
                    os.fstat(descriptor)
            peer.release_files()

    asyncio.run(scenario())


@pytest.mark.parametrize("change", ["pause", "pause_resume", "destroy", "interface", "seat", "modifiers"])
def test_device_lifecycle_and_transient_pause_invalidate_old_observation_without_reviving_it(
    monkeypatch, tmp_path, change
):
    async def scenario():
        ports(monkeypatch)
        peer = DevicePeer(tmp_path)
        wire = peer.connection(guard)
        try:
            await wire.prepare()
            original = await wire.bind(wire.seats()[0].identity)
            device = original.devices[0].identity
            serial = (peer.serial + 1) % (1 << 32)
            if change in {"pause", "pause_resume"}:
                peer.emit(device, 8, word(serial))
                if change == "pause_resume":
                    peer.emit(device, 7, word((serial + 1) % (1 << 32)))
            elif change == "destroy":
                peer.emit(device, 0, word(serial))
            elif change == "interface":
                peer.emit(peer.child_ids[(device, "ei_button")], 0, word(serial))
            elif change == "seat":
                peer.emit(original.seat, 0, word(serial))
            else:
                peer.emit(
                    peer.child_ids[(device, "ei_keyboard")], 3, word(serial) + struct.pack(ORDER + "IIII", 1, 0, 0, 0)
                )
            with pytest.raises(BusinessError) as changed:
                await wire.inventory(original)
            assert changed.value.code == "portal_eis_observation_changed" and not wire.alive
            with pytest.raises(BusinessError):
                await wire.inventory()
        finally:
            assert await wire.close()
            peer.release_files()

    asyncio.run(scenario())


@pytest.mark.parametrize(
    "event",
    [
        "resume_twice",
        "backwards",
        "late_name",
        "late_region",
        "late_keymap",
        "receiver_key",
        "receiver_frame",
        "mapping_twice",
    ],
)
def test_illegal_order_receiver_events_and_old_serials_fail_the_bound_connection(monkeypatch, tmp_path, event):
    async def scenario():
        ports(monkeypatch)
        peer = DevicePeer(tmp_path)
        wire = peer.connection(guard)
        try:
            await wire.prepare()
            value = await wire.bind(wire.seats()[0].identity)
            device = value.devices[0].identity
            serial = (peer.serial + 1) % (1 << 32)
            if event == "resume_twice":
                peer.emit(device, 7, word(serial))
            elif event == "backwards":
                peer.emit(device, 8, word((peer.serial - 1) % (1 << 32)))
            elif event == "late_name":
                peer.emit(device, 1, string("changed"))
            elif event == "late_region":
                peer.emit(device, 4, struct.pack(ORDER + "IIIIf", 0, 0, 1, 1, 1.0))
            elif event == "late_keymap":
                peer.emit(peer.child_ids[(device, "ei_keyboard")], 1, word(1) + word(len(KEYMAP)))
            elif event == "receiver_key":
                peer.emit(peer.child_ids[(device, "ei_keyboard")], 2, word(30) + word(1))
            elif event == "receiver_frame":
                peer.emit(device, 11, word(serial) + bytes(8))
            else:
                peer.emit(device, 12, string("unexpected mapping"))
            with pytest.raises(BusinessError):
                await wire.inventory()
            assert not wire.alive
        finally:
            assert await wire.close()
            peer.release_files()

    asyncio.run(scenario())


def test_initial_paused_inventory_stays_unavailable_until_an_actual_resume_changes_its_epoch(monkeypatch, tmp_path):
    async def scenario():
        ports(monkeypatch)
        peer = DevicePeer(tmp_path, fault="initial_paused")
        wire = peer.connection(guard)
        try:
            await wire.prepare()
            original = await wire.bind(wire.seats()[0].identity)
            assert not original.devices[0].resumed
            peer.emit(original.devices[0].identity, 7, word((peer.serial + 1) % (1 << 32)))
            actual = await wire.inventory()
            assert actual.devices[0].resumed and actual.devices[0].epoch > original.devices[0].epoch
            assert original.devices[0].resumed is False
        finally:
            assert await wire.close()
            peer.release_files()

    asyncio.run(scenario())


def test_cancelled_keymap_wait_retains_actual_thread_fd_and_eis_connection_until_the_read_finishes(
    monkeypatch, tmp_path
):
    async def scenario():
        ports(monkeypatch)
        peer = DevicePeer(tmp_path)
        wire = peer.connection(guard)
        entered, release = asyncio.Event(), threading.Event()
        loop = asyncio.get_running_loop()
        original = keymap_module.read_keymap

        def read(descriptor, size):
            loop.call_soon_threadsafe(entered.set)
            assert release.wait(10)
            return original(descriptor, size)

        monkeypatch.setattr(keymap_module, "read_keymap", read)
        task = None
        try:
            await wire.prepare()
            task = asyncio.create_task(wire.bind(wire.seats()[0].identity))
            await entered.wait()
            task.cancel()
            with pytest.raises(asyncio.CancelledError):
                await task
            keymap = wire._keymaps[0]
            assert not keymap.task.done() and not await wire.close() and not peer.closed
            os.fstat(peer.delivered[0])
            release.set()
            with pytest.raises(BusinessError):
                await keymap.task
            assert await wire.close() and peer.closed and keymap.data is None
            with pytest.raises(OSError):
                os.fstat(peer.delivered[0])
        finally:
            release.set()
            if task is not None:
                await asyncio.gather(task, return_exceptions=True)
            for keymap in wire._keymaps:
                if keymap.task is not None:
                    await asyncio.gather(keymap.task, return_exceptions=True)
            assert await wire.close()
            peer.release_files()

    asyncio.run(scenario())


def test_native_session_selects_device_profile_only_after_its_own_exact_system_grant(monkeypatch, tmp_path):
    async def scenario():
        ports(monkeypatch)
        value, peer, calls = factory(), DevicePeer(tmp_path), []

        async def connect(owner, session):
            calls.append((owner, session))
            return peer.owned

        def device_factory(descriptor, check, requested):
            assert requested == 3 and descriptor is peer.owned
            return peer.connection(check)

        value.buses["user"].connect_eis = connect
        native = ControlSession(SessionInspector(value.ops, value), 3, device_factory=device_factory)
        try:
            with pytest.raises(BusinessError):
                await native.prepare_eis(with_devices=True)
            await native.prepare()
            await native.authorize(lambda phase: asyncio.sleep(0))
            wire = await native.prepare_eis(with_devices=True)
            assert isinstance(wire, EisDevices) and not peer.bindings
            inventory = await wire.bind(wire.seats()[0].identity)
            assert await wire.inventory(inventory) == inventory and len(calls) == 1
            assert await native.granted_devices() == 3
            with pytest.raises(BusinessError):
                await native.prepare_eis()
        finally:
            assert await native.close() and await native.inspector.close()
            peer.release_files()

    asyncio.run(scenario())


@pytest.mark.parametrize("regions", [16, 17])
def test_region_bound_accepts_complete_exact_limit_and_refuses_truncating_excess(monkeypatch, tmp_path, regions):
    async def scenario():
        ports(monkeypatch)
        peer = DevicePeer(tmp_path, requested=2, regions=regions)
        wire = peer.connection(guard)
        try:
            await wire.prepare()
            if regions == 16:
                inventory = await wire.bind(wire.seats()[0].identity)
                assert len(inventory.devices[0].regions) == 16
                assert inventory.devices[0].regions[-1][5] == "mapping-display-16"
            else:
                with pytest.raises(BusinessError):
                    await wire.bind(wire.seats()[0].identity)
                assert not wire.alive
        finally:
            assert await wire.close()
            peer.release_files()

    asyncio.run(scenario())


def test_unknown_explicit_seat_never_sends_a_bind_or_falls_back_to_the_first_seat(monkeypatch, tmp_path):
    async def scenario():
        ports(monkeypatch)
        peer = DevicePeer(tmp_path, requested=2, seats=2)
        wire = peer.connection(guard)
        try:
            await wire.prepare()
            with pytest.raises(BusinessError):
                await wire.bind(wire.seats()[-1].identity + 100)
            assert not peer.bindings and not wire.alive
        finally:
            assert await wire.close()
            peer.release_files()

    asyncio.run(scenario())


def test_orphaned_batched_descriptor_keeps_original_budget_then_closes_without_publishing_inventory(
    monkeypatch, tmp_path
):
    async def scenario():
        ports(monkeypatch)
        peer = DevicePeer(tmp_path)
        wire = peer.connection(guard)
        original = peer.devices

        def devices(seat):
            original(seat)
            peer.keymap_fd(b"orphan\0")

        peer.devices = devices
        try:
            await wire.prepare()
            started = asyncio.get_running_loop().time()
            with pytest.raises(TimeoutError):
                await wire.bind(wire.seats()[0].identity)
            assert asyncio.get_running_loop().time() - started >= 3
            assert not wire.alive and len(wire._rights.owned) == 1
        finally:
            assert await wire.close() and not wire._rights.owned
            for descriptor in peer.delivered:
                with pytest.raises(OSError):
                    os.fstat(descriptor)
            peer.release_files()

    asyncio.run(scenario())
