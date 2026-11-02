"""Memory capabilities share application transactions and verified source rules."""

from __future__ import annotations

from collections.abc import Mapping

from ..domain.contracts import JSON, ActionResult, BusinessError, Effect, InvocationContext, Outcome
from .ports import DatabasePort


class MemoryCapabilities:
    def __init__(self, database: DatabasePort) -> None:
        self._db = database

    async def invoke(
        self, name: str, arguments: Mapping[str, JSON], *, operation_id: str, context: InvocationContext
    ) -> ActionResult:
        if name == "memory_search":
            rows = await self._db.read(lambda u: u.search_memory(context.agent_id, str(arguments["query"])))
            return ActionResult(
                Outcome.OK,
                "\n".join(f"{row['id']}: {row['content']}" for row in rows),
                data={"entries": [{"id": str(row["id"]), "content": str(row["content"])} for row in rows]},
            )
        if name == "memory_remember":
            if not arguments.get("message_id") or not arguments.get("quote"):
                raise BusinessError(
                    "evidence_required", "Model-authored memory requires a source message and exact quote", status=422
                )
            row = await self._db.write(
                lambda u: u.remember(
                    context.agent_id,
                    str(arguments["content"]),
                    message_id=str(arguments["message_id"]),
                    quote=str(arguments["quote"]),
                )
            )
            return ActionResult(Outcome.OK, "Memory saved: " + str(row["id"]), Effect.APPLIED)
        if name == "memory_forget":
            await self._db.write(lambda u: u.forget(context.agent_id, str(arguments["memory_id"])))
            return ActionResult(Outcome.OK, "Memory forgotten", Effect.APPLIED)
        return ActionResult(Outcome.ERROR, "Unknown memory tool", error_code="tool_not_found")

    async def close(self, timeout_s: float) -> bool:
        return True
