"""Original seat monitor executor/watch ownership and irreversible invalidation, all native ports injected."""

import asyncio

import pytest
from v2_seat_input_helpers import Source, Watch, inspector

from omni.adapters.platform.linux_seat_input import SeatInput
from omni.domain.contracts import BusinessError


async def guard():
    pass


def test_actual_quiet_window_and_every_watch_readiness_latch_without_reading_input():
    async def scenario():
        source, watch = Source(), Watch()
        monitor = SeatInput(inspector(), guard, factory=lambda: source, watch=watch)
        started = asyncio.get_running_loop().time()
        try:
            await monitor.prepare()
            assert asyncio.get_running_loop().time() - started >= 0.25
            assert set(watch.callbacks) == set(source.fds) and source.opened == 1
            await monitor.check()
            watch.callbacks[102]()
            assert not watch.callbacks and not monitor._watching
            with pytest.raises(BusinessError):
                await monitor.check()
            source.error = None
            with pytest.raises(BusinessError):
                await monitor.check()
            with pytest.raises(BusinessError):
                await monitor.prepare()
        finally:
            assert await monitor.close() and not watch.callbacks and source.closes == 1

    asyncio.run(scenario())


def test_exact_full_seat_watch_bound_can_prepare_without_truncation():
    async def scenario():
        source, watch = Source(), Watch()
        source.fds = tuple(range(100, 166))
        monitor = SeatInput(inspector(), guard, factory=lambda: source, watch=watch)
        try:
            await monitor.prepare()
            assert set(watch.callbacks) == set(source.fds) and len(watch.callbacks) == 66
        finally:
            assert await monitor.close()

    asyncio.run(scenario())


def test_concurrent_readers_share_original_native_snapshot_even_when_one_waiter_cancels():
    async def scenario():
        source, watch = Source(), Watch()
        monitor = SeatInput(inspector(), guard, factory=lambda: source, watch=watch)
        await monitor.prepare()
        before = source.checks
        source.pause("check")
        callers = [asyncio.create_task(monitor.check()) for _ in range(4)]
        try:
            assert await asyncio.to_thread(source.entered.wait, 5)
            original = monitor.operation
            callers[0].cancel()
            with pytest.raises(asyncio.CancelledError):
                await callers[0]
            assert source.checks == before + 1 and not original.done() and not await monitor.close()
            source.release.set()
            results = await asyncio.gather(*callers[1:], return_exceptions=True)
            assert all(isinstance(item, BusinessError) for item in results)
            assert monitor.operation is original and await monitor.close() and source.closes == 1
        finally:
            source.release.set()
            await asyncio.gather(*callers, return_exceptions=True)
            assert await monitor.close()

    asyncio.run(scenario())


@pytest.mark.parametrize("fds", [(1, 2), (1, 2, 2), (1, 2, -1), tuple(range(67))])
def test_incomplete_or_duplicate_watch_set_cannot_prepare(fds):
    async def scenario():
        source, watch = Source(), Watch()
        source.fds = fds
        monitor = SeatInput(inspector(), guard, factory=lambda: source, watch=watch)
        try:
            with pytest.raises(BusinessError):
                await monitor.prepare()
            assert not watch.callbacks
        finally:
            assert await monitor.close()

    asyncio.run(scenario())


@pytest.mark.parametrize("phase", ["open", "check"])
@pytest.mark.parametrize("cancel_actual", [False, True])
def test_cancel_never_closes_descriptors_under_original_actual_executor(phase, cancel_actual):
    async def scenario():
        source, watch = Source(), Watch()
        monitor = SeatInput(inspector(), guard, factory=lambda: source, watch=watch)
        if phase == "check":
            await monitor.prepare()
        source.pause(phase)
        caller = asyncio.create_task(monitor.prepare() if phase == "open" else monitor.check())
        try:
            assert await asyncio.to_thread(source.entered.wait, 5)
            original = monitor.operation
            (original if cancel_actual else caller).cancel()
            if not cancel_actual:
                with pytest.raises(asyncio.CancelledError):
                    await caller
            assert not await monitor.close() and source.closes == 0 and not original.done()
            source.release.set()
            await asyncio.gather(caller, original, return_exceptions=True)
            assert monitor.operation is original and await monitor.close() and source.closes == 1
        finally:
            source.release.set()
            await asyncio.gather(caller, return_exceptions=True)
            assert await monitor.close()

    asyncio.run(scenario())


def test_unwatch_failure_keeps_native_owner_and_late_close_keeps_one_original_task():
    async def scenario():
        source, watch = Source(), Watch()
        monitor = SeatInput(inspector(), guard, factory=lambda: source, watch=watch)
        await monitor.prepare()
        try:
            watch.fail_remove = True
            assert not await monitor.close() and source.closes == 0 and watch.callbacks
            watch.fail_remove = False
            source.pause("close")
            close = asyncio.create_task(monitor.close())
            assert await asyncio.to_thread(source.entered.wait, 5)
            close.cancel()
            with pytest.raises(asyncio.CancelledError):
                await close
            original = monitor._close_task
            assert source.closes == 1 and not original.done() and not watch.callbacks
            source.release.set()
            assert await monitor.close() and monitor._close_task is original and source.closes == 1
        finally:
            source.release.set()
            assert await monitor.close()

    asyncio.run(scenario())


def test_native_failure_is_static_and_cannot_leak_device_paths_or_revive_after_restore():
    async def scenario():
        source = Source()
        source.error = PermissionError("private-node-canary")
        monitor = SeatInput(inspector(), guard, factory=lambda: source, watch=Watch())
        try:
            with pytest.raises(BusinessError) as caught:
                await monitor.prepare()
            assert caught.value.code == "portal_input_unavailable" and "private-node-canary" not in str(caught.value)
            source.error = None
            with pytest.raises(BusinessError):
                await monitor.check()
        finally:
            assert await monitor.close()

    asyncio.run(scenario())


def test_actual_prepare_deadline_preserves_original_work_until_late_return():
    async def scenario():
        source = Source()
        source.pause("open")
        monitor = SeatInput(inspector(), guard, factory=lambda: source, watch=Watch())
        started = asyncio.get_running_loop().time()
        try:
            with pytest.raises(TimeoutError):
                await monitor.prepare()
            assert asyncio.get_running_loop().time() - started >= 3
            original = monitor.operation
            assert not original.done() and not await monitor.close() and source.closes == 0
            source.release.set()
            await asyncio.gather(original, return_exceptions=True)
            assert await monitor.close() and source.closes == 1
        finally:
            source.release.set()
            if monitor.operation is not None:
                await asyncio.gather(monitor.operation, return_exceptions=True)
            assert await monitor.close()

    asyncio.run(scenario())


@pytest.mark.parametrize("change", ["healthy", "before", "release"])
def test_control_session_gates_actual_framed_key_and_settles_up_before_input_watch_close(monkeypatch, tmp_path, change):
    from test_v2_remote_desktop import factory
    from v2_eis_device_helpers import ports
    from v2_eis_key_packet_helpers import KeyPeer
    from v2_xkbcommon_helpers import Library

    from omni.adapters.platform.linux_remote_desktop import ControlSession
    from omni.adapters.platform.linux_session import SessionInspector
    from omni.adapters.platform.linux_xkbcommon import NativeXkb, XkbCompiler

    async def scenario():
        ports(monkeypatch)
        platform, source, watch, peer = factory(), Source(), Watch(), KeyPeer(tmp_path)

        async def connect(owner, session):
            return peer.owned

        async def phase(value):
            pass

        platform.buses["user"].connect_eis = connect
        native = ControlSession(
            SessionInspector(platform.ops, platform),
            1,
            device_factory=lambda descriptor, check, mask: peer.connection(check),
            input_factory=lambda identity, check: SeatInput(identity, check, factory=lambda: source, watch=watch),
        )
        try:
            await native.prepare()
            await native.authorize(phase)
            monitor = await native.prepare_input_monitor()
            assert monitor.source is source and source.opened == 1
            with pytest.raises(BusinessError):
                await native.prepare_input_monitor()
            wire = await native.prepare_eis(with_devices=True)
            await wire.bind(wire.seats()[0].identity)
            await wire.prepare_key(
                peer.device_ids[0],
                "Enter",
                compiler_factory=lambda: XkbCompiler(lambda: NativeXkb(loader=lambda name: Library(code=167))),
            )
            packet = await wire.key_packet(clock=lambda: 123456789)
            peer.armed = True
            if change == "before":
                watch.callbacks[101]()
                with pytest.raises(BusinessError):
                    await packet.emit(guard)
                assert not peer.keys and native._granted == 0
            elif change == "release":
                peer.boundary, peer.write_mode = 76, "zero"
                with pytest.raises(BusinessError):
                    await packet.emit(guard)
                watch.callbacks[102]()
                assert packet.release_pending and not await native.close()
                assert (
                    source.closes == 0
                    and native._input.source is source
                    and platform.buses["user"].rules
                    and not peer.closed
                )
                peer.write_release.set()
            else:
                await packet.emit(guard)
                assert packet.transport_verified and packet.settled and source.checks > 2
            assert await native.close() and source.closes == 1 and not watch.callbacks and peer.closed
            if change != "before":
                assert peer.keys == [(159, 1), (159, 0)] and not peer.logical
            assert not platform.buses["user"].rules
        finally:
            peer.write_release.set()
            peer.finish_barrier()
            await native.close()
            await native.inspector.close()
            peer.release_files()

    asyncio.run(scenario())


def test_control_cancelled_prepare_retains_original_monitor_thread_and_portal_session():
    from test_v2_remote_desktop import factory

    from omni.adapters.platform.linux_remote_desktop import ControlSession
    from omni.adapters.platform.linux_session import SessionInspector

    async def scenario():
        platform, source, watch = factory(), Source(), Watch()
        source.pause("open")
        native = ControlSession(
            SessionInspector(platform.ops, platform),
            1,
            input_factory=lambda identity, check: SeatInput(identity, check, factory=lambda: source, watch=watch),
        )

        async def phase(value):
            pass

        await native.prepare()
        await native.authorize(phase)
        caller = asyncio.create_task(native.prepare_input_monitor())
        try:
            assert await asyncio.to_thread(source.entered.wait, 5)
            original = native._input_task
            caller.cancel()
            with pytest.raises(asyncio.CancelledError):
                await caller
            assert not original.done() and not await native.close() and source.closes == 0
            assert platform.buses["user"].rules
            source.release.set()
            await asyncio.gather(original, return_exceptions=True)
            assert native._input_task is original and source.opened == 1 and await native.close() and source.closes == 1
            assert not platform.buses["user"].rules
        finally:
            source.release.set()
            await asyncio.gather(caller, return_exceptions=True)
            await native.close()
            await native.inspector.close()

    asyncio.run(scenario())
