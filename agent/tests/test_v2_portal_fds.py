"""Descriptor lifetime and exact response correlation using local pipes/fake ancillary data; no native bus."""

import array
import asyncio
import os
import socket
from types import SimpleNamespace

import pytest
from test_v2_linux_bus import transport

from omni.adapters.platform.desktop.linux.linux_fds import OwnedFd, ReceivedFds
from omni.domain.contracts import BusinessError


def pipe_fd():
    read, write = os.pipe()
    os.close(write)
    return read


@pytest.mark.parametrize("fault", [None, "extra", "truncated", "partial_int"])
def test_received_rights_are_bounded_noninheritable_and_closed_on_rejection(monkeypatch, fault):
    monkeypatch.setattr(socket, "SCM_RIGHTS", 1, raising=False)
    monkeypatch.setattr(socket, "CMSG_SPACE", lambda size: size + 16, raising=False)
    descriptors = [pipe_fd() for _ in range(2 if fault == "extra" else 1)]
    receiver = ReceivedFds()
    raw = array.array("i", descriptors).tobytes() + (b"x" if fault == "partial_int" else b"")

    class Peer:
        def recvmsg(self, length, ancillary, flags):
            assert length == 16 and ancillary >= len(array.array("i", [0, 0]).tobytes())
            return (
                b"frame",
                [(socket.SOL_SOCKET, socket.SCM_RIGHTS, raw)],
                getattr(socket, "MSG_CTRUNC", 8) if fault == "truncated" else 0,
                None,
            )

    try:
        if fault is None:
            assert receiver.receive(Peer(), 16)[0] == b"frame"
            assert not os.get_inheritable(descriptors[0])
            owned = receiver.claim(descriptors[0])
            assert not receiver.owned and owned.fileno() == descriptors[0]
            with pytest.raises(BusinessError):
                owned.verify_socket()  # pipe is not a connected Unix stream socket
            owned.close()
            owned.close()
        else:
            with pytest.raises(BusinessError):
                receiver.receive(Peer(), 16)
        for descriptor in descriptors:
            with pytest.raises(OSError):
                os.fstat(descriptor)
    finally:
        receiver.close()


@pytest.mark.parametrize("fault", [None, "sender", "serial", "signature", "index", "error", "late"])
def test_only_one_exact_pending_open_remote_reply_can_transfer_ownership(monkeypatch, fault):
    async def scenario():
        bus, _, _, _ = transport(monkeypatch, unix_fds=True)
        await bus.open()
        descriptor = pipe_fd()
        verified = []
        monkeypatch.setattr(OwnedFd, "verify_socket", lambda fd: verified.append(fd.fileno()))

        async def respond(message):
            assert message.member == "OpenPipeWireRemote" and message.body == ["/session/test", {}]
            assert message.flags == 2 and message.destination == ":1.40"
            bus._received.owned[descriptor] = OwnedFd(descriptor)
            reply = SimpleNamespace(
                sender=":1.41" if fault == "sender" else message.destination,
                reply_serial=18 if fault == "serial" else message.serial,
                unix_fds=[descriptor],
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
                remote = await bus.open_pipewire(":1.40", "/session/test")
                assert verified == [descriptor] and remote.fileno() == descriptor and not bus._received.owned
                remote.close()
            else:
                with pytest.raises(BusinessError):
                    await bus.open_pipewire(":1.40", "/session/test")
                assert not verified
            with pytest.raises(OSError):
                os.fstat(descriptor)
        finally:
            assert await bus.close()

    asyncio.run(scenario())


def test_partial_frame_descriptor_is_released_even_without_a_completed_reply(monkeypatch):
    async def scenario():
        bus, _, _, _ = transport(monkeypatch, unix_fds=True)
        await bus.open()
        descriptor = pipe_fd()
        bus._received.owned[descriptor] = OwnedFd(descriptor)
        assert await bus.close()
        with pytest.raises(OSError):
            os.fstat(descriptor)

    asyncio.run(scenario())
