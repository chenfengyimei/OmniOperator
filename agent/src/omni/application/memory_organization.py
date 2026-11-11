"""Own bounded exact-fact proposals and explicit owner decisions; never calls a model or erases facts."""

from __future__ import annotations

import asyncio
from collections.abc import Awaitable, Callable
from typing import TypeVar, cast

from ..domain.contracts import BusinessError, Outcome
from .ports import DatabasePort, Observer, Record

T = TypeVar("T")


class MemoryOrganizationService:
    def __init__(self, database: DatabasePort, observer: Observer) -> None:
        self._db, self._observer = database, observer
        self._pending: set[asyncio.Task[object]] = set()
        self._slots = asyncio.Semaphore(2)
        self._closing = False
        self._notify: Callable[[], None] = lambda: None

    def on_queued(self, notify: Callable[[], None]) -> None:
        self._notify = notify

    async def _execute(self, operation: Callable[[], Awaitable[T]]) -> T:
        if self._closing:
            raise BusinessError("memory_organization_closing", "Memory organization is draining", status=503)
        if len(self._pending) >= 8:
            raise BusinessError(
                "memory_organization_busy", "Memory organization requests are bounded", status=503, retryable=True
            )

        async def perform():
            async with self._slots:
                return await operation()

        task = asyncio.create_task(perform(), name="memory-organization-operation")
        self._pending.add(task)

        def complete(future: asyncio.Task[T]) -> None:
            self._pending.discard(cast(asyncio.Task[object], future))
            if not future.cancelled():
                future.exception()

        task.add_done_callback(complete)
        return await asyncio.shield(task)

    @staticmethod
    def _public(row: Record) -> Record:
        return {key: value for key, value in row.items() if key not in {"request_key", "request_sha256"}}

    async def submit(self, agent_id: str, request: Record) -> Record:
        async def perform():
            result = await self._db.write(lambda u: u.memory_organization_enqueue(agent_id, request))
            self._notify()
            return self._public(result)

        return await self._execute(perform)

    async def page(self, agent_id: str, after: int = 0, limit: int = 20) -> Record:
        async def perform():
            rows = await self._db.read(lambda u: u.memory_organization_page(agent_id, after, limit))
            return cast(
                Record,
                {
                    "items": [self._public(row) for row in rows],
                    "next_after": rows[-1]["sequence"] if len(rows) == limit else None,
                },
            )

        return await self._execute(perform)

    async def get(self, agent_id: str, batch_id: str) -> Record:
        async def perform():
            return self._public(await self._db.read(lambda u: u.memory_organization_get(agent_id, batch_id)))

        return await self._execute(perform)

    async def cancel(self, agent_id: str, batch_id: str, revision: int) -> Record:
        async def perform():
            return self._public(
                await self._db.write(lambda u: u.memory_organization_cancel(agent_id, batch_id, revision))
            )

        return await self._execute(perform)

    async def decide(self, agent_id: str, candidate_id: str, decision: Record) -> Record:
        async def perform():
            with self._observer.context(agent_id=agent_id, operation_id=candidate_id):
                result = await self._db.write(lambda u: u.memory_organization_decide(agent_id, candidate_id, decision))
                if result.get("valid") is False:
                    # Persist invalidation before reporting the conflict to the caller.
                    raise BusinessError("memory_organization_stale", "Captured facts changed; review a new proposal")
                return self._public(result)

        return await self._execute(perform)

    async def process_one(self) -> bool:
        """A bounded local classification commits entirely inside one short transaction; no external lease is needed."""

        async def perform():
            target = await self._db.read(lambda u: u.memory_organization_next())
            if not target:
                return False
            agent, identity = str(target["agent_id"]), str(target["id"])
            with (
                self._observer.context(agent_id=agent, operation_id=identity),
                self._observer.span("memory.organization") as measurement,
            ):
                try:
                    result = await self._db.write(lambda u: u.memory_organization_process(agent, identity))
                    if measurement is not None and result["state"] in {"invalidated", "failed"}:
                        measurement.result(Outcome.ERROR)
                    elif measurement is not None and result["state"] == "cancelled":
                        measurement.result(Outcome.CANCELLED)
                except BusinessError as exc:
                    # A rejected deterministic input is settled without repeating classification;
                    # infrastructure errors remain visible to the owning maintenance worker.
                    code = exc.code
                    await self._db.write(lambda u: u.memory_organization_fail(agent, identity, code))
                    if measurement is not None:
                        measurement.result(Outcome.ERROR)
            return True

        return await self._execute(perform)

    def health(self) -> Record:
        return {
            "closing": self._closing,
            "pending": len(self._pending),
            "model_calls_enabled": False,
            "budget_micro": 0,
        }

    def stop_accepting(self) -> None:
        self._closing = True

    async def close(self, timeout_s: float = 30) -> bool:
        self.stop_accepting()
        if self._pending:
            _, pending = await asyncio.wait(self._pending, timeout=max(0, timeout_s))
            return not pending
        return True
