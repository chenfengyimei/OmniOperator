"""Own startup rollback and one shared shutdown budget; never dispatches business work."""

from __future__ import annotations

import asyncio
import logging
from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from typing import cast

from ..application.ports import Record

log = logging.getLogger("omni.v2.lifecycle")


@dataclass(frozen=True)
class CloseStep:
    name: str
    phase: int
    close: Callable[[float], Awaitable[object]]


class Lifetime:
    def __init__(self) -> None:
        self._steps: dict[str, CloseStep] = {}
        self._stops: dict[str, Callable[[], None]] = {}
        self._tasks: dict[str, asyncio.Task[object]] = {}
        self._coordinator: asyncio.Task[Record] | None = None
        self.closing = self.complete = False
        self.residual: list[str] = []

    def own(
        self, name: str, phase: int, close: Callable[[float], Awaitable[object]], stop: Callable[[], None] | None = None
    ) -> None:
        if self.closing:
            raise RuntimeError("Cannot attach a component after draining begins")
        self._steps[name] = CloseStep(name, phase, close)
        if stop:
            self._stops[name] = stop

    @staticmethod
    def _successful(task: asyncio.Task[object]) -> bool:
        if not task.done() or task.cancelled() or task.exception() is not None:
            return False
        value = task.result()
        if isinstance(value, dict):
            return cast(dict[str, object], value).get("complete") is True
        return value is None or value is True

    async def _close_step(self, name: str, remaining: float) -> object:
        return await self._steps[name].close(remaining)

    async def _perform(self, timeout_s: float) -> Record:
        deadline = asyncio.get_running_loop().time() + max(0, timeout_s)
        for phase in sorted({step.phase for step in self._steps.values()}):
            names = [step.name for step in self._steps.values() if step.phase == phase]
            inherited: set[str] = set()
            for name in names:
                task = self._tasks.get(name)
                if task is not None and not task.done():
                    inherited.add(name)
                if task is None or (task.done() and not self._successful(task)):
                    remaining = max(0, deadline - asyncio.get_running_loop().time())
                    self._tasks[name] = asyncio.create_task(self._close_step(name, remaining), name="close-" + name)
            tasks = [self._tasks[name] for name in names]
            await asyncio.wait(tasks, timeout=max(0, deadline - asyncio.get_running_loop().time()))
            # A previous coordinator may already be done while one of its
            # short-budget component waiters is still finishing. Give only
            # those completed false results this caller's remaining budget,
            # once; never duplicate an actually pending close or spin on a
            # component that rejects its fresh budget.
            retried = False
            for name in inherited:
                task = self._tasks[name]
                remaining = max(0, deadline - asyncio.get_running_loop().time())
                if (
                    remaining
                    and task.done()
                    and not task.cancelled()
                    and task.exception() is None
                    and not self._successful(task)
                ):
                    self._tasks[name] = asyncio.create_task(self._close_step(name, remaining), name="close-" + name)
                    retried = True
            if retried:
                await asyncio.wait(
                    [self._tasks[name] for name in names], timeout=max(0, deadline - asyncio.get_running_loop().time())
                )
            self.residual = [name for name in names if not self._successful(self._tasks[name])]
            if self.residual:
                for name in self.residual:
                    task = self._tasks[name]
                    if task.done() and not task.cancelled() and task.exception() is not None:
                        log.error(
                            "Component shutdown failed",
                            extra={
                                "event": "component.close_failed",
                                "component": name,
                                "error_code": type(task.exception()).__name__,
                            },
                        )
                return cast(Record, {"complete": False, "residual": list(self.residual)})
        self.complete = True
        self.residual = []
        return cast(Record, {"complete": True, "residual": []})

    async def close(self, timeout_s: float = 30) -> Record:
        deadline = asyncio.get_running_loop().time() + max(0, timeout_s)
        if self.complete:
            return cast(Record, {"complete": True, "residual": []})
        self.stop_accepting()
        reused = self._coordinator is not None and not self._coordinator.done()
        if self._coordinator is None or self._coordinator.done():
            self._coordinator = asyncio.create_task(self._perform(timeout_s), name="omni-shutdown")
        coordinator = self._coordinator
        _, pending = await asyncio.wait((coordinator,), timeout=max(0, timeout_s))
        if pending:
            residual = [name for name, task in self._tasks.items() if not self._successful(task)]
            return cast(Record, {"complete": False, "residual": residual or ["shutdown_coordinator"]})
        result = coordinator.result()
        remaining = max(0, deadline - asyncio.get_running_loop().time())
        if reused and not result["complete"] and remaining:
            # A second caller may have joined the old short-budget coordinator
            # just before it reports False. Spend this caller's fresh budget,
            # reusing every actual pending component task as usual.
            if self._coordinator is coordinator:
                self._coordinator = asyncio.create_task(self._perform(remaining), name="omni-shutdown-retry")
            _, pending = await asyncio.wait((self._coordinator,), timeout=remaining)
            if not pending:
                return self._coordinator.result()
        return result

    def stop_accepting(self) -> None:
        if not self.closing:
            self.closing = True
            for name, stop in self._stops.items():
                try:
                    stop()
                except Exception:
                    log.exception("Admission stop failed", extra={"event": "component.stop_failed", "component": name})


async def close_sync(operation: Callable[[], object]) -> object:
    # A single owned close task stays charged while synchronous cleanup runs.
    return await asyncio.to_thread(operation)


async def close_until_quiescent(
    close: Callable[[float], Awaitable[Record]], initial_budget: float
) -> Record:
    """Report the bounded normal shutdown; keep the loop and actual ownership for residual cleanup."""
    result = await close(max(0, initial_budget))
    warned = False
    while result.get("complete") is not True:
        if not warned:
            log.warning(
                "Shutdown budget exhausted; actual cleanup remains owned",
                extra={
                    "event": "runtime.shutdown_pending",
                    "residual_count": len(pending) if isinstance((pending := result.get("residual")), list) else 0,
                },
            )
            warned = True
        await asyncio.sleep(0.25)
        result = await close(5)
    return result
