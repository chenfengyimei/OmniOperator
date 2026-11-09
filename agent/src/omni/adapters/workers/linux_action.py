"""One prepared Linux semantic action; the initial frame never permits a write, and no provider text is logged."""

from __future__ import annotations

import asyncio
import json
import os
import sys

from ...domain.contracts import BusinessError
from ...domain.desktop_actions import linux_ready
from .desktop import MAX_FRAME, decode, encode, frame_data
from .linux_desktop import perform


async def run(reader: asyncio.StreamReader, send, **injected) -> int:
    initial = await reader.readline()
    request = decode(initial)
    linux_ready(request)

    async def authorize():
        await send(json.dumps(linux_ready(request), separators=(",", ":")).encode() + b"\n")
        try:
            async with asyncio.timeout(1):
                permission = await reader.readline()
            if len(initial) + len(permission) > MAX_FRAME:
                return False
            return frame_data(permission) == linux_ready(request, permitted=True)
        except (BusinessError, ValueError, UnicodeError, TimeoutError):
            return False

    response = await perform(request, permit=authorize, **injected)
    await send(encode(response, request))
    return 0


async def pipe_main() -> int:
    loop = asyncio.get_running_loop()
    reader = asyncio.StreamReader(limit=MAX_FRAME)
    transport, _ = await loop.connect_read_pipe(lambda: asyncio.StreamReaderProtocol(reader), sys.stdin.buffer)
    descriptor = sys.stdout.fileno()
    os.set_blocking(descriptor, False)

    async def send(data):
        async with asyncio.timeout(1):
            pending = memoryview(data)
            while pending:
                try:
                    written = os.write(descriptor, pending)
                    if written <= 0:
                        raise OSError("native pipe closed")
                    pending = pending[written:]
                except BlockingIOError:
                    writable = loop.create_future()
                    loop.add_writer(descriptor, lambda: not writable.done() and writable.set_result(None))
                    try:
                        await writable
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
    except Exception:  # noqa: BLE001 -- stdout is a fixed protocol, not a native traceback channel
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
