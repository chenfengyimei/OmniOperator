"""Owned independent Wayland control-consent helper; no input commands, EIS/clipboard descriptors, database or restored authority."""

from __future__ import annotations

import asyncio
import os
import re
import sys
from collections.abc import Awaitable, Callable, Coroutine
from typing import Any, cast

from ...domain.contracts import BusinessError
from ...domain.desktop_control import VERSION, command, event
from ...domain.desktop_portal import LIFETIME_SECONDS, MAX_FRAME, MAX_TOTAL, decode, encode
from omni.adapters.platform.desktop.linux.linux_bus import LinuxOps, failure, load_types
from omni.adapters.platform.desktop.linux.linux_portal import PortalFactory
from omni.adapters.platform.desktop.linux.linux_remote_desktop import ControlSession
from omni.adapters.platform.desktop.linux.linux_session import SessionInspector

Payload = dict[str, object]


async def run(
    reader: asyncio.StreamReader,
    send: Callable[[bytes], Awaitable[None]],
    *,
    session_factory: Callable[[int], ControlSession] | None = None,
) -> int:
    try:
        async with asyncio.timeout(5):
            initial = await reader.readline()
        opening = command(decode(initial))
        if opening["command"] != "open":
            return 2
    except (BusinessError, ValueError, OSError, TimeoutError):
        return 2
    scope, requested = cast(dict[str, str], opening["binding"]), cast(int, opening["devices"])
    phase = "starting"
    stop, permitted = asyncio.Event(), asyncio.Event()
    control_error: str | None = None
    error: str | None = None
    native: ControlSession | None = None
    tasks: list[asyncio.Task[Any]] = []
    emission = asyncio.Lock()
    clean = False

    async def emit(next_phase: str, *, kind: str = "state", code: str | None = None, cleaned: bool = False) -> None:
        nonlocal phase
        async with emission:
            if kind == "heartbeat":
                next_phase = phase
            value = event(
                {
                    "version": VERSION,
                    "binding": scope,
                    "kind": kind,
                    "phase": next_phase,
                    "devices": requested,
                    "granted_devices": requested if next_phase == "system_authorized" else 0,
                    "input_authorized": False,
                    "clean": cleaned,
                    "error_code": code,
                },
                scope,
                phase,
            )
            phase = next_phase
            async with asyncio.timeout(1):
                await send(encode(value))

    async def controls() -> None:
        nonlocal control_error
        total, count = len(initial), 1
        try:
            while True:
                frame = await reader.readline()
                if not frame:
                    control_error = "portal_parent_closed"
                    stop.set()
                    return
                total += len(frame)
                count += 1
                if total > MAX_TOTAL or count > 3:
                    raise failure("portal_control_invalid")
                value = command(decode(frame), scope)
                if value["command"] == "close":
                    stop.set()
                    return
                if value["command"] != "permit" or phase != "prepared" or permitted.is_set():
                    raise failure("portal_control_invalid")
                permitted.set()
        except Exception:  # noqa: BLE001 -- invalid control data never becomes a system prompt or input authority
            control_error = "portal_control_invalid"
            stop.set()

    async def monitor() -> None:
        nonlocal control_error
        try:
            while True:
                await asyncio.sleep(1)
                if native is not None and native.prepared:
                    await native.check()
                await emit(phase, kind="heartbeat")
        except Exception:  # noqa: BLE001 -- a lost pinned session or blocked metadata pipe revokes this helper
            control_error = "portal_revoked"
            stop.set()

    async def until_stop(work: Coroutine[Any, Any, object]) -> bool:
        operation, stopped = asyncio.create_task(work), asyncio.create_task(stop.wait())
        tasks.extend((operation, stopped))
        done, _ = await asyncio.wait((operation, stopped), return_when=asyncio.FIRST_COMPLETED)
        if stopped in done:
            operation.cancel()
            await asyncio.gather(operation, return_exceptions=True)
            if control_error:
                raise failure(control_error)
            return False
        stopped.cancel()
        await asyncio.gather(stopped, return_exceptions=True)
        await operation
        return True

    try:
        async with asyncio.timeout(LIFETIME_SECONDS):
            if session_factory is None:
                load_types()
                ops = LinuxOps()
                native = ControlSession(SessionInspector(ops, PortalFactory(ops)), requested)
            else:
                native = session_factory(requested)
            tasks.extend((asyncio.create_task(controls()), asyncio.create_task(monitor())))
            if await until_stop(native.prepare()):
                await emit("prepared")
                async with asyncio.timeout(5):
                    allowed = await until_stop(permitted.wait())
                if allowed and not stop.is_set() and await until_stop(native.authorize(emit)):
                    await stop.wait()
                    if control_error:
                        raise failure(control_error)
    except BusinessError as caught:
        error = (
            caught.code
            if re.fullmatch(r"(?:portal|desktop)_[a-z0-9_]{1,100}", caught.code)
            else "portal_control_unavailable"
        )
    except TimeoutError:
        error = "portal_expired"
    except Exception:  # noqa: BLE001 -- raw portal/provider error text is not a control receipt
        error = "portal_control_unavailable"
    finally:
        for task in tasks:
            if not task.done():
                task.cancel()
        await asyncio.gather(*tasks, return_exceptions=True)
        try:
            await emit("closing")
        except Exception:  # noqa: BLE001 -- transport failure cannot skip native reclamation
            error = error or "portal_transport_failed"
        clean = native is None
        if native is not None:
            clean = True
            deadline = asyncio.get_running_loop().time() + 4
            for resource in (native, native.inspector):
                try:
                    async with asyncio.timeout_at(deadline):
                        if not await resource.close():
                            clean = False
                except Exception:  # noqa: BLE001 -- unfinished native release is never acknowledged as clean
                    clean = False
        if not clean:
            error = "portal_cleanup_pending"
        try:
            await emit(
                "denied" if error == "portal_cancelled" else "failed" if error else "closed", code=error, cleaned=clean
            )
        except Exception:  # noqa: BLE001 -- a failed final receipt remains an unsuccessful helper exit
            error = error or "portal_transport_failed"
    return 0 if clean and error is None else 2


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
                        raise OSError("control pipe closed")
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
    except Exception:  # noqa: BLE001 -- never export raw desktop or authorization bodies
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
