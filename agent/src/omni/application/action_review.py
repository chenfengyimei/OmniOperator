"""Owner reconciliation records observations, never dispatches an action or rewrites its result."""

from __future__ import annotations

import asyncio
from collections.abc import Awaitable, Callable
from typing import TypeVar

from ..domain.action_review import parse_review
from ..domain.contracts import JSON, BusinessError
from .ports import DatabasePort, Record

T = TypeVar("T")


class ActionReviewService:
    def __init__(
        self, database: DatabasePort, blockers: Callable[[Record], list[str]], notify: Callable[[], None]
    ) -> None:
        self._db, self._blockers, self._notify = database, blockers, notify
        self._pending: set[asyncio.Task] = set()
        self._slots = asyncio.Semaphore(2)
        self._closing = False

    async def _execute(self, operation: Callable[[], Awaitable[T]]) -> T:
        if self._closing:
            raise BusinessError("action_review_closing", "Action review is draining", status=503)
        if len(self._pending) >= 16:
            raise BusinessError("action_review_busy", "Action review requests are bounded", status=503, retryable=True)

        async def perform():
            async with self._slots:
                return await operation()

        task = asyncio.create_task(perform(), name="action-review-operation")
        self._pending.add(task)

        def complete(future):
            self._pending.discard(future)
            if not future.cancelled():
                future.exception()

        task.add_done_callback(complete)
        return await asyncio.shield(task)

    def _public(self, record: Record) -> Record:
        protected = record["protected_reasons"]
        if not isinstance(protected, list) or any(not isinstance(reason, str) for reason in protected):
            raise BusinessError("action_review_contract", "Action review protections are unavailable", status=503)
        reasons: list[JSON] = list(protected)
        reasons.extend(reason for reason in self._blockers(record) if reason not in reasons)
        return {**record, "protected_reasons": reasons, "eligible": bool(record.get("eligible")) and not reasons}

    async def page(self, agent_id: str, after: int = 0, limit: int = 20) -> Record:
        async def perform():
            rows = await self._db.write(lambda u: u.action_review_page(agent_id, after, limit))
            return {
                "items": [self._public(row) for row in rows],
                "next_after": rows[-1]["sequence"] if len(rows) == limit else None,
            }

        return await self._execute(perform)

    async def get(self, agent_id: str, action_id: str) -> Record:
        async def perform():
            return self._public(await self._db.write(lambda u: u.action_review_get(agent_id, action_id)))

        return await self._execute(perform)

    async def history(self, agent_id: str, action_id: str, after: int = 0, limit: int = 20) -> Record:
        async def perform():
            rows = await self._db.read(lambda u: u.action_review_history(agent_id, action_id, after, limit))
            return {"items": rows, "next_after": rows[-1]["sequence"] if len(rows) == limit else None}

        return await self._execute(perform)

    async def review(self, agent_id: str, action_id: str, request: Record) -> Record:
        checked = parse_review(request)

        async def perform():
            replayed = await self._db.write(lambda u: u.action_review_replay(agent_id, action_id, checked))
            if replayed is not None:
                return self._public(replayed)
            record = await self._db.write(lambda u: u.action_review_get(agent_id, action_id))
            blockers = self._blockers(record)
            if blockers:
                raise BusinessError(blockers[0], "Action execution ownership has not been reclaimed")
            result = await self._db.write(lambda u: u.action_review_commit(agent_id, action_id, checked))
            self._notify()
            return self._public(result)

        return await self._execute(perform)

    def health(self) -> Record:
        return {"closing": self._closing, "pending": len(self._pending), "max_pending": 16}

    def stop_accepting(self) -> None:
        self._closing = True

    async def close(self, timeout_s: float = 30) -> bool:
        self.stop_accepting()
        if self._pending:
            _, pending = await asyncio.wait(self._pending, timeout=max(0, timeout_s))
            return not pending
        return True
