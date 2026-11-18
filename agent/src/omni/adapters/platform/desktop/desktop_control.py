"""Owned parent transport for independent control consent; no input dispatch, capture grants or unowned process termination."""

from __future__ import annotations

import asyncio
import contextvars
import os
import sys
from collections import OrderedDict
from collections.abc import Awaitable, Callable
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, cast

from omni.adapters.platform.desktop.backend import helper_environment
from omni.adapters.platform.runtime.processes import OwnedProcess
from omni.application.ports import Record
from omni.domain.contracts import BusinessError
from omni.domain.desktop_control import PURPOSE, TERMINAL, VERSION, command, devices, event
from omni.domain.desktop_portal import LIFETIME_SECONDS, MAX_EVENTS, MAX_FRAME, MAX_TOTAL, binding, decode, encode

Guard = Callable[[], Awaitable[None]]
Spawn = Callable[..., Awaitable[OwnedProcess]]


def error(code: str) -> BusinessError:
    return BusinessError(code, "Independent control consent did not complete or is no longer available", status=503)


@dataclass(eq=False)
class Host:
    scope: dict[str, str]
    devices: int
    phase: str = "starting"
    failure: str | None = None
    code: str | None = None
    clean: bool = False
    acquired: bool = False
    released: bool = False
    permitted: bool = False
    open_sent: bool = False
    close_sent: bool = False
    sent: int = 0
    received: int = 0
    events: int = 0
    revision: int = 0
    last_seen: float = 0
    owned: OwnedProcess | None = None
    starting: asyncio.Task[OwnedProcess] | None = None
    runner: asyncio.Task[None] | None = None
    cleanup: asyncio.Task[bool] | None = None
    readers: list[asyncio.Task[None]] = field(default_factory=lambda: list[asyncio.Task[None]]())
    ready: asyncio.Event = field(default_factory=asyncio.Event)
    stop: asyncio.Event = field(default_factory=asyncio.Event)
    changed: asyncio.Condition = field(default_factory=asyncio.Condition)
    writing: asyncio.Lock = field(default_factory=asyncio.Lock)


class DesktopControlBackend:
    def __init__(self, *, process_factory: Spawn | None = None, capacity: asyncio.Semaphore | None = None) -> None:
        self._spawn = process_factory if process_factory is not None else OwnedProcess.spawn
        self._slots = capacity if capacity is not None else asyncio.Semaphore(2)
        self._hosts: OrderedDict[str, Host] = OrderedDict()
        self._opening: set[str] = set()
        self._closing = False

    def _host(self, scope: Record) -> Host:
        checked = binding(scope)
        host = self._hosts.get(checked["grant_id"])
        if host is None or host.scope != checked:
            raise error("portal_control_changed")
        return host

    def _record(self, host: Host) -> Record:
        phase = "failed" if host.failure else "closing" if host.stop.is_set() and not host.released else host.phase
        return {
            "grant_id": host.scope["grant_id"],
            "phase": phase,
            "devices": host.devices,
            "granted_devices": host.devices if phase == "system_authorized" and not host.stop.is_set() else 0,
            "input_authorized": False,
            "revision": host.revision,
            "error_code": host.failure or host.code,
            "clean": host.clean and host.released,
            "cleanup_pending": (host.stop.is_set() or host.phase in TERMINAL) and not host.released,
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

    async def _authority(self, guard: Guard) -> None:
        try:
            await guard()
        except BusinessError as caught:
            raise BusinessError(
                caught.code, "Control authority changed before native consent", status=caught.status
            ) from None

    async def _guard(self, host: Host, guard: Guard) -> None:
        if self._closing or host.stop.is_set():
            raise error("portal_control_draining")
        await self._authority(guard)
        if self._closing or host.stop.is_set():
            raise error("portal_control_draining")

    async def open(self, scope: Record, requested: int, *, before_dispatch: Guard) -> Record:
        checked, requested = binding(scope), devices(requested)
        if sys.platform != "linux":
            raise error("portal_platform_unavailable")
        identity = checked["grant_id"]
        if self._closing:
            raise error("portal_control_draining")
        if identity in self._hosts or identity in self._opening:
            raise error("portal_control_changed")
        if len({key for key, host in self._hosts.items() if not host.released} | self._opening) >= 4:
            raise error("portal_control_busy")
        self._opening.add(identity)
        host: Host | None = None
        try:
            await self._authority(before_dispatch)
            if self._closing:
                raise error("portal_control_draining")
            while len(self._hosts) >= 32:
                victim = next((key for key, old in self._hosts.items() if old.released), None)
                if victim is None:
                    raise error("portal_control_busy")
                del self._hosts[victim]
            host = Host(checked, requested, last_seen=asyncio.get_running_loop().time())
            self._hosts[identity] = host
            host.runner = asyncio.create_task(
                self._run(host, before_dispatch), name="control-consent-owner", context=contextvars.Context()
            )
            host.runner.add_done_callback(lambda task: task.exception() if not task.cancelled() else None)
            async with asyncio.timeout(6):
                await host.ready.wait()
            if host.phase != "prepared" or host.failure is not None:
                raise error(host.failure or host.code or "portal_control_unavailable")
            await self._guard(host, before_dispatch)
            if host.phase != "prepared":
                raise error("portal_control_changed")
            host.permitted = True
            await self._send(host, "permit")
            return self._record(host)
        except TimeoutError:
            if host is not None:
                host.failure = "portal_control_timeout"
            raise error("portal_control_timeout") from None
        except BaseException:
            if host is not None:
                host.stop.set()
            raise
        finally:
            self._opening.discard(identity)
            if host is not None and host.failure is not None:
                host.stop.set()

    async def _send(self, host: Host, operation: str) -> None:
        async with asyncio.timeout(1), host.writing:
            if host.owned is None or host.owned.process.stdin is None:
                raise error("portal_transport_failed")
            value: dict[str, object] = {"version": VERSION, "binding": host.scope, "command": operation}
            if operation == "open":
                value.update(purpose=PURPOSE, consent=True, devices=host.devices)
                host.open_sent = True
            if operation == "close":
                host.close_sent = True
            frame = encode(command(value, host.scope))
            host.sent += len(frame)
            if host.sent + host.received > MAX_TOTAL:
                raise error("portal_control_output_limit")
            host.owned.process.stdin.write(frame)
            await host.owned.process.stdin.drain()

    async def _stdout(self, host: Host) -> None:
        assert host.owned is not None and host.owned.process.stdout is not None
        stream = host.owned.process.stdout
        try:
            while frame := await stream.readline():
                host.received += len(frame)
                host.events += 1
                if host.sent + host.received > MAX_TOTAL or host.events > MAX_EVENTS:
                    raise error("portal_control_output_limit")
                value = event(decode(frame), host.scope, host.phase)
                if (
                    value["devices"] != host.devices
                    or not host.permitted
                    and value["phase"] in {"creating", "selecting", "authorizing", "system_authorized"}
                ):
                    raise error("portal_control_invalid")
                host.phase = cast(str, value["phase"])
                host.code = cast(str | None, value["error_code"])
                host.clean = cast(bool, value["clean"])
                host.last_seen = asyncio.get_running_loop().time()
                if host.phase == "prepared" or host.phase in TERMINAL:
                    host.ready.set()
                if host.phase in TERMINAL:
                    host.stop.set()
                await self._changed(host)
            if host.phase not in TERMINAL:
                raise error("portal_control_helper_lost")
        except Exception as caught:  # noqa: BLE001 -- reject the proof but drain its owned pipe until actual process reclamation
            host.failure = caught.code if isinstance(caught, BusinessError) else "portal_control_invalid"
            host.clean = False
            host.ready.set()
            host.stop.set()
            await self._changed(host)
            while await stream.read(4096):
                pass

    async def _stderr(self, host: Host) -> None:
        assert host.owned is not None and host.owned.process.stderr is not None
        while block := await host.owned.process.stderr.read(4096):
            host.received = min(MAX_TOTAL + 1, host.received + len(block))
            if host.sent + host.received > MAX_TOTAL and host.failure is None:
                host.failure = "portal_control_output_limit"
                host.clean = False
                host.ready.set()
                host.stop.set()
                await self._changed(host)

    async def _watchdog(self, host: Host) -> None:
        deadline = asyncio.get_running_loop().time() + LIFETIME_SECONDS + 5
        while not host.stop.is_set():
            await asyncio.sleep(1)
            now = asyncio.get_running_loop().time()
            if now >= deadline or now - host.last_seen > 8:
                host.failure = "portal_expired"
                host.stop.set()

    async def _start(self, host: Host) -> OwnedProcess:
        host.owned = await self._spawn(
            [
                sys.executable,
                "-P" if os.environ.get("OMNI_TEST_OFFLINE") == "1" else "-I",
                "-u",
                "-m",
                "omni.adapters.workers.desktop_control",
            ],
            stdin=asyncio.subprocess.PIPE,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
            limit=MAX_FRAME + 1,
            env=helper_environment(),
            cwd=str(Path(__file__).resolve().parents[1]),
        )
        return host.owned

    async def _run(self, host: Host, guard: Guard) -> None:
        auxiliary: list[asyncio.Task[Any]] = []
        try:
            async with asyncio.timeout(5):
                await self._slots.acquire()
                host.acquired = True
                await self._guard(host, guard)
                host.starting = asyncio.create_task(
                    self._start(host), name="control-consent-start", context=contextvars.Context()
                )
                await asyncio.shield(host.starting)
                await self._guard(host, guard)
                for operation in (self._stdout, self._stderr):
                    task = asyncio.create_task(
                        operation(host), name="control-consent-pipe", context=contextvars.Context()
                    )
                    task.add_done_callback(lambda future: future.exception() if not future.cancelled() else None)
                    host.readers.append(task)
                await self._send(host, "open")
            output, stderr = host.readers
            watchdog = asyncio.create_task(self._watchdog(host), context=contextvars.Context())
            stopped = asyncio.create_task(host.stop.wait(), context=contextvars.Context())
            auxiliary.extend((watchdog, stopped))
            done, _ = await asyncio.wait((output, stderr, stopped), return_when=asyncio.FIRST_COMPLETED)
            if host.failure is not None:
                raise error(host.failure)
            if output in done:
                await output
            else:
                if stderr in done:
                    await stderr
                if host.stop.is_set() and host.phase not in TERMINAL and not host.close_sent:
                    await self._send(host, "close")
                async with asyncio.timeout(4):
                    await output
        except BusinessError as caught:
            host.failure = (
                caught.code if caught.code.startswith(("portal_", "desktop_")) else "portal_control_unavailable"
            )
        except TimeoutError:
            host.failure = "portal_control_timeout"
        except asyncio.CancelledError:
            host.failure = "portal_control_cancelled"
            raise
        except Exception:  # noqa: BLE001 -- native pipe/provider bytes must never become public errors
            host.failure = "portal_transport_failed"
        finally:
            host.stop.set()
            host.ready.set()
            for task in auxiliary:
                if not task.done():
                    task.cancel()
            await asyncio.gather(*auxiliary, return_exceptions=True)
            try:
                if not await self._reclaim(host):
                    host.failure = host.failure or "portal_cleanup_pending"
            except Exception:  # noqa: BLE001 -- keep failed cleanup owned without exporting process diagnostics
                host.failure = host.failure or "portal_cleanup_pending"
            await self._changed(host)

    async def _cleanup(self, host: Host) -> bool:
        try:
            return await self._cleanup_actual(host)
        finally:
            await self._changed(host)

    async def _cleanup_actual(self, host: Host) -> bool:
        if host.starting is not None:
            try:
                await asyncio.shield(host.starting)
            except Exception:  # noqa: BLE001 -- a late/failed start retains exactly one owner
                if host.owned is not None:
                    return False
        if host.owned is not None:
            if host.owned.process.stdin is not None:
                host.owned.process.stdin.close()
            if not await host.owned.terminate(1):
                return False
        for task in host.readers:
            if not task.done():
                task.cancel()
        await asyncio.gather(*host.readers, return_exceptions=True)
        host.readers.clear()
        host.owned = None
        host.starting = None
        if not host.released:
            host.released = True
            host.clean = host.clean or not host.open_sent
            if host.clean and host.failure == "portal_cleanup_pending":
                host.failure = None
            if host.acquired:
                self._slots.release()
        return True

    def _ensure_cleanup(self, host: Host) -> asyncio.Task[bool]:
        if (
            host.cleanup is None
            or host.cleanup.done()
            and (host.cleanup.cancelled() or host.cleanup.exception() is not None or not host.cleanup.result())
        ):
            host.cleanup = asyncio.create_task(
                self._cleanup(host), name="control-consent-reclaim", context=contextvars.Context()
            )
            host.cleanup.add_done_callback(lambda future: future.exception() if not future.cancelled() else None)
        return host.cleanup

    async def _reclaim(self, host: Host) -> bool:
        return await asyncio.shield(self._ensure_cleanup(host))

    async def revoke(self, scope: Record, timeout_s: float = 5) -> Record:
        host = self._host(scope)
        host.stop.set()
        await self._changed(host)
        if host.runner is not None and not host.runner.done():
            await asyncio.wait((host.runner,), timeout=max(0, timeout_s))
        elif not host.released:
            work = self._ensure_cleanup(host)
            await asyncio.wait((work,), timeout=max(0, timeout_s))
        return self._record(host)

    def health(self) -> Record:
        return {
            "closing": self._closing,
            "resident": sum(host.acquired and not host.released for host in self._hosts.values()),
            "pending": len(
                {key for key, host in self._hosts.items() if host.runner is not None and not host.runner.done()}
                | self._opening
            ),
            "cleanup_pending": sum(host.stop.is_set() and not host.released for host in self._hosts.values()),
            "retained": len(self._hosts),
            "input_authorized": False,
        }

    def stop_accepting(self) -> None:
        self._closing = True
        for host in self._hosts.values():
            host.stop.set()

    async def close(self, timeout_s: float = 30) -> bool:
        self.stop_accepting()
        deadline = asyncio.get_running_loop().time() + max(0, timeout_s)
        for host in list(self._hosts.values()):
            await self.revoke(cast(Record, host.scope), max(0, deadline - asyncio.get_running_loop().time()))
        return not self._opening and all(
            host.released
            and (host.runner is None or host.runner.done())
            and (host.cleanup is None or host.cleanup.done())
            for host in self._hosts.values()
        )
