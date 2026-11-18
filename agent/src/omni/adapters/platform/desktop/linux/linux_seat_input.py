"""One seat's kernel-input guard with owned executor/FD watches; no authority, input data capture or compositor-target proof."""

from __future__ import annotations

import asyncio
from collections.abc import Awaitable, Callable
from typing import Protocol, TypeVar

from omni.domain.contracts import BusinessError
from omni.adapters.platform.desktop.linux.linux_bus import failure
from omni.adapters.platform.desktop.linux.linux_evdev_input import EvdevInput
from omni.adapters.platform.desktop.linux.linux_session import SessionInspector

T = TypeVar("T")


class InputPort(Protocol):
    @property
    def fds(self) -> tuple[int, ...]: ...
    def open(self, seat: str, uid: int) -> None: ...
    def check(self) -> None: ...
    def close(self) -> bool: ...


class WatchPort(Protocol):
    def add(self, descriptor: int, callback: Callable[[], None]) -> None: ...
    def remove(self, descriptor: int) -> None: ...


class NativeWatch:
    def add(self, descriptor: int, callback: Callable[[], None]) -> None:
        asyncio.get_running_loop().add_reader(descriptor, callback)

    def remove(self, descriptor: int) -> None:
        asyncio.get_running_loop().remove_reader(descriptor)


class SeatInput:
    def __init__(
        self,
        inspector: SessionInspector,
        guard: Callable[[], Awaitable[None]],
        *,
        factory: Callable[[], InputPort] = EvdevInput,
        watch: WatchPort | None = None,
    ) -> None:
        self.inspector, self.guard, self.factory = inspector, guard, factory
        self.watch = NativeWatch() if watch is None else watch
        self.source: InputPort | None = None
        self.operation: asyncio.Task[None] | None = None
        self._close_task: asyncio.Task[bool] | None = None
        self._watching: list[int] = []
        self._attempted = self._ready = self._failed = self._closed = False

    def changed(self) -> None:
        self._failed, self._ready = True, False
        # Readiness stays level-triggered because event contents are never
        # consumed. Unwatch after latching to avoid a hot loop; native fds
        # remain owned until the original close can settle them.
        for descriptor in self._watching[:]:
            try:
                self.watch.remove(descriptor)
            except Exception:  # noqa: BLE001 -- retain failed watch removal for the original close owner
                continue
            self._watching.remove(descriptor)

    def _valid(self) -> None:
        if self._failed or self._closed:
            raise failure("portal_input_changed")

    async def _work(self, function: Callable[[], T]) -> T:
        work = asyncio.get_running_loop().run_in_executor(None, function)
        cancelled = False
        while True:
            try:
                result = await asyncio.shield(work)
                break
            except asyncio.CancelledError:
                cancelled = True
        if cancelled:
            raise asyncio.CancelledError
        return result

    async def _snapshot(self) -> None:
        self._valid()
        await self.guard()
        assert self.source is not None
        await self._work(self.source.check)
        await self.guard()
        self._valid()

    async def _prepare(self) -> None:
        try:
            self._valid()
            await self.guard()
            session = self.inspector.session
            if session is None or session.kind != "wayland" or session.uid != self.inspector.ops.uid:
                raise failure("portal_input_unverified")
            source = self.factory()
            self.source = source  # retain actual native owner before its first I/O
            await self._work(lambda: source.open(session.seat, session.uid))
            self._valid()
            fds = self.source.fds
            if not 3 <= len(fds) <= 66 or len(set(fds)) != len(fds) or any(fd < 0 for fd in fds):
                raise failure("portal_input_unverified")
            for descriptor in fds:
                self._watching.append(descriptor)
                self.watch.add(descriptor, self.changed)
            await asyncio.sleep(0.25)
            await self._snapshot()
            self._ready = True
        except (BusinessError, asyncio.CancelledError):
            self.changed()
            raise
        except Exception:  # noqa: BLE001 -- native paths/provider text cannot escape the guard
            self.changed()
            raise failure("portal_input_unavailable") from None

    @staticmethod
    def _observe(task: asyncio.Task[object]) -> None:
        if not task.cancelled():
            task.exception()

    async def prepare(self) -> None:
        if self._attempted or self._closed:
            raise failure("portal_state_invalid")
        self._attempted = True
        self.operation = asyncio.create_task(self._prepare(), name="owned-seat-input-prepare")
        self.operation.add_done_callback(self._observe)
        try:
            async with asyncio.timeout(3):
                await asyncio.shield(self.operation)
        except BaseException:
            self.changed()
            raise

    async def _check(self) -> None:
        try:
            await self._snapshot()
        except (BusinessError, asyncio.CancelledError):
            self.changed()
            raise
        except Exception:  # noqa: BLE001 -- native paths/provider text cannot escape the guard
            self.changed()
            raise failure("portal_input_unavailable") from None

    async def check(self) -> None:
        self._valid()
        if not self._ready:
            raise failure("portal_input_unverified")
        if self.operation is None or self.operation.done():
            self.operation = asyncio.create_task(self._check(), name="owned-seat-input-check")
            self.operation.add_done_callback(self._observe)
        try:
            async with asyncio.timeout(1):
                await asyncio.shield(self.operation)
        except BaseException:
            self.changed()
            raise

    async def close(self) -> bool:
        self._closed, self._ready = True, False
        if self.operation is not None and not self.operation.done():
            return False
        for descriptor in self._watching[:]:
            try:
                self.watch.remove(descriptor)
            except Exception:  # noqa: BLE001 -- callback removal must be certain before closing its fd
                return False  # retain fd while its loop callback may remain registered
            self._watching.remove(descriptor)
        if self.source is None:
            return True
        if self._close_task is None:
            self._close_task = asyncio.create_task(self._work(self.source.close), name="owned-seat-input-close")
            self._close_task.add_done_callback(self._observe)
        done, _pending = await asyncio.wait((self._close_task,), timeout=1)
        return (
            bool(done)
            and not self._close_task.cancelled()
            and self._close_task.exception() is None
            and self._close_task.result() is True
        )
