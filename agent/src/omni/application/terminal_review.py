"""Owner terminal evidence use cases; no native actions, receipt changes or historical PID operations."""

from __future__ import annotations

import asyncio
import logging
from collections.abc import Awaitable, Callable
from typing import Any, cast

from ..domain.contracts import BusinessError
from ..domain.terminal_review import parse_review
from ..domain.terminals import identity
from .ports import DatabasePort, Observer, Record

log = logging.getLogger("omni.v2.terminal.review")


class TerminalReviewService:
    def __init__(
        self,
        database: DatabasePort,
        observer: Observer,
        *,
        native_quiescent: Callable[[], bool],
        while_quiescent: Callable[[Callable[[], Awaitable[Record]]], Awaitable[Record]],
    ) -> None:
        self._db, self._observer = database, observer
        self._quiescent, self._guard = native_quiescent, while_quiescent
        self._pending: set[asyncio.Task[Any]] = set()
        self._closing = False

    def _status(self, row: Record) -> Record:
        available = self._quiescent() and not self._closing
        reasons = row["reasons"]
        if not isinstance(reasons, list) or not all(isinstance(item, str) for item in reasons):
            raise BusinessError(
                "terminal_review_source_invalid", "Terminal review source has an invalid status", status=503
            )
        return {
            **row,
            "reviewable": row["reviewable"] is True and available,
            "native_quiescent": available,
            "reasons": [*reasons, *([] if available else ["terminal_native_work_active"])],
        }

    async def page(self, agent_id: str, after: str = "", limit: int = 20) -> Record:
        rows = await self._db.read(lambda u: u.terminal_review_page(agent_id, after, limit))
        last_operation = rows[-1]["operation"] if rows else None
        if rows and not isinstance(last_operation, dict):
            raise BusinessError(
                "terminal_review_source_invalid", "Terminal review source has an invalid operation", status=503
            )
        after_id = identity(last_operation["id"]) if isinstance(last_operation, dict) and len(rows) == limit else None
        return {
            "items": [self._status(row) for row in rows],
            "next_after": after_id,
        }

    async def get(self, agent_id: str, operation_id: str) -> Record:
        return self._status(await self._db.read(lambda u: u.terminal_review_detail(agent_id, operation_id)))

    async def history(self, agent_id: str, operation_id: str, after: int = 0, limit: int = 20) -> Record:
        rows = await self._db.read(lambda u: u.terminal_review_history(agent_id, operation_id, after, limit))
        return {"items": list(rows), "next_after": rows[-1]["revision"] if len(rows) == limit else None}

    async def _owned(self, operation: Callable[[], Awaitable[Record]]) -> Record:
        if self._closing or len(self._pending) >= 4:
            raise BusinessError("terminal_review_busy", "Terminal review admission is closed or full", status=503)

        async def perform() -> Record:
            return await operation()

        task = asyncio.create_task(perform(), name="terminal-owner-review")
        self._pending.add(task)

        def done(future: asyncio.Task[Any]) -> None:
            self._pending.discard(future)
            if not future.cancelled():
                future.exception()

        task.add_done_callback(done)
        return await asyncio.shield(task)

    async def record(self, agent_id: str, operation_id: str, data: object) -> Record:
        operation_id, request = identity(operation_id), cast(Record, parse_review(data))

        async def perform():
            prior = await self._db.read(lambda u: u.terminal_review_replay(agent_id, operation_id, request))
            if prior is not None:
                return prior
            with self._observer.context(agent_id=agent_id, operation_id=str(request["review_id"])):
                row = await self._guard(
                    lambda: self._db.write(lambda u: u.terminal_review_commit(agent_id, operation_id, request))
                )
                log.info(
                    "Owner terminal observation settled",
                    extra={"event": "terminal.review.replayed" if row.get("replayed") else "terminal.review.recorded"},
                )
                return row

        return await self._owned(perform)

    async def erase_evidence(self, agent_id: str, operation_id: str, review_id: str) -> Record:
        operation_id, review_id = identity(operation_id), identity(review_id)

        async def perform():
            with self._observer.context(agent_id=agent_id, operation_id=review_id):
                row = await self._db.write(
                    lambda u: u.terminal_review_erase_evidence(agent_id, operation_id, review_id)
                )
                log.info("Owner terminal review evidence erased", extra={"event": "terminal.review.evidence_erased"})
                return row

        return await self._owned(perform)

    def stop_accepting(self) -> None:
        self._closing = True

    def health(self) -> Record:
        return {"closing": self._closing, "pending": len(self._pending)}

    async def close(self, timeout_s: float = 30) -> bool:
        self.stop_accepting()
        if self._pending:
            _, pending = await asyncio.wait(self._pending, timeout=max(0, timeout_s))
            return not pending
        return True
