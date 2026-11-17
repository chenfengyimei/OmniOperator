"""Owned persistent X11 guardian transport; no native APIs, target rebinding from verify, unbounded queues or input."""

from __future__ import annotations

import asyncio
import contextvars
import os
import sys
import uuid
from collections import deque
from collections.abc import Awaitable, Callable, Mapping
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Protocol, cast

from ...domain.contracts import BusinessError
from ...domain.desktop import MAX_FRAME, validate_target
from ...domain.linux_window_leases import (
    LIFETIME_S,
    MAX_COMMANDS,
    MAX_GROUPS,
    MAX_TOTAL_BYTES,
    VERSION,
    Payload,
    decode,
    encode,
    parse_request,
    parse_response,
)
from .processes import OwnedProcess

Spawn = Callable[..., Awaitable[OwnedProcess]]
Guard = Callable[[], Awaitable[None]]


class LeaseClientPort(Protocol):
    def health(self) -> dict[str, int | bool]: ...
    def quiescent(self) -> bool: ...
    def stop_accepting(self) -> None: ...
    def is_leased(self, target: Payload) -> bool: ...
    async def bind(
        self, targets: list[Payload], deadline: float, before_dispatch: Guard | None = None
    ) -> list[Payload]: ...
    async def guard(self, target: Payload, deadline: float, before_dispatch: Guard | None = None) -> Payload: ...
    async def reclaim(self, timeout_s: float) -> bool: ...
    async def close(self, timeout_s: float = 30) -> bool: ...


def error(code: str) -> BusinessError:
    return BusinessError(code, "Window identity lease is no longer available; refresh its desktop snapshot", status=503)


@dataclass(eq=False)
class Host:
    started: float
    starting: asyncio.Task[OwnedProcess] | None = None
    owned: OwnedProcess | None = None
    readers: list[asyncio.Task[None]] = field(default_factory=lambda: list[asyncio.Task[None]]())
    exited: asyncio.Task[int] | None = None
    cleanup: asyncio.Task[bool] | None = None
    reply: asyncio.Future[Payload] | None = None
    expected: Payload | None = None
    lock: asyncio.Lock = field(default_factory=asyncio.Lock)
    broken: bool = False
    closing: bool = False
    released: bool = False
    commands: int = 0
    received: int = 0
    sent: int = 0


class LinuxLeaseClient:
    def __init__(
        self, factory: Spawn, capacity: asyncio.Semaphore, environment: Callable[[], Mapping[str, str]]
    ) -> None:
        self._spawn, self._slots, self._environment = factory, capacity, environment
        self._hosts: set[Host] = set()
        self._current: Host | None = None
        self._aliases: dict[str, tuple[Host, Payload, Payload]] = {}
        self._groups: deque[list[str]] = deque()
        self._opening = asyncio.Lock()
        self._closing = False

    def health(self) -> dict[str, int | bool]:
        return {
            "closing": self._closing,
            "resident": len(self._hosts),
            "cleanup_pending": sum(host.closing for host in self._hosts),
        }

    def quiescent(self) -> bool:
        # A healthy idle read-only subscription cannot hold a completed action's
        # resource lease forever. Startup, active RPC and uncertain reclamation
        # remain busy. close() separately requires every process to be gone.
        return not (self._closing and self._hosts) and all(
            not host.closing
            and not host.broken
            and host.starting is not None
            and host.starting.done()
            and host.reply is None
            for host in self._hosts
        )

    def stop_accepting(self) -> None:
        self._closing = True

    def is_leased(self, target: Payload) -> bool:
        return (
            target.get("platform") == "linux"
            and target.get("backend") == "x11"
            and str(target.get("native_id", "")).startswith("x11-live:")
        )

    def _forget(self, host: Host) -> None:
        if self._current is host:
            self._current = None
            self._aliases.clear()
            self._groups.clear()
        if host.reply is not None and not host.reply.done():
            host.reply.set_exception(error("desktop_target_changed"))

    def _schedule_cleanup(self, host: Host) -> None:
        host.broken = host.closing = True
        self._forget(host)
        if (
            host.cleanup is None
            or host.cleanup.done()
            and (host.cleanup.cancelled() or host.cleanup.exception() is not None or not host.cleanup.result())
        ):
            host.cleanup = asyncio.create_task(
                self._cleanup(host), name="x11-lease-reclaim", context=contextvars.Context()
            )
            host.cleanup.add_done_callback(lambda future: future.exception() if not future.cancelled() else None)

    async def _cleanup(self, host: Host) -> bool:
        if host.starting is not None:
            try:
                await asyncio.shield(host.starting)
            except Exception:  # noqa: BLE001 -- failed/late spawning still has one retained owner
                if host.owned is not None:
                    return False
        if host.owned is not None:
            if host.owned.process.stdin is not None:
                host.owned.process.stdin.close()
            if not await host.owned.terminate(1):
                return False
        tasks: list[asyncio.Task[Any]] = [*host.readers, *((host.exited,) if host.exited else ())]
        for task in tasks:
            if not task.done():
                task.cancel()
        await asyncio.gather(*tasks, return_exceptions=True)
        if not host.released:
            host.released = True
            self._hosts.discard(host)
            self._slots.release()
        return True

    async def _retire(self, host: Host, deadline: float) -> bool:
        self._schedule_cleanup(host)
        assert host.cleanup is not None
        done, _ = await asyncio.wait((host.cleanup,), timeout=max(0, deadline - asyncio.get_running_loop().time()))
        return bool(
            done and not host.cleanup.cancelled() and host.cleanup.exception() is None and host.cleanup.result()
        )

    async def _stdout(self, host: Host) -> None:
        assert host.owned is not None and host.owned.process.stdout is not None
        try:
            while frame := await host.owned.process.stdout.readline():
                host.received += len(frame)
                if (
                    host.received + host.sent > MAX_TOTAL_BYTES
                    or host.expected is None
                    or host.reply is None
                    or host.reply.done()
                ):
                    raise error("desktop_protocol_invalid")
                reply = parse_response(decode(frame), host.expected)
                host.reply.set_result(reply)
        finally:
            if not host.closing:
                self._schedule_cleanup(host)

    async def _stderr(self, host: Host) -> None:
        assert host.owned is not None and host.owned.process.stderr is not None
        try:
            while block := await host.owned.process.stderr.read(4096):
                host.received += len(block)
                if host.received + host.sent > MAX_TOTAL_BYTES:
                    self._schedule_cleanup(host)
                    return
        finally:
            if not host.closing:
                self._schedule_cleanup(host)

    async def _start(self, host: Host) -> OwnedProcess:
        host.owned = await self._spawn(
            [
                sys.executable,
                "-P" if os.environ.get("OMNI_TEST_OFFLINE") == "1" else "-I",
                "-u",
                "-m",
                "omni.adapters.workers.linux_window_lease",
            ],
            stdin=asyncio.subprocess.PIPE,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
            limit=MAX_FRAME + 1,
            env=self._environment(),
            cwd=str(Path(__file__).resolve().parent),
        )
        for operation in (self._stdout, self._stderr):
            task = asyncio.create_task(operation(host), name="x11-lease-pipe", context=contextvars.Context())
            task.add_done_callback(lambda future: future.exception() if not future.cancelled() else None)
            host.readers.append(task)
        host.exited = asyncio.create_task(
            host.owned.process.wait(), name="x11-lease-exit", context=contextvars.Context()
        )
        host.exited.add_done_callback(lambda future: self._schedule_cleanup(host) if not host.closing else None)
        return host.owned

    async def _admit(self, deadline: float) -> Host:
        async with asyncio.timeout_at(deadline), self._opening:
            if self._closing:
                raise error("desktop_closing")
            host = self._current
            if host is not None and (
                host.commands >= MAX_COMMANDS - 5 or asyncio.get_running_loop().time() - host.started >= LIFETIME_S - 60
            ):
                await self._retire(host, deadline)
                host = None
            if host is None or host.broken or host.closing:
                async with asyncio.timeout_at(deadline):
                    await self._slots.acquire()
                    if self._closing:
                        self._slots.release()
                        raise error("desktop_closing")
                    host = Host(asyncio.get_running_loop().time())
                    self._hosts.add(host)
                    self._current = host
                    host.starting = asyncio.create_task(
                        self._start(host), name="x11-lease-start", context=contextvars.Context()
                    )
                    try:
                        await asyncio.shield(host.starting)
                    except BaseException:
                        self._schedule_cleanup(host)
                        raise
            return host

    async def _query(
        self, host: Host, operation: str, deadline: float, before_dispatch: Guard | None, **fields: object
    ) -> object:
        admitted = False
        try:
            async with asyncio.timeout_at(deadline), host.lock:
                admitted = True
                if self._closing or host.closing or host.broken or host.commands >= MAX_COMMANDS - 1:
                    raise error("desktop_target_changed")
                if before_dispatch is not None:
                    await before_dispatch()
                if self._closing or host.closing or asyncio.get_running_loop().time() >= deadline:
                    raise error("desktop_closing")
                request = parse_request(
                    {"version": VERSION, "request_id": str(uuid.uuid4()), "operation": operation, **fields}
                )
                frame = encode(request)
                if host.sent + host.received + len(frame) > MAX_TOTAL_BYTES:
                    raise error("desktop_protocol_invalid")
                assert host.owned is not None and host.owned.process.stdin is not None
                host.expected = request
                host.reply = asyncio.get_running_loop().create_future()
                host.reply.add_done_callback(lambda future: future.exception() if not future.cancelled() else None)
                host.commands += 1
                host.sent += len(frame)
                host.owned.process.stdin.write(frame)
                await host.owned.process.stdin.drain()
                result = await asyncio.shield(host.reply)
                if host.broken or host.closing:
                    raise error("desktop_target_changed")
                return result["result"]
        except BaseException:
            if admitted:
                self._schedule_cleanup(host)
            raise
        finally:
            if admitted:
                host.expected = None
                host.reply = None

    async def bind(
        self, targets: list[Payload], deadline: float, before_dispatch: Guard | None = None
    ) -> list[Payload]:
        host = await self._admit(deadline)
        result = cast(list[Payload], await self._query(host, "bind", deadline, before_dispatch, targets=targets))
        if self._current is not host or host.broken:
            raise error("desktop_target_changed")
        group: list[str] = []
        for target, original in zip(result, targets, strict=True):
            alias = cast(str, target["native_id"])
            self._aliases[alias] = host, validate_target(target), validate_target(original)
            group.append(alias)
        self._groups.append(group)
        while len(self._groups) > MAX_GROUPS:
            for alias in self._groups.popleft():
                self._aliases.pop(alias, None)
        return result

    async def guard(self, target: Payload, deadline: float, before_dispatch: Guard | None = None) -> Payload:
        alias = str(target.get("native_id", ""))
        known = self._aliases.get(alias)
        if known is None:
            raise error("desktop_target_changed")
        host, expected, original = known
        if validate_target(target) != expected:
            raise error("desktop_target_changed")
        actual = await self._query(host, "verify", deadline, before_dispatch, target=target)
        if actual != original:
            self._schedule_cleanup(host)
            raise error("desktop_target_changed")
        return dict(original)

    async def reclaim(self, timeout_s: float) -> bool:
        deadline = asyncio.get_running_loop().time() + max(0, timeout_s)
        for host in list(self._hosts):
            if host.closing:
                await self._retire(host, deadline)
        return self.quiescent()

    async def close(self, timeout_s: float = 30) -> bool:
        self.stop_accepting()
        deadline = asyncio.get_running_loop().time() + max(0, timeout_s)
        for host in list(self._hosts):
            await self._retire(host, deadline)
        return not self._hosts
