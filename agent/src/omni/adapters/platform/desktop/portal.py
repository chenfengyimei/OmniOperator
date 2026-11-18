"""Owned long-lived Portal helper sessions with shared desktop capacity and bounded metadata pipes; no pixel or input dispatch."""

from __future__ import annotations

import asyncio
import contextvars
import sys
import uuid
from collections import OrderedDict
from collections.abc import Awaitable, Callable, Coroutine
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass, field
from functools import partial
from pathlib import Path
from typing import Any, TypeVar, cast

from omni.application.ports import Record
from omni.domain.contracts import BusinessError
from omni.domain.desktop import CapturedFrame
from omni.domain.desktop_portal import (
    LIFETIME_SECONDS,
    MAX_EVENTS,
    MAX_FRAME,
    MAX_TOTAL,
    TERMINAL,
    TRANSITIONS,
    VERSION,
    binding,
    decode,
    encode,
    event,
    frame_event,
)
from omni.adapters.platform.desktop import desktop_capture
from omni.adapters.platform.desktop.backend import helper_environment
from omni.adapters.platform.runtime.processes import OwnedProcess

T = TypeVar("T")


def error(code: str) -> BusinessError:
    return BusinessError(code, "Window-share session is unavailable or no longer authorized", status=503)


@dataclass(eq=False)
class Host:
    binding: dict[str, str]
    phase: str = "starting"
    code: str | None = None
    clean: bool = False
    released: bool = False
    acquired: bool = False
    permitted: bool = False
    revision: int = 0
    total: int = 0
    events: int = 0
    last_seen: float = 0
    owned: OwnedProcess | None = None
    starting: asyncio.Task[OwnedProcess] | None = None
    runner: asyncio.Task[None] | None = None
    cleanup: asyncio.Task[bool] | None = None
    capture_task: asyncio.Task[CapturedFrame] | None = None
    capture_reply: asyncio.Future[Record] | None = None
    capture_id: str | None = None
    capture_count: int = 0
    capture_available: bool = False
    capture_code: str | None = "portal_frame_adapter_missing"
    staging: set[Path] = field(default_factory=cast(Callable[[], set[Path]], set))
    ready: asyncio.Event = field(default_factory=asyncio.Event)
    stop: asyncio.Event = field(default_factory=asyncio.Event)
    changed: asyncio.Condition = field(default_factory=asyncio.Condition)


class PortalBackend:
    def __init__(
        self,
        *,
        process_factory: Callable[..., Coroutine[Any, Any, OwnedProcess]] | None = None,
        capacity: asyncio.Semaphore | None = None,
        capture_root: Path | None = None,
    ) -> None:
        self._spawn = process_factory or OwnedProcess.spawn
        self._slots = capacity if capacity is not None else asyncio.Semaphore(2)
        self._hosts: OrderedDict[str, Host] = OrderedDict()
        self._opening: set[str] = set()
        self._closing = False
        self._capture_root = capture_root.absolute() if capture_root else None
        self._captures: set[asyncio.Task[CapturedFrame]] = set()
        self._file_work: set[asyncio.Task[Any]] = set()
        self._files = ThreadPoolExecutor(max_workers=1, thread_name_prefix="portal-files")
        self._staging_inventory: Record | None = None

    def _host(self, scope: Record) -> Host:
        scope = cast(Record, binding(scope))
        host = self._hosts.get(cast(str, scope["grant_id"]))
        if host is None or host.binding != scope:
            raise error("portal_grant_changed")
        return host

    def _record(self, host: Host) -> Record:
        available = (
            self._capture_root is not None
            and host.capture_available
            and host.phase == "active"
            and not host.stop.is_set()
        )
        return {
            "grant_id": host.binding["grant_id"],
            "phase": host.phase,
            "error_code": host.code,
            "revision": host.revision,
            "clean": host.clean and host.released,
            "cleanup_pending": (host.stop.is_set() or host.phase in TERMINAL) and not host.released,
            "capture_available": available,
            "capture_error_code": None if available else host.capture_code or "portal_session_inactive",
        }

    async def _changed(self, host: Host) -> None:
        async with host.changed:
            host.revision += 1
            host.changed.notify_all()

    async def status(self, scope: Record) -> Record:
        return self._record(self._host(scope))

    async def wait(self, scope: Record, revision: int) -> Record:
        host = self._host(scope)
        async with host.changed:
            await host.changed.wait_for(lambda: host.revision != revision)
            return self._record(host)

    async def open(self, scope: Record, *, before_dispatch: Callable[[], Awaitable[None]]) -> Record:
        scope = cast(Record, binding(scope))
        if sys.platform != "linux":
            raise error("portal_platform_unavailable")
        if self._closing:
            raise error("portal_draining")
        if len({key for key, h in self._hosts.items() if not h.released} | self._opening) >= 4:
            raise error("portal_busy")
        if cast(str, scope["grant_id"]) in self._hosts or cast(str, scope["grant_id"]) in self._opening:
            raise error("portal_grant_changed")
        self._opening.add(cast(str, scope["grant_id"]))
        try:
            return await self._admit(scope, before_dispatch)
        finally:
            self._opening.discard(cast(str, scope["grant_id"]))

    async def _admit(self, scope: Record, before_dispatch: Callable[[], Awaitable[None]]) -> Record:
        await before_dispatch()
        if self._closing:
            raise error("portal_draining")
        while len(self._hosts) >= 32:
            victim = next(
                (
                    key
                    for key, h in self._hosts.items()
                    if h.released and not h.staging and (h.capture_task is None or h.capture_task.done())
                ),
                None,
            )
            if victim is None:
                raise error("portal_busy")
            del self._hosts[victim]
        host = Host(cast(dict[str, str], scope), last_seen=asyncio.get_running_loop().time())
        self._hosts[cast(str, scope["grant_id"])] = host
        host.runner = asyncio.create_task(
            self._run(host, before_dispatch), name="portal-owned-session", context=contextvars.Context()
        )
        host.runner.add_done_callback(lambda f: f.exception() if not f.cancelled() else None)
        try:
            async with asyncio.timeout(6):
                await host.ready.wait()
            if host.phase != "prepared":
                raise error(host.code or "portal_native_unavailable")
            await before_dispatch()
            if self._closing or host.stop.is_set():
                raise error("portal_draining")
            host.permitted = True
            await self._send(host, "permit")
            return self._record(host)
        except BaseException:
            host.stop.set()
            raise

    async def _send(self, host: Host, command: str, **extra: object) -> None:
        if host.owned is None or host.owned.process.stdin is None:
            raise error("portal_transport_failed")
        async with asyncio.timeout(1):
            host.owned.process.stdin.write(
                encode({"version": VERSION, "binding": host.binding, "command": command, **extra})
            )
            await host.owned.process.stdin.drain()

    async def _stdout(self, host: Host) -> None:
        stream = cast(OwnedProcess, host.owned).process.stdout
        if stream is None:
            raise error("portal_transport_failed")
        while frame := await stream.readline():
            host.total += len(frame)
            host.events += 1
            if host.total > MAX_TOTAL or host.events > MAX_EVENTS:
                raise error("portal_protocol_limit")
            value = decode(frame)
            if value.get("kind") == "frame":
                result = frame_event(value, host.binding)
                if (
                    host.phase != "active"
                    or host.stop.is_set()
                    or host.capture_reply is None
                    or host.capture_reply.done()
                    or result["capture_id"] != host.capture_id
                ):
                    raise error("portal_protocol_invalid")
                host.capture_reply.set_result(cast(Record, result["receipt"]))
                continue
            value = event(value, host.binding)
            host.last_seen = asyncio.get_running_loop().time()
            if value["kind"] == "heartbeat":
                if value["phase"] != host.phase or value["error_code"] is not None or value["clean"]:
                    raise error("portal_protocol_invalid")
                continue
            phase = cast(str, value["phase"])
            if phase not in TRANSITIONS[host.phase]:
                raise error("portal_protocol_invalid")
            if phase in {"creating", "selecting", "authorizing", "active"} and not host.permitted:
                raise error("portal_protocol_invalid")
            host.phase, host.code, host.clean = phase, cast(str | None, value["error_code"]), cast(bool, value["clean"])
            host.capture_available, host.capture_code = (
                cast(bool, value["capture_available"]),
                cast(str | None, value["capture_error_code"]),
            )
            await self._changed(host)
            if phase == "prepared":
                host.ready.set()
        if host.phase not in TERMINAL:
            raise error("portal_helper_lost")

    async def _stderr(self, host: Host) -> None:
        if cast(OwnedProcess, host.owned).process.stderr is None:
            raise error("portal_transport_failed")
        while block := await cast(asyncio.StreamReader, cast(OwnedProcess, host.owned).process.stderr).read(4096):
            host.total += len(block)
            if host.total > MAX_TOTAL:
                host.code = "portal_protocol_limit"
                host.stop.set()

    async def _watchdog(self, host: Host) -> None:
        end = asyncio.get_running_loop().time() + LIFETIME_SECONDS + 5
        while not host.stop.is_set():
            await asyncio.sleep(1)
            now = asyncio.get_running_loop().time()
            if now > end or now - host.last_seen > 8:
                host.code = "portal_expired"
                host.stop.set()
                return

    async def _run(self, host: Host, guard: Callable[[], Awaitable[None]]) -> None:
        tasks: list[asyncio.Task[Any]] = []
        try:
            async with asyncio.timeout(5):
                await self._slots.acquire()
                host.acquired = True
                if self._closing or host.stop.is_set():
                    raise error("portal_draining")
                await guard()
                host.starting = asyncio.create_task(
                    self._spawn(
                        [sys.executable, "-P", "-u", "-m", "omni.adapters.workers.portal"],
                        stdin=asyncio.subprocess.PIPE,
                        stdout=asyncio.subprocess.PIPE,
                        stderr=asyncio.subprocess.PIPE,
                        limit=MAX_FRAME + 1,
                        env=helper_environment(),
                        cwd=str(Path(__file__).resolve().parents[1]),
                    )
                )
                host.owned = await asyncio.shield(host.starting)
                if self._closing or host.stop.is_set():
                    raise error("portal_draining")
                await guard()
                await self._send(host, "open")
            output = asyncio.create_task(self._stdout(host))
            stderr = asyncio.create_task(self._stderr(host))
            watchdog = asyncio.create_task(self._watchdog(host))
            stopped = asyncio.create_task(host.stop.wait())
            tasks = [output, stderr, watchdog, stopped]
            done, _ = await asyncio.wait((output, stderr, stopped), return_when=asyncio.FIRST_COMPLETED)
            if output in done:
                await output
            elif stderr in done:
                await stderr
                async with asyncio.timeout(4):
                    await output
            else:
                await self._send(host, "close")
                async with asyncio.timeout(4):
                    await output
            if cast(OwnedProcess, host.owned).process.returncode not in {None, 0}:
                raise error("portal_helper_lost")
        except BusinessError as caught:
            host.code = caught.code if caught.code.startswith(("portal_", "desktop_")) else "portal_native_unavailable"
            host.phase = "failed"
            host.clean = False
        except TimeoutError:
            host.code = "portal_timeout"
            host.phase = "failed"
            host.clean = False
        except Exception:  # noqa: BLE001 -- never persist or export native pipe/error text
            host.code = "portal_transport_failed"
            host.phase = "failed"
            host.clean = False
        finally:
            host.stop.set()
            if host.capture_reply is not None and not host.capture_reply.done():
                host.capture_reply.set_exception(error(host.code or "portal_revoked"))
            host.ready.set()
            for task in tasks:
                if not task.done():
                    task.cancel()
            await asyncio.gather(*tasks, return_exceptions=True)
            await self._reclaim(host, 3)
            await self._changed(host)

    async def _file(self, operation: Callable[[], T]) -> T:
        async def actual() -> T:
            try:
                return await asyncio.get_running_loop().run_in_executor(
                    self._files, contextvars.copy_context().run, operation
                )
            except OSError:
                raise error("portal_frame_staging_unavailable") from None

        task = asyncio.create_task(actual(), name="portal-file")
        self._file_work.add(task)

        def finished(done: asyncio.Task[T]) -> None:
            self._file_work.discard(done)
            if not done.cancelled():
                done.exception()

        task.add_done_callback(finished)
        return await asyncio.shield(task)

    async def capture(self, scope: Record, *, before_dispatch: Callable[[], Awaitable[None]]) -> CapturedFrame:
        host = self._host(scope)
        if self._closing or self._capture_root is None or not self._record(host)["capture_available"]:
            raise error(host.capture_code or "portal_frame_unavailable")
        if (
            host.capture_task is not None
            and not host.capture_task.done()
            or len(self._captures) >= 4
            or host.capture_count >= 4
        ):
            raise error("portal_frame_busy")
        host.capture_count += 1
        task = asyncio.create_task(self._capture(host, before_dispatch), name="portal-capture")
        host.capture_task = task
        self._captures.add(task)

        def finished(done: asyncio.Task[CapturedFrame]) -> None:
            self._captures.discard(done)
            if not done.cancelled():
                done.exception()

        task.add_done_callback(finished)
        return await asyncio.shield(task)

    async def _capture(self, host: Host, guard: Callable[[], Awaitable[None]]) -> CapturedFrame:
        root = self._capture_root
        assert root is not None
        ticket = str(uuid.uuid4())
        path = root / ticket / "frame.png"
        host.capture_reply = asyncio.get_running_loop().create_future()
        host.capture_reply.add_done_callback(lambda f: f.exception() if not f.cancelled() else None)
        host.capture_id = ticket
        delivered = False
        try:
            async with asyncio.timeout(5):
                await guard()
                known = {p.parent.name for item in self._hosts.values() for p in item.staging}
                self._staging_inventory = await self._file(lambda: desktop_capture.inventory(root, known))
                host.staging.add(path)
                await self._file(lambda: desktop_capture.prepare(root, ticket))
                await guard()
                if host.phase != "active" or host.stop.is_set():
                    raise error("portal_revoked")
                await self._send(host, "capture", capture_id=ticket, capture_path=str(path))
                receipt = await asyncio.shield(host.capture_reply)
                delivered = True
                await guard()
                result = await self._file(lambda: desktop_capture.read(root, path, receipt))
                await guard()
                if host.phase != "active" or host.stop.is_set():
                    raise error("portal_revoked")
                return result
        except BaseException:
            host.stop.set()
            await self.stop(cast(Record, host.binding))
            raise
        finally:
            # A late native write can only be discarded after its process is
            # reaped; retained staging otherwise stays a shutdown obligation.
            if delivered or host.released:
                if self._file_work:
                    await asyncio.gather(*list(self._file_work), return_exceptions=True)
                await self._file(lambda: desktop_capture.discard(root, path))
                host.staging.discard(path)

    async def _reclaim(self, host: Host, timeout: float) -> bool:
        if host.cleanup is None or host.cleanup.done() and not host.released:
            host.cleanup = asyncio.create_task(
                self._reclaim_actual(host, timeout), name="portal-reclaim", context=contextvars.Context()
            )
            host.cleanup.add_done_callback(lambda f: f.exception() if not f.cancelled() else None)
        return await asyncio.shield(host.cleanup)

    async def _reclaim_actual(self, host: Host, timeout: float) -> bool:
        if host.released:
            return True
        if host.starting is not None and host.owned is None:
            try:
                host.owned = await asyncio.shield(host.starting)
            except Exception:  # noqa: BLE001 -- a failed spawn has no native session; actual tasks remain retained until done
                if not host.starting.done():
                    return False
        if host.owned is not None:
            if host.owned.process.stdin is not None:
                host.owned.process.stdin.close()
            try:
                reclaimed = await host.owned.terminate(timeout)
            except Exception:  # noqa: BLE001 -- keep the actual owner, never emit native reclamation text
                reclaimed = False
            if not reclaimed:
                host.code = "portal_cleanup_pending"
                host.phase = "failed"
                host.clean = False
                return False
        host.released = True
        if host.acquired:
            self._slots.release()
            host.acquired = False
        return True

    async def stop(self, scope: Record) -> bool:
        host = self._host(scope)
        host.stop.set()
        if host.runner is not None and not host.runner.done():
            try:
                async with asyncio.timeout(5):
                    await asyncio.shield(host.runner)
            except TimeoutError:
                return False
        result = await self._reclaim(host, 1)
        await self._changed(host)
        return result

    def health(self) -> Record:
        return {
            "admitting": len(self._opening),
            "resident": sum(h.acquired and not h.released for h in self._hosts.values()),
            "pending": sum(h.runner is not None and not h.runner.done() for h in self._hosts.values()),
            "cleanup_pending": sum(
                (h.stop.is_set() and not h.released)
                or (bool(h.staging) and h.capture_task is not None and h.capture_task.done())
                for h in self._hosts.values()
            ),
            "closing": self._closing,
            "max_resident": 2,
            "capture_available": any(self._record(h)["capture_available"] for h in self._hosts.values()),
            "captures_pending": len(self._captures),
            "staging_pending": sum(len(h.staging) for h in self._hosts.values()),
            "capture_staging": dict(self._staging_inventory) if self._staging_inventory else None,
        }

    def stop_accepting(self) -> None:
        self._closing = True
        for host in self._hosts.values():
            host.stop.set()

    async def close(self, timeout_s: float) -> bool:
        self.stop_accepting()
        tasks = [h.runner for h in self._hosts.values() if h.runner is not None and not h.runner.done()]
        if tasks:
            _, pending = await asyncio.wait(tasks, timeout=max(0, timeout_s))
            if pending:
                return False
        for host in self._hosts.values():
            if not await self._reclaim(host, 0):
                return False
        if self._captures:
            _, pending = await asyncio.wait(self._captures, timeout=max(0, timeout_s))
            if pending:
                return False
        if self._file_work:
            await asyncio.gather(*list(self._file_work), return_exceptions=True)
        for host in self._hosts.values():
            for path in list(host.staging):
                try:
                    root = self._capture_root
                    assert root is not None
                    await self._file(partial(desktop_capture.discard, root, path))
                    host.staging.discard(path)
                except (OSError, BusinessError):
                    return False
        self._files.shutdown(wait=False, cancel_futures=False)
        return all(h.released for h in self._hosts.values())
