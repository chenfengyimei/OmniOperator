"""Bounded read-only X11 lease helper; one owned connection carries window lifetime notifications across requests."""

from __future__ import annotations

import asyncio
import os
import sys
from collections.abc import Awaitable, Callable
from typing import Protocol, cast

from ...domain.contracts import BusinessError
from ...domain.desktop import MAX_FRAME
from ...domain.linux_window_leases import (
    LIFETIME_S,
    MAX_COMMANDS,
    MAX_TOTAL_BYTES,
    Payload,
    decode,
    encode,
    parse_request,
)
from omni.adapters.platform.desktop.linux.linux_bus import LinuxOps, failure, load_types
from omni.adapters.platform.desktop.linux.linux_session import LocalFactory, SessionInspector
from omni.adapters.platform.desktop.linux.linux_window_leases import LeaseWire, WindowLeases
from omni.adapters.platform.desktop.linux.linux_x11 import X11Desktop


def parse(frame: bytes) -> Payload:
    return parse_request(decode(frame))


def response(request: Payload, result: object, error: str | None = None) -> bytes:
    value = {
        **{key: request[key] for key in ("version", "request_id", "operation")},
        "ok": error is None,
        "result": result if error is None else None,
        "error_code": error,
    }
    return encode(value)


class LeaseSession(Protocol):
    async def open(self) -> None: ...
    async def bind(self, targets: list[Payload]) -> list[Payload]: ...
    async def verify(self, target: Payload) -> Payload: ...
    async def tick(self) -> None: ...
    async def close(self) -> bool: ...


class NativeSession:
    def __init__(self) -> None:
        self.inspector: SessionInspector | None = None
        self.desktop: X11Desktop | None = None
        self.leases: WindowLeases | None = None

    async def open(self) -> None:
        load_types()
        native = LinuxOps()
        self.inspector = SessionInspector(native, LocalFactory(native))
        await self.inspector.open(accessibility=False)
        if self.inspector.session is None or self.inspector.session.kind != "x11":
            raise failure("desktop_x11_session_unavailable")
        self.desktop = X11Desktop(self.inspector, wire_factory=LeaseWire)
        await self.desktop.open()
        self.leases = WindowLeases(self.desktop)

    async def bind(self, targets: list[Payload]) -> list[Payload]:
        if self.leases is None:
            raise failure("desktop_target_changed")
        return await self.leases.bind(targets)

    async def verify(self, target: Payload) -> Payload:
        if self.leases is None:
            raise failure("desktop_target_changed")
        return await self.leases.verify(target)

    async def tick(self) -> None:
        if self.leases is None:
            raise failure("desktop_target_changed")
        await self.leases.tick()

    async def close(self) -> bool:
        closed = True
        for resource in (self.desktop, self.inspector):
            if resource is not None:
                try:
                    if not await resource.close():
                        closed = False
                except Exception:  # noqa: BLE001 -- native close failure retains an explicit non-success exit
                    closed = False
        return closed


async def run(
    reader: asyncio.StreamReader,
    send: Callable[[bytes], Awaitable[None]],
    *,
    session_factory: Callable[[], LeaseSession] = NativeSession,
) -> int:
    session: LeaseSession | None = None
    pending: asyncio.Task[bytes] | None = None
    closing: Payload | None = None
    ended = False
    total = commands = 0
    try:
        async with asyncio.timeout(LIFETIME_S):
            while commands < MAX_COMMANDS:
                if pending is None:
                    pending = asyncio.create_task(reader.readline())
                done, _ = await asyncio.wait((pending,), timeout=0.5 if session is not None else 5)
                if not done:
                    if session is None:
                        return 2
                    async with asyncio.timeout(4):
                        await session.tick()
                    continue
                frame, pending = pending.result(), None
                if not frame:
                    ended = True
                    break
                total += len(frame)
                commands += 1
                if total > MAX_TOTAL_BYTES:
                    return 2
                request = parse(frame)
                if request["operation"] == "close":
                    closing = request
                    break
                if session is None:
                    if request["operation"] != "bind":
                        return 2
                    session = session_factory()
                    async with asyncio.timeout(4):
                        await session.open()
                async with asyncio.timeout(4):
                    if request["operation"] == "bind":
                        result = await session.bind(cast(list[Payload], request["targets"]))
                    else:
                        result = await session.verify(cast(Payload, request["target"]))
                encoded = response(request, result)
                total += len(encoded)
                if total > MAX_TOTAL_BYTES:
                    return 2
                await send(encoded)
    except (BusinessError, ValueError, OSError, TimeoutError):
        return 2
    finally:
        if pending is not None:
            pending.cancel()
            await asyncio.gather(pending, return_exceptions=True)
        clean = session is None
        if session is not None:
            try:
                async with asyncio.timeout(4):
                    clean = await session.close()
            except Exception:  # noqa: BLE001 -- shutdown cannot fabricate a native release acknowledgement
                clean = False
        if closing is not None and clean:
            await send(response(closing, {"closed": True}))
    return 0 if (closing is not None or ended) and clean else 2


async def pipe_main() -> int:
    loop = asyncio.get_running_loop()
    reader = asyncio.StreamReader(limit=MAX_FRAME)
    transport, _ = await loop.connect_read_pipe(lambda: asyncio.StreamReaderProtocol(reader), sys.stdin.buffer)
    descriptor = sys.stdout.fileno()
    os.set_blocking(descriptor, False)

    async def send(data: bytes) -> None:
        async with asyncio.timeout(1):
            pending = memoryview(data)
            while pending:
                try:
                    size = os.write(descriptor, pending)
                    if size <= 0:
                        raise OSError("native pipe closed")
                    pending = pending[size:]
                except BlockingIOError:
                    ready = loop.create_future()
                    loop.add_writer(descriptor, lambda: not ready.done() and ready.set_result(None))
                    try:
                        await ready
                    finally:
                        loop.remove_writer(descriptor)

    try:
        return await run(reader, send)
    finally:
        transport.close()


def main() -> int:
    if sys.platform != "linux":
        return 2
    try:
        return asyncio.run(pipe_main())
    except Exception:  # noqa: BLE001 -- raw native traceback/UI data never enters the IPC pipe
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
