"""Own the Windows TCP reset workaround without patching global asyncio classes."""

from __future__ import annotations

import asyncio
import logging
import os
import socket
from collections.abc import Callable
from types import CodeType, FunctionType
from typing import Any, Protocol, cast

log = logging.getLogger("omni.v2.network")


class _Socket(Protocol):
    def close(self) -> None: ...


class _Server(Protocol):
    def _detach(self) -> None: ...


class _Connection(Protocol):
    _sock: _Socket | None
    _server: _Server | None
    _called_connection_lost: bool


def finish_connection_loss(
    connection: _Connection,
    original: Callable[[BaseException | None], None],
    error: BaseException | None,
    shutdown_code: CodeType,
) -> None:
    """Repair only a reset raised by CPython's socket-finalization frame.

    The original callback already notified the protocol. Closing its still-owned
    socket and detaching exactly once finishes cleanup; unrelated errors remain
    visible. No descriptor lookup, global monkeypatch or timeout bypass occurs.
    """
    # CPython keeps these transport members private by construction; the
    # protocol above documents the shape and this bridge keeps the access local.
    state: Any = connection
    try:
        original(error)
    except ConnectionResetError as reset:
        frame = reset.__traceback__
        while frame and frame.tb_next:
            frame = frame.tb_next
        if (
            getattr(reset, "winerror", None) != 10054
            or state._called_connection_lost
            or state._sock is None
            or frame is None
            or frame.tb_frame.f_code is not shutdown_code
        ):
            raise
        state._sock.close()
        state._sock = None
        if state._server is not None:
            state._server._detach()
            state._server = None
        state._called_connection_lost = True
        log.warning(
            "Closed a reset TCP connection",
            extra={"event": "network.reset_cleanup", "error_code": "tcp_peer_reset"},
        )
        if reset.__context__ is not None:
            raise reset.__context__


if os.name == "nt":
    from asyncio.proactor_events import _ProactorBasePipeTransport, _ProactorSocketTransport  # pyright: ignore[reportPrivateUsage]

    class _ResetSafeSocketTransport(_ProactorSocketTransport):
        def _call_connection_lost(self, exc: BaseException | None) -> None:
            parent: Any = super()
            finish_connection_loss(
                cast(_Connection, self),
                parent._call_connection_lost,
                exc,
                cast(FunctionType, vars(_ProactorBasePipeTransport)["_call_connection_lost"]).__code__,
            )

    class _ResetSafeProactorLoop(asyncio.ProactorEventLoop):
        def _make_socket_transport(
            self,
            sock: socket.socket,
            protocol: asyncio.BaseProtocol,
            waiter: asyncio.Future[None] | None = None,
            extra: dict[str, Any] | None = None,
            server: asyncio.Server | None = None,
        ) -> asyncio.Transport:
            # CPython accepts BaseProtocol here; its private constructor stub
            # currently names only StreamReaderProtocol, excluding ASGI protocols.
            return _ResetSafeSocketTransport(
                self, sock, cast(asyncio.StreamReaderProtocol, protocol), waiter, extra, server
            )


def create_event_loop() -> asyncio.AbstractEventLoop:
    loop = _ResetSafeProactorLoop() if os.name == "nt" else asyncio.new_event_loop()
    asyncio.set_event_loop(loop)
    return loop
