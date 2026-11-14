"""Owned Wayland window-share helper with explicit one-frame requests; no database, restore token or input capability."""

from __future__ import annotations

import asyncio
import os
import re
import sys
from collections.abc import Awaitable, Callable, Coroutine
from pathlib import Path
from typing import Any, cast

from ...domain.contracts import BusinessError
from ...domain.desktop_portal import LIFETIME_SECONDS, MAX_FRAME, VERSION, command, decode, encode
from ..platform.linux_bus import LinuxOps, RemoteError, failure, load_types
from ..platform.linux_pipewire import FrameExecutor
from ..platform.linux_portal import PortalFactory, PortalSession
from ..platform.linux_session import SessionInspector


async def run(
    reader: asyncio.StreamReader,
    send: Callable[[bytes], Awaitable[None]],
    *,
    session_factory: Callable[[], PortalSession] | None = None,
    frame_factory: Callable[[], FrameExecutor] | None = None,
) -> int:
    opening = command(decode(await reader.readline()))
    if opening["command"] != "open":
        return 2
    scope = cast(dict[str, str], opening["binding"])
    phase = "starting"
    stop, permitted = asyncio.Event(), asyncio.Event()
    control_error: str | None = None
    native: PortalSession | None = None
    tasks: list[asyncio.Task[Any]] = []
    error: str | None = None
    clean = False
    emission = asyncio.Lock()
    frames = frame_factory() if frame_factory else FrameExecutor()
    captures: asyncio.Queue[dict[str, object]] = asyncio.Queue(maxsize=1)

    async def emit(next_phase: str, *, kind: str = "state", code: str | None = None, cleaned: bool = False) -> None:
        async with emission:
            await emit_locked(next_phase, kind=kind, code=code, cleaned=cleaned)

    async def emit_locked(next_phase: str, *, kind: str, code: str | None, cleaned: bool) -> None:
        nonlocal phase
        if kind == "state":
            phase = next_phase
        async with asyncio.timeout(1):
            await send(
                encode(
                    {
                        "version": VERSION,
                        "binding": scope,
                        "kind": kind,
                        "phase": phase,
                        "error_code": code,
                        "clean": cleaned,
                        "capture_available": frames.available,
                        "capture_error_code": frames.error_code,
                    }
                )
            )

    async def controls() -> None:
        nonlocal control_error
        try:
            while True:
                frame = await reader.readline()
                if not frame:
                    control_error = "portal_parent_closed"
                    stop.set()
                    return
                value = command(decode(frame), scope)
                if value["command"] == "close":
                    stop.set()
                    return
                if value["command"] == "capture":
                    if phase != "active" or not frames.available or not captures.empty():
                        raise failure("portal_protocol_invalid")
                    captures.put_nowait(value)
                    continue
                if value["command"] != "permit" or permitted.is_set():
                    raise failure("portal_protocol_invalid")
                permitted.set()
        except Exception:  # noqa: BLE001 -- malformed control data has no native authority
            control_error = "portal_protocol_invalid"
            stop.set()

    async def monitor() -> None:
        nonlocal control_error
        while True:
            await asyncio.sleep(1)
            if native is not None and native.prepared:
                try:
                    await native.check()
                except Exception:  # noqa: BLE001 -- no provider body crosses IPC
                    control_error = "portal_revoked"
                    stop.set()
                    return
            try:
                await emit(phase, kind="heartbeat")
            except Exception:  # noqa: BLE001 -- a blocked or closed metadata pipe must revoke the native session
                control_error = "portal_transport_failed"
                stop.set()
                return

    async def until_stop(work: Coroutine[Any, Any, object]) -> bool:
        task = asyncio.create_task(work)
        tasks.append(task)
        stopped = asyncio.create_task(stop.wait())
        tasks.append(stopped)
        done, _ = await asyncio.wait((task, stopped), return_when=asyncio.FIRST_COMPLETED)
        if stopped in done:
            task.cancel()
            await asyncio.gather(task, return_exceptions=True)
            if control_error:
                raise failure(control_error)
            return False
        stopped.cancel()
        await asyncio.gather(stopped, return_exceptions=True)
        await task
        return True

    async def capture_frames() -> None:
        assert native is not None and native.stream is not None
        count = 0
        while True:
            value = await captures.get()
            count += 1
            if count > 4 or Path(cast(str, value["capture_path"])).parent.name != value["capture_id"]:
                raise failure("portal_protocol_limit")
            await native.check()
            remote = await native.open_pipewire()
            result = await frames.read(remote, native.stream[1], Path(cast(str, value["capture_path"])))
            await native.check()
            if stop.is_set():
                raise failure("portal_revoked")
            async with emission:
                async with asyncio.timeout(1):
                    await send(
                        encode(
                            {
                                "version": VERSION,
                                "binding": scope,
                                "kind": "frame",
                                "capture_id": value["capture_id"],
                                "receipt": result,
                            }
                        )
                    )

    try:
        tasks.extend([asyncio.create_task(controls()), asyncio.create_task(monitor())])
        async with asyncio.timeout(LIFETIME_SECONDS):
            if session_factory is None:
                load_types()
                ops = LinuxOps()
                native = PortalSession(SessionInspector(ops, PortalFactory(ops)))
            else:
                native = session_factory()
            if await until_stop(native.prepare()) and await until_stop(frames.probe()):
                await emit("prepared")
                async with asyncio.timeout(5):
                    allowed = await until_stop(permitted.wait())
                if allowed and not stop.is_set() and await until_stop(native.authorize(emit)):
                    await until_stop(capture_frames())
                    if control_error:
                        raise failure(control_error)
    except BusinessError as caught:
        error = (
            caught.code
            if re.fullmatch(r"(?:portal|desktop)_[a-z0-9_]{1,100}", caught.code)
            else "portal_native_unavailable"
        )
    except TimeoutError:
        error = "portal_expired"
    except RemoteError:
        error = "portal_native_unavailable"
    except Exception:  # noqa: BLE001 -- exception text may contain private bus/session data
        error = "portal_native_unavailable"
    finally:
        for task in tasks:
            if not task.done():
                task.cancel()
        await asyncio.gather(*tasks, return_exceptions=True)
        frames_clean = await frames.close()
        try:
            await emit("closing")
        except Exception:  # noqa: BLE001 -- still release all native resources
            control_error = "portal_parent_closed"
        if native is not None:
            try:
                async with asyncio.timeout(3):
                    try:
                        session_clean = await native.close()
                    finally:
                        buses_clean = await native.inspector.close()
                    clean = session_clean and buses_clean and frames_clean
            except Exception:  # noqa: BLE001 -- retained as an explicit cleanup failure
                clean = False
        else:
            clean = frames_clean
        if not clean:
            error = "portal_cleanup_pending"
        final = "denied" if error == "portal_cancelled" else "failed" if error else "closed"
        try:
            await emit(final, code=error, cleaned=clean)
        except Exception:  # noqa: BLE001 -- parent disconnect cannot export a traceback
            return 2
    return 0 if clean else 1


async def pipe_main() -> int:
    loop = asyncio.get_running_loop()
    reader = asyncio.StreamReader(limit=MAX_FRAME)
    transport, _ = await loop.connect_read_pipe(lambda: asyncio.StreamReaderProtocol(reader), sys.stdin.buffer)
    descriptor = sys.stdout.fileno()
    os.set_blocking(descriptor, False)

    async def send(data: bytes) -> None:
        pending = memoryview(data)
        while pending:
            try:
                written = os.write(descriptor, pending)
                if written <= 0:
                    raise failure("portal_parent_closed")
                pending = pending[written:]
            except BlockingIOError:
                writable: asyncio.Future[None] = loop.create_future()
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
    except Exception:  # noqa: BLE001 -- stdout is bounded IPC, stderr never contains native details
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
