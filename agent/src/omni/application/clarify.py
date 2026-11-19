"""Owner-facing clarification use cases; the model-side wait lives in the engine run coroutine."""

from __future__ import annotations

from collections.abc import Mapping
from typing import cast

from ..domain.contracts import JSON, ActionResult, BusinessError, InvocationContext, Outcome
from .ports import DatabasePort, Record


class ClarifyService:
    def __init__(self, database: DatabasePort) -> None:
        self._db = database

    async def pending(self, agent_id: str) -> list[Record]:
        return await self._db.read(lambda u: u.pending_clarifications(agent_id))

    async def get(self, agent_id: str, clarification_id: str) -> Record:
        return await self._db.read(lambda u: u.clarification(agent_id, clarification_id))

    async def respond(
        self, agent_id: str, clarification_id: str, answers: Mapping[str, object], revision: int, policy_revision: int
    ) -> Record:
        if not isinstance(answers, dict):
            raise BusinessError("invalid_clarify", "answers must be an object keyed by question id", status=422)
        return await self._db.write(
            lambda u: u.clarify_respond(
                agent_id, clarification_id, cast(dict[str, JSON], dict(answers)), revision, policy_revision
            )
        )

    async def cancel(self, agent_id: str, clarification_id: str, revision: int, notice: str = "") -> Record:
        return await self._db.write(lambda u: u.clarify_cancel(agent_id, clarification_id, revision, notice))

    async def invoke(
        self, name: str, arguments: Mapping[str, JSON], *, operation_id: str, context: InvocationContext
    ) -> ActionResult:
        return ActionResult(Outcome.ERROR, "clarify is dispatched by the engine run loop", error_code="tool_not_found")

    async def close(self, timeout_s: float) -> bool:
        return True
