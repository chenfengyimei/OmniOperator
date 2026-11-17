"""Portal-only EI core negotiation and original FD ownership, all native peers injected and no input sent."""

import array
import asyncio
import os
import socket
import struct
from types import SimpleNamespace

import pytest
from test_v2_linux_bus import transport
from test_v2_remote_desktop import factory
from v2_eis_helpers import CONNECTION, ORDER, Peer, frame, injected_fds, word

from omni.adapters.platform.linux_remote_desktop import ControlSession
from omni.adapters.platform.linux_session import SessionInspector
from omni.domain.contracts import BusinessError


async def guard():
    pass


@pytest.mark.parametrize("partial,ping", [(1, False), (3, True), (4096, False), (4096, True)])
def test_exact_sender_core_handshake_sync_and_ping_without_device_or_input_requests(monkeypatch, partial, ping):
    async def scenario():
        injected_fds(monkeypatch)
        peer = Peer(partial=partial, ping=ping)
        wire = peer.connection(guard)
        try:
            await wire.prepare()
            assert wire.alive and wire.serial == 0xFFFFFFFF and wire.versions == {"ei_connection": 3}
            assert peer.requests[0] == (0, 0, word(1))
            assert peer.requests[1] == (0, 2, word(2))
            advertised = [item[2][4:].split(b"\0")[0] for item in peer.requests if item[:2] == (0, 4)]
            assert advertised == [b"ei_connection", b"ei_callback", b"ei_pingpong"]
            assert (0, 1, b"") in peer.requests
            initial = (wire._received, wire._messages, wire._sent)
            await wire.check()
            assert wire._received > initial[0] and wire._messages > initial[1] and wire._sent > initial[2]
            callbacks = [
                struct.unpack(ORDER + "QI", item[2])[0] for item in peer.requests if item[:2] == (CONNECTION, 0)
            ]
            assert callbacks == [1, 2]
            assert not peer.outgoing
            with pytest.raises(BusinessError):
                await wire.prepare()
        finally:
            assert await wire.close() and await wire.close()
        assert peer.closed
        with pytest.raises(OSError):
            os.fstat(peer.fd)

    asyncio.run(scenario())


@pytest.mark.parametrize(
    "fault",
    [
        "initial_object",
        "initial_opcode",
        "initial_zero",
        "short",
        "large",
        "unaligned",
        "interface_null",
        "interface_zero",
        "interface_handshake",
        "interface_tail",
        "interface_nonascii",
        "interface_embedded_null",
        "duplicate_interface",
        "repeat_handshake",
        "client_connection_id",
        "connection_version",
        "short_connection",
        "callback_id",
        "callback_value",
        "unexpected_seat",
        "disconnected",
        "invalid_object",
        "ping_reuse",
        "ping_version",
    ],
)
def test_bad_eis_stream_never_becomes_ready_and_cannot_be_revived(monkeypatch, fault):
    async def scenario():
        injected_fds(monkeypatch)
        peer = Peer(fault=fault, ping=fault.startswith("ping_"))
        wire = peer.connection(guard)
        try:
            with pytest.raises(BusinessError) as caught:
                await wire.prepare()
            assert "private-provider-canary" not in str(caught.value)
            assert not wire.alive
            peer.fault = None
            with pytest.raises(BusinessError):
                await wire.check()
        finally:
            assert await wire.close()

    asyncio.run(scenario())


@pytest.mark.parametrize("fault", ["guard", "cancel", "busy_close", "messages", "received", "sent", "eof"])
def test_live_connection_failures_keep_original_budgets_and_release_ownership(monkeypatch, fault):
    async def scenario():
        injected_fds(monkeypatch)
        peer = Peer()
        wire = peer.connection(guard)
        task = None
        try:
            await wire.prepare()
            if fault in {"cancel", "busy_close"}:
                peer.blocked = True
                task = asyncio.create_task(wire.check())
                await peer.waiting.wait()
                if fault == "busy_close":
                    assert not await wire.close() and not peer.closed
                    os.fstat(peer.fd)
                    peer.release.set()
                    with pytest.raises(BusinessError):
                        await task
                else:
                    task.cancel()
                    with pytest.raises(asyncio.CancelledError):
                        await task
            elif fault == "guard":

                async def changed():
                    raise BusinessError("portal_owner_changed", "changed")

                wire.guard = changed
                with pytest.raises(BusinessError):
                    await wire.check()
            elif fault == "eof":
                peer.recvmsg = lambda *_args: (b"", [], 0, None)
                with pytest.raises(BusinessError):
                    await wire.check()
            else:
                setattr(
                    wire,
                    {"messages": "_messages", "received": "_received", "sent": "_sent"}[fault],
                    128 if fault == "messages" else 65536,
                )
                with pytest.raises(BusinessError):
                    await wire.check()
            assert not wire.alive
        finally:
            peer.release.set()
            if task is not None:
                await asyncio.gather(task, return_exceptions=True)
            assert await wire.close()

    asyncio.run(scenario())


@pytest.mark.parametrize("fault", [None, "sender", "serial", "signature", "index", "error", "late"])
def test_eis_fd_reply_uses_exact_remote_interface_serial_owner_and_single_fd(monkeypatch, fault):
    async def scenario():
        injected_fds(monkeypatch)
        bus, _, _, _ = transport(monkeypatch, unix_fds=True)
        await bus.open()
        peer = Peer()

        async def respond(message):
            assert message.interface == "org.freedesktop.portal.RemoteDesktop" and message.member == "ConnectToEIS"
            assert message.body == ["/session/test", {}] and message.signature == "oa{sv}" and message.flags == 2
            bus._received.owned[peer.fd] = peer.owned
            reply = SimpleNamespace(
                sender=":1.41" if fault == "sender" else message.destination,
                reply_serial=18 if fault == "serial" else message.serial,
                unix_fds=[peer.fd],
                message_type=3 if fault == "error" else 2,
                signature="u" if fault == "signature" else "h",
                body=[1 if fault == "index" else 0],
            )
            if fault == "late":
                bus._fd_expected = None
            bus.bus._on_message(reply)
            return reply

        bus.bus.call = respond
        try:
            if fault is None:
                owned = await bus.connect_eis(":1.40", "/session/test")
                assert owned is peer.owned and not bus._received.owned
                owned.close()
            else:
                with pytest.raises(BusinessError):
                    await bus.connect_eis(":1.40", "/session/test")
            with pytest.raises(OSError):
                os.fstat(peer.fd)
        finally:
            assert await bus.close()

    asyncio.run(scenario())


async def authorized(monkeypatch):
    injected_fds(monkeypatch)
    value, peer, calls = factory(), Peer(), []

    async def connect(owner, session):
        assert owner == value.buses["user"].names["org.freedesktop.portal.Desktop"]
        assert session == value.buses["user"].session
        calls.append((owner, session))
        return peer.owned

    value.buses["user"].connect_eis = connect
    native = ControlSession(
        SessionInspector(value.ops, value), 3, eis_factory=lambda descriptor, guard: peer.connection(guard)
    )
    await native.prepare()
    await native.authorize(lambda _phase: asyncio.sleep(0))
    return value, peer, calls, native


def test_only_started_independent_grant_can_connect_once_and_owner_closes_actual_eis_first(monkeypatch):
    async def scenario():
        value, peer, calls, native = await authorized(monkeypatch)
        try:
            wire = await native.prepare_eis()
            await wire.check()
            assert await native.granted_devices() == 3 and len(calls) == 1
            with pytest.raises(BusinessError):
                await native.prepare_eis()
            assert await native.close()
            assert peer.closed and not value.buses["user"].rules
            with pytest.raises(OSError):
                os.fstat(peer.fd)
        finally:
            assert await native.close() and await native.inspector.close()

    asyncio.run(scenario())


@pytest.mark.parametrize("stage", ["before_prepare", "before_authorize", "before_fd", "handshake"])
def test_early_or_invalid_grants_never_open_or_adopt_a_connection(monkeypatch, stage):
    async def scenario():
        injected_fds(monkeypatch)
        value, peer, calls = factory(), Peer(), []

        async def connect(owner, session):
            calls.append((owner, session))
            return peer.owned

        value.buses["user"].connect_eis = connect
        native = ControlSession(
            SessionInspector(value.ops, value), 3, eis_factory=lambda descriptor, guard: peer.connection(guard)
        )
        try:
            if stage != "before_prepare":
                await native.prepare()
            if stage in {"before_fd", "handshake"}:
                await native.authorize(lambda _phase: asyncio.sleep(0))
            if stage == "before_fd":
                native._invalidated = True
            elif stage == "handshake":
                peer.incoming[:] = frame(0, 0, word(0))
            with pytest.raises(BusinessError):
                await native.prepare_eis()
            assert len(calls) == (1 if stage == "handshake" else 0)
            if stage == "handshake":
                assert native._granted == 0 and peer.closed
                with pytest.raises(BusinessError):
                    await native.prepare_eis()
        finally:
            assert await native.close() and await native.inspector.close()
            peer.owned.close()

    asyncio.run(scenario())


@pytest.mark.parametrize("close_owner", [False, True])
def test_cancelled_caller_retains_same_late_fd_task_without_second_connect_or_early_session_close(
    monkeypatch, close_owner
):
    async def scenario():
        value, peer, calls, native = await authorized(monkeypatch)
        started, release = asyncio.Event(), asyncio.Event()

        async def connect(owner, session):
            calls.append((owner, session))
            started.set()
            await release.wait()
            return peer.owned

        value.buses["user"].connect_eis = connect
        task = asyncio.create_task(native.prepare_eis())
        try:
            await started.wait()
            task.cancel()
            with pytest.raises(asyncio.CancelledError):
                await task
            original = native._eis_task
            assert not original.done() and not peer.closed
            if close_owner:
                assert not await native.close() and value.buses["user"].rules
                os.fstat(peer.fd)
            release.set()
            if close_owner:
                with pytest.raises(BusinessError):
                    await original
                assert peer.closed
            else:
                assert (await original).alive
            assert native._eis_task is original and len(calls) == 1
            with pytest.raises(BusinessError):
                await native.prepare_eis()
        finally:
            release.set()
            await asyncio.gather(task, return_exceptions=True)
            if native._eis_task is not None:
                await asyncio.gather(native._eis_task, return_exceptions=True)
            assert await native.close() and await native.inspector.close()

    asyncio.run(scenario())


def test_unexpected_rights_are_closed_while_original_eis_descriptor_keeps_its_owner(monkeypatch):
    async def scenario():
        injected_fds(monkeypatch)
        monkeypatch.setattr(socket, "SCM_RIGHTS", 1, raising=False)
        peer = Peer()
        wire = peer.connection(guard)
        extra, write = os.pipe()
        os.close(write)
        try:
            await wire.prepare()
            peer.recvmsg = lambda *_args: (
                b"x",
                [(socket.SOL_SOCKET, socket.SCM_RIGHTS, array.array("i", [extra]).tobytes())],
                0,
                None,
            )
            with pytest.raises(BusinessError):
                await wire.check()
            with pytest.raises(OSError):
                os.fstat(extra)
            os.fstat(peer.fd)
            assert not wire._rights.owned
        finally:
            assert await wire.close()

    asyncio.run(scenario())


def test_partial_write_waits_for_actual_writability_and_never_restarts_a_request(monkeypatch):
    async def scenario():
        injected_fds(monkeypatch)
        peer = Peer(partial=3)
        wire = peer.connection(guard)
        original = peer.send
        calls, waits = [], []

        def send(data):
            calls.append(bytes(data))
            if len(calls) in {2, 5, 9}:
                raise BlockingIOError
            return original(data)

        async def writable(write):
            assert write is True
            waits.append(write)

        peer.send, wire._wait = send, writable
        try:
            await wire.prepare()
            assert len(waits) == 3
            assert [entry[:2] for entry in peer.requests].count((0, 0)) == 1
            assert [entry[:2] for entry in peer.requests].count((0, 1)) == 1
            assert not peer.outgoing
        finally:
            assert await wire.close()

    asyncio.run(scenario())


def test_failed_borrow_detach_retains_original_fd_until_the_same_close_succeeds(monkeypatch):
    async def scenario():
        injected_fds(monkeypatch)
        peer = Peer()
        wire = peer.connection(guard)
        original = peer.detach
        try:
            await wire.prepare()

            def failed():
                raise OSError("injected detach failure")

            peer.detach = failed
            assert not await wire.close() and not peer.closed
            os.fstat(peer.fd)
            assert not wire.alive
            peer.detach = original
            assert await wire.close() and peer.closed
            with pytest.raises(OSError):
                os.fstat(peer.fd)
        finally:
            peer.detach = original
            assert await wire.close()

    asyncio.run(scenario())


@pytest.mark.parametrize("phase", ["prepare", "check"])
def test_real_protocol_deadlines_invalidate_without_shortening_the_configured_budget(monkeypatch, phase):
    async def scenario():
        injected_fds(monkeypatch)
        peer = Peer()
        wire = peer.connection(guard)
        try:
            if phase == "check":
                await wire.prepare()
            peer.blocked = True
            started = asyncio.get_running_loop().time()
            with pytest.raises(TimeoutError):
                await (wire.prepare() if phase == "prepare" else wire.check())
            assert asyncio.get_running_loop().time() - started >= (3 if phase == "prepare" else 1)
            assert not wire.alive
        finally:
            assert await wire.close()

    asyncio.run(scenario())
