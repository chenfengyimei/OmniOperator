"""Native object generations and bounded Wayland messages using independent binary peers; no input/native desktop."""

import asyncio
import os

import pytest
from v2_seat_input_helpers import inspector
from v2_wayland_helpers import BASE, Peer, source, string, view, word

from omni.adapters.platform.linux_wayland_wire import MAX_BYTES, MAX_MESSAGES, read_string
from omni.domain.contracts import BusinessError


async def guard():
    pass


@pytest.mark.parametrize("partial", [1, 7, 4096])
def test_exact_readonly_registry_output_and_window_batches_bind_actual_object_generation(monkeypatch, partial):
    async def scenario():
        descriptor, peer = source(monkeypatch), Peer(partial=partial)
        windows, ops = view(inspector(), descriptor, peer, guard)
        try:
            await windows.prepare()
            values = windows.windows_view()
            assert len(values) == 2 and values[0].identity == BASE and values[1].parent == BASE
            assert values[0].outputs == tuple(peer.output_ids) and values[0].states == (2,)
            assert "private-window-title-canary" not in str(values) and "private-output" not in str(values)
            chosen = await windows.select(BASE)
            await windows.check()
            assert windows.selected == chosen and len(set(ops.queries)) == 1 and ops.queries[0] != descriptor.fileno()
            assert ops.paths[0].address == "/run/user/1000/wayland-2"
            assert all(identity in {1, peer.registry} for identity, opcode, payload in peer.requests)
            with pytest.raises(BusinessError):
                await windows.select(BASE)
            assert await windows.close() and peer.closed
            os.fstat(descriptor.fileno())  # original EI ownership is external
        finally:
            await windows.close()
            descriptor.close()
            peer.cleanup()

    asyncio.run(scenario())


@pytest.mark.parametrize(
    "fault",
    [
        "missing_manager",
        "old_manager",
        "duplicate_global",
        "client_window",
        "invalid_state",
        "duplicate_state",
        "missing_done",
    ],
)
def test_missing_version_or_malformed_protocol_never_produces_a_ready_view(monkeypatch, fault):
    async def scenario():
        descriptor, peer = source(monkeypatch), Peer(fault=fault)
        windows, _ = view(inspector(), descriptor, peer, guard)
        try:
            with pytest.raises(BusinessError):
                await windows.prepare()
            with pytest.raises(BusinessError):
                windows.windows_view()
        finally:
            assert await windows.close()
            descriptor.close()
            peer.cleanup()

    asyncio.run(scenario())


@pytest.mark.parametrize(
    "change", ["focus_restore", "title", "app_id", "closed", "new_active", "output", "global_remove", "parent"]
)
def test_old_selected_object_never_revives_after_lifecycle_or_observation_change(monkeypatch, change):
    async def scenario():
        descriptor, peer = source(monkeypatch), Peer()
        windows, _ = view(inspector(), descriptor, peer, guard)
        try:
            await windows.prepare()
            await windows.select(BASE)
            if change == "focus_restore":
                peer.emit(BASE, 4, word(0))
                peer.emit(BASE, 5)
                peer.emit(BASE, 4, word(4) + word(2))
                peer.emit(BASE, 5)
            elif change == "title":
                peer.emit(BASE, 0, string("changed private title"))
                peer.emit(BASE, 5)
            elif change == "app_id":
                peer.emit(BASE, 1, string("different.app"))
                peer.emit(BASE, 5)
            elif change == "closed":
                peer.emit(BASE, 6)
            elif change == "new_active":
                peer.emit(BASE + 1, 4, word(4) + word(2))
                peer.emit(BASE + 1, 5)
            elif change == "output":
                peer.emit(peer.output_ids[0], 3, word(1))
                peer.emit(peer.output_ids[0], 2)
            elif change == "global_remove":
                peer.emit(peer.registry, 1, word(99))
            elif change == "parent":
                peer.emit(BASE, 7, word(BASE + 1))
                peer.emit(BASE, 5)
            with pytest.raises(BusinessError):
                await windows.check()
            with pytest.raises(BusinessError):
                await windows.check()
            assert windows.selected.identity == BASE
        finally:
            assert await windows.close()
            descriptor.close()
            peer.cleanup()

    asyncio.run(scenario())


@pytest.mark.parametrize("fault", ["multi_active", "closed"])
def test_active_or_closed_ambiguity_is_never_a_selected_target(monkeypatch, fault):
    async def scenario():
        descriptor, peer = source(monkeypatch), Peer(fault=fault)
        windows, _ = view(inspector(), descriptor, peer, guard)
        try:
            await windows.prepare()
            with pytest.raises(BusinessError):
                await windows.select(BASE)
            assert windows.selected is None
        finally:
            assert await windows.close()
            descriptor.close()
            peer.cleanup()

    asyncio.run(scenario())


@pytest.mark.parametrize("count", [64, 65])
def test_exact_lifetime_window_bound_is_complete_or_refused(monkeypatch, count):
    async def scenario():
        descriptor, peer = source(monkeypatch), Peer(windows=count)
        windows, _ = view(inspector(), descriptor, peer, guard)
        try:
            if count == 64:
                await windows.prepare()
                assert len(windows.windows_view()) == 64
            else:
                with pytest.raises(BusinessError):
                    await windows.prepare()
        finally:
            assert await windows.close()
            descriptor.close()
            peer.cleanup()

    asyncio.run(scenario())


@pytest.mark.parametrize("limit", ["sent", "received", "messages"])
def test_original_cumulative_wire_limits_never_reset_on_check(monkeypatch, limit):
    async def scenario():
        descriptor, peer = source(monkeypatch), Peer()
        windows, _ = view(inspector(), descriptor, peer, guard)
        try:
            await windows.prepare()
            setattr(windows.wire, limit, MAX_MESSAGES if limit == "messages" else MAX_BYTES)
            with pytest.raises(BusinessError):
                await windows.check()
        finally:
            assert await windows.close()
            descriptor.close()
            peer.cleanup()

    asyncio.run(scenario())


def test_cancelled_waiter_keeps_same_partial_reader_until_readonly_owner_close(monkeypatch):
    async def scenario():
        descriptor, peer = source(monkeypatch), Peer()
        windows, _ = view(inspector(), descriptor, peer, guard)
        try:
            await windows.prepare()
            peer.hold = True
            waiter = asyncio.create_task(windows.check())
            await peer.waiting.wait()
            original = windows.operation
            waiter.cancel()
            with pytest.raises(asyncio.CancelledError):
                await waiter
            assert not original.done() and peer.active_waits == 1 and not peer.closed
            assert (
                await windows.close() and windows.operation is original and original.done() and peer.active_waits == 0
            )
        finally:
            await windows.close()
            descriptor.close()
            peer.cleanup()

    asyncio.run(scenario())


def test_wire_strings_are_exact_utf8_with_nonzero_padding_and_no_internal_nul():
    value, end = read_string(string("汉字"))
    assert value == "汉字" and end == len(string("汉字"))
    for raw in (word(0), word(2) + b"x?\0\0", word(4) + b"a\0b\0", word(2) + b"\xff\0\0\0"):
        with pytest.raises(BusinessError):
            read_string(raw)


@pytest.mark.parametrize("phase", ["prepare", "check"])
def test_actual_read_deadline_invalidates_view_and_removes_original_wait(monkeypatch, phase):
    async def scenario():
        import time

        descriptor, peer = source(monkeypatch), Peer()
        windows, _ = view(inspector(), descriptor, peer, guard)
        try:
            if phase == "check":
                await windows.prepare()
            peer.hold = True
            start = time.monotonic()
            with pytest.raises(TimeoutError):
                await (windows.prepare() if phase == "prepare" else windows.check())
            assert time.monotonic() - start >= (3 if phase == "prepare" else 1)
            assert windows.operation.done() and peer.active_waits == 0
            with pytest.raises(BusinessError):
                windows.windows_view()
        finally:
            assert await windows.close()
            descriptor.close()
            peer.cleanup()

    asyncio.run(scenario())


@pytest.mark.parametrize("outputs", [0, 16, 17])
def test_output_directory_exact_bound_and_version_two_batches(monkeypatch, outputs):
    async def scenario():
        descriptor, peer = source(monkeypatch), Peer(outputs=outputs, fault="output_v2")
        windows, _ = view(inspector(), descriptor, peer, guard)
        try:
            if outputs <= 16:
                await windows.prepare()
                assert len(windows.outputs) == outputs and set(windows.outputs.values()) <= {2}
            else:
                with pytest.raises(BusinessError):
                    await windows.prepare()
                assert not peer.output_ids
        finally:
            assert await windows.close()
            descriptor.close()
            peer.cleanup()

    asyncio.run(scenario())


def test_unexpected_descriptor_is_closed_and_close_uncertainty_never_turns_clean(monkeypatch):
    import array
    import socket

    async def scenario():
        descriptor, peer = source(monkeypatch), Peer()
        windows, _ = view(inspector(), descriptor, peer, guard)
        read, write = os.pipe()
        os.close(write)
        monkeypatch.setattr(socket, "SCM_RIGHTS", 1, raising=False)
        original = peer.recvmsg

        def receive(*args):
            data, _, flags, address = original(*args)
            peer.recvmsg = original
            return data, [(socket.SOL_SOCKET, socket.SCM_RIGHTS, array.array("i", [read]).tobytes())], flags, address

        peer.recvmsg = receive
        try:
            with pytest.raises(BusinessError):
                await windows.prepare()
            with pytest.raises(OSError):
                os.fstat(read)
            attempts = []

            def uncertain():
                attempts.append(1)
                if len(attempts) == 1:
                    raise OSError("private-close-canary")

            windows.wire.rights.close = uncertain
            assert not await windows.close() and not await windows.close() and peer.closed
        finally:
            await windows.close()
            descriptor.close()
            peer.cleanup()

    asyncio.run(scenario())


def test_concurrent_selection_cannot_return_two_grants_from_one_pending_snapshot(monkeypatch):
    async def scenario():
        descriptor, peer = source(monkeypatch), Peer()
        windows, _ = view(inspector(), descriptor, peer, guard)
        try:
            await windows.prepare()
            results = await asyncio.gather(windows.select(BASE), windows.select(BASE), return_exceptions=True)
            assert sum(isinstance(value, BusinessError) for value in results) == 1
            assert sum(value == windows.selected for value in results) == 1
        finally:
            assert await windows.close()
            descriptor.close()
            peer.cleanup()

    asyncio.run(scenario())
