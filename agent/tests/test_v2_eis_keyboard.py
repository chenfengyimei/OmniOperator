"""Map/epoch-bound EI key plans through actual protocol peers and injected C ABI; planning never emits input."""

import asyncio
import threading

import pytest
from v2_eis_device_helpers import DevicePeer, ports
from v2_eis_helpers import ORDER, word
from v2_xkbcommon_helpers import Library

from omni.adapters.platform.linux_xkbcommon import NativeXkb, XkbCompiler
from omni.domain.contracts import BusinessError


async def guard():
    pass


async def bound(peer):
    wire = peer.connection(guard)
    await wire.prepare()
    observation = await wire.bind(wire.seats()[0].identity)
    return wire, observation, observation.devices[0].identity


def test_real_ei_inventory_fd_hash_native_interpretation_and_repeat_read_are_one_plan(monkeypatch, tmp_path):
    async def scenario():
        ports(monkeypatch)
        peer, library = DevicePeer(tmp_path, requested=1), Library(code=167)
        wire, original, device = await bound(peer)
        try:
            plan = await wire.prepare_key(
                device, "Enter", compiler_factory=lambda: XkbCompiler(lambda: NativeXkb(loader=lambda name: library))
            )
            assert plan.inventory == original and plan.device == device
            assert plan.meaning.evdev_code == 159 and plan.meaning.xkb_code == 167
            assert plan.keyboard == peer.child_ids[(device, "ei_keyboard")]
            assert await wire.verify_key() is plan
            assert [name for name, _args in library.calls].count("xkb_keymap_new_from_buffer") == 1
            with pytest.raises(BusinessError):
                await wire.prepare_key(device, "Tab")
        finally:
            assert await wire.close()
            peer.release_files()

    asyncio.run(scenario())


@pytest.mark.parametrize("fault", ["unknown", "paused", "modifiers", "removed", "no_keymap", "invalid_key"])
def test_missing_or_unsafe_observation_does_not_load_a_native_library_or_guess_a_key(monkeypatch, tmp_path, fault):
    async def scenario():
        import struct

        ports(monkeypatch)
        peer = DevicePeer(tmp_path, requested=1, fault="no_keymap" if fault == "no_keymap" else None)
        wire, _observation, device = await bound(peer)
        calls = []

        def native():
            calls.append("native")
            return NativeXkb(loader=lambda name: Library())

        try:
            serial = word((peer.serial + 1) % (1 << 32))
            if fault == "paused":
                peer.emit(device, 8, serial)
            elif fault == "modifiers":
                peer.emit(peer.child_ids[(device, "ei_keyboard")], 3, serial + struct.pack(ORDER + "IIII", 1, 0, 0, 0))
            elif fault == "removed":
                peer.emit(peer.child_ids[(device, "ei_keyboard")], 0, serial)
            with pytest.raises(BusinessError):
                await wire.prepare_key(
                    device + 100 if fault == "unknown" else device,
                    "Ctrl+Enter" if fault == "invalid_key" else "Enter",
                    compiler_factory=lambda: XkbCompiler(native),
                )
            assert not calls and wire._keyboard.plan is None
        finally:
            assert await wire.close()
            peer.release_files()

    asyncio.run(scenario())


@pytest.mark.parametrize("change", ["pause_resume", "map_body"])
def test_changes_while_actual_compile_is_running_refuse_the_old_plan_after_native_cleanup(
    monkeypatch, tmp_path, change
):
    async def scenario():
        ports(monkeypatch)
        peer, library = DevicePeer(tmp_path, requested=1), Library()
        wire, _observation, device = await bound(peer)
        entered, release = asyncio.Event(), threading.Event()
        loop = asyncio.get_running_loop()
        native = NativeXkb(loader=lambda name: library)
        original = native.interpret

        def interpret(data, identity, key):
            loop.call_soon_threadsafe(entered.set)
            assert release.wait(10)
            return original(data, identity, key)

        native.interpret = interpret
        task = asyncio.create_task(
            wire.prepare_key(device, "Enter", compiler_factory=lambda: XkbCompiler(lambda: native))
        )
        try:
            await entered.wait()
            if change == "pause_resume":
                peer.emit(device, 8, word((peer.serial + 1) % (1 << 32)))
                peer.emit(device, 7, word((peer.serial + 2) % (1 << 32)))
            else:
                data = wire._keymaps[0].data
                wire._keymaps[0].data = b"!" + data[1:]
            release.set()
            with pytest.raises(BusinessError):
                await task
            assert wire._keyboard.plan is None
            assert library.released() == ["xkb_state_unref", "xkb_keymap_unref", "xkb_context_unref"]
        finally:
            release.set()
            await asyncio.gather(task, return_exceptions=True)
            assert await wire.close()
            peer.release_files()

    asyncio.run(scenario())


def test_map_body_change_after_preparation_invalidates_plan_even_when_restored_later(monkeypatch, tmp_path):
    async def scenario():
        ports(monkeypatch)
        peer = DevicePeer(tmp_path, requested=1)
        wire, _observation, device = await bound(peer)
        try:
            await wire.prepare_key(
                device, "Enter", compiler_factory=lambda: XkbCompiler(lambda: NativeXkb(loader=lambda name: Library()))
            )
            body = wire._keymaps[0].data
            wire._keymaps[0].data = b"!" + body[1:]
            with pytest.raises(BusinessError):
                await wire.verify_key()
            wire._keymaps[0].data = body
            with pytest.raises(BusinessError):
                await wire.verify_key()
        finally:
            assert await wire.close()
            peer.release_files()

    asyncio.run(scenario())


def test_cancel_and_owner_close_retain_eis_socket_until_actual_compiler_disposes_native_handles(monkeypatch, tmp_path):
    async def scenario():
        ports(monkeypatch)
        peer, library = DevicePeer(tmp_path, requested=1), Library()
        wire, _observation, device = await bound(peer)
        entered, release = asyncio.Event(), threading.Event()
        loop = asyncio.get_running_loop()
        native = NativeXkb(loader=lambda name: library)
        original = native.interpret

        def interpret(data, identity, key):
            loop.call_soon_threadsafe(entered.set)
            assert release.wait(10)
            return original(data, identity, key)

        native.interpret = interpret
        task = asyncio.create_task(
            wire.prepare_key(device, "Enter", compiler_factory=lambda: XkbCompiler(lambda: native))
        )
        try:
            await entered.wait()
            task.cancel()
            with pytest.raises(asyncio.CancelledError):
                await task
            assert not await wire.close() and not peer.closed and not library.released()
            release.set()
            with pytest.raises(BusinessError):
                await wire._keyboard.compiler.task
            assert await wire.close() and peer.closed
            assert library.released() == ["xkb_state_unref", "xkb_keymap_unref", "xkb_context_unref"]
        finally:
            release.set()
            await asyncio.gather(task, return_exceptions=True)
            await asyncio.gather(wire._keyboard.compiler.task, return_exceptions=True)
            assert await wire.close()
            peer.release_files()

    asyncio.run(scenario())
