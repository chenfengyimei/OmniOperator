"""Original Portal/EI release ownership and read-only window checks; isolated binary peers and injected native ports."""

import asyncio

import pytest
from test_v2_remote_desktop import factory
from v2_eis_device_helpers import ports
from v2_eis_key_packet_helpers import KeyPeer
from v2_wayland_helpers import BASE, Peer, view, word
from v2_xkbcommon_helpers import Library

from omni.adapters.platform.linux_remote_desktop import ControlSession
from omni.adapters.platform.linux_session import SessionInspector
from omni.adapters.platform.linux_xkbcommon import NativeXkb, XkbCompiler
from omni.domain.contracts import BusinessError


async def phase(value=None):
    pass


@pytest.mark.parametrize("change", ["before", "release", "stable"])
def test_selected_window_guard_precedes_key_and_original_ei_release_precedes_window_close(
    monkeypatch, tmp_path, change
):
    async def scenario():
        ports(monkeypatch)
        platform, eis, peer = factory(), KeyPeer(tmp_path), Peer()

        async def connect(owner, session):
            return eis.owned

        platform.buses["user"].connect_eis = connect
        native = ControlSession(
            SessionInspector(platform.ops, platform),
            1,
            device_factory=lambda fd, guard, mask: eis.connection(guard),
            window_factory=lambda identity, fd, name, guard: view(identity, fd, peer, guard, name=name)[0],
        )
        try:
            await native.prepare()
            await native.authorize(phase)
            wire = await native.prepare_eis(with_devices=True)
            windows = await native.prepare_windows("wayland-2")
            await windows.select(BASE)
            with pytest.raises(BusinessError):
                await native.prepare_windows("wayland-2")
            await wire.bind(wire.seats()[0].identity)
            await wire.prepare_key(
                eis.device_ids[0],
                "Enter",
                compiler_factory=lambda: XkbCompiler(lambda: NativeXkb(loader=lambda name: Library(code=167))),
            )
            packet = await wire.key_packet(clock=lambda: 123456789)
            eis.armed = True
            if change == "before":
                peer.emit(BASE, 4, word(0))
                peer.emit(BASE, 5)
                peer.emit(BASE, 4, word(4) + word(2))
                peer.emit(BASE, 5)
                with pytest.raises(BusinessError):
                    await packet.emit(phase)
                assert not eis.keys and native._granted == 0
            elif change == "release":
                eis.boundary, eis.write_mode = 76, "zero"
                with pytest.raises(BusinessError):
                    await packet.emit(phase)
                assert packet.release_pending and not await native.close()
                assert not peer.closed and not eis.closed and platform.buses["user"].rules
                eis.write_release.set()
            else:
                await packet.emit(phase)
                assert packet.transport_verified and packet.settled
            assert await native.close() and peer.closed and eis.closed
            assert not platform.buses["user"].rules
            if change != "before":
                assert eis.keys == [(159, 1), (159, 0)] and not eis.logical
        finally:
            eis.write_release.set()
            eis.finish_barrier()
            await native.close()
            await native.inspector.close()
            eis.release_files()
            peer.cleanup()

    asyncio.run(scenario())


def test_cancelled_prepare_waiter_keeps_original_window_operation_until_owner_close(monkeypatch, tmp_path):
    async def scenario():
        ports(monkeypatch)
        platform, eis, peer = factory(), KeyPeer(tmp_path), Peer()

        async def connect(owner, session):
            return eis.owned

        platform.buses["user"].connect_eis = connect
        native = ControlSession(
            SessionInspector(platform.ops, platform),
            1,
            device_factory=lambda fd, guard, mask: eis.connection(guard),
            window_factory=lambda identity, fd, name, guard: view(identity, fd, peer, guard, name=name)[0],
        )
        caller = None
        try:
            await native.prepare()
            await native.authorize(phase)
            await native.prepare_eis(with_devices=True)
            peer.hold = True
            caller = asyncio.create_task(native.prepare_windows("wayland-2"))
            await peer.waiting.wait()
            original = native._window_task
            reader = native._windows.operation
            caller.cancel()
            with pytest.raises(asyncio.CancelledError):
                await caller
            assert not original.done() and not reader.done() and peer.active_waits == 1
            await native.close()
            await asyncio.gather(original, return_exceptions=True)
            assert native._window_task is original and native._windows.operation is reader
            assert await native.close() and peer.closed and peer.active_waits == 0
            assert not platform.buses["user"].rules
        finally:
            if caller is not None:
                await asyncio.gather(caller, return_exceptions=True)
            await native.close()
            await native.inspector.close()
            eis.release_files()
            peer.cleanup()

    asyncio.run(scenario())
