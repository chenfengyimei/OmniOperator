"""TCP reset cleanup keeps unrelated exceptions visible and subprocess support intact."""

import asyncio
import os
import socket
import struct
import sys
from types import SimpleNamespace

import pytest

from omni.adapters.platform.runtime.event_loop import create_event_loop, finish_connection_loss


@pytest.mark.parametrize("scenario", ["reset", "other_error", "protocol_error", "nested_error"])
def test_owned_reset_cleanup_is_exact_and_does_not_suppress_unrelated_errors(scenario):
    closes, detaches = [], []
    state = SimpleNamespace(
        _called_connection_lost=False,
        _sock=SimpleNamespace(close=lambda: closes.append(1)),
        _server=SimpleNamespace(_detach=lambda: detaches.append(1)),
    )
    reset = ConnectionResetError("isolated reset fixture")
    reset.winerror = 10053 if scenario == "other_error" else 10054

    def protocol():
        raise reset

    def original(_):
        if state._called_connection_lost:
            return
        if scenario == "protocol_error":
            protocol()
        if scenario == "nested_error":
            try:
                raise ValueError("protocol failure must survive cleanup")
            except ValueError:
                raise reset
        raise reset

    if scenario == "reset":
        finish_connection_loss(state, original, None, original.__code__)
        assert state._called_connection_lost and state._sock is None and state._server is None
        finish_connection_loss(state, original, None, original.__code__)
        assert closes == detaches == [1]
    elif scenario == "nested_error":
        with pytest.raises(ValueError, match="protocol failure"):
            finish_connection_loss(state, original, None, original.__code__)
        assert closes == detaches == [1]
    else:
        with pytest.raises(ConnectionResetError):
            finish_connection_loss(state, original, None, original.__code__)
        assert closes == detaches == []


def test_loop_reclaims_real_reset_connections_and_closes_listener():
    async def scenario():
        loop = asyncio.get_running_loop()
        connections = asyncio.Queue()

        class Peer(asyncio.Protocol):
            def connection_made(self, transport):
                self.closed = asyncio.Event()
                self.transport = transport
                connections.put_nowait(self)

            def connection_lost(self, error):
                self.closed.set()

        server = await loop.create_server(Peer, "127.0.0.1", 0)
        try:
            async with asyncio.timeout(15):
                for _ in range(12):
                    peer = socket.socket()
                    peer.setblocking(False)
                    try:
                        await loop.sock_connect(peer, server.sockets[0].getsockname())
                        connection = await connections.get()
                        peer.setsockopt(
                            socket.SOL_SOCKET, socket.SO_LINGER, struct.pack("hh" if os.name == "nt" else "ii", 1, 0)
                        )
                    finally:
                        peer.close()
                    await connection.closed.wait()
                server.close()
                await server.wait_closed()
        finally:
            server.close()

    with asyncio.Runner(loop_factory=create_event_loop) as runner:
        runner.run(scenario())


def test_loop_still_supports_managed_async_subprocesses():
    async def scenario():
        child = await asyncio.create_subprocess_exec(
            sys.executable,
            "-c",
            "print('isolated-loop-child')",
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
        )
        try:
            out, err = await asyncio.wait_for(child.communicate(), 15)
            assert child.returncode == 0 and out.strip() == b"isolated-loop-child" and not err
        finally:
            if child.returncode is None:
                child.kill()
                await child.wait()

    with asyncio.Runner(loop_factory=create_event_loop) as runner:
        runner.run(scenario())
