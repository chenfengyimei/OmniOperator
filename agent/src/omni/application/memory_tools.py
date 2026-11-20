"""Memory capabilities share application transactions and verified source rules."""

from __future__ import annotations

from collections.abc import Mapping
from typing import cast

from ..domain.contracts import JSON, ActionResult, BusinessError, Effect, InvocationContext, Outcome
from ..domain.memory_decay import TOUCH_LIMIT
from .memory_semantics import MemorySemanticService
from .ports import DatabasePort, Record, UnitOfWork


class MemoryCapabilities:
    def __init__(self, database: DatabasePort, semantics: MemorySemanticService | None = None) -> None:
        self._db = database
        self._semantics = semantics

    async def invoke(
        self, name: str, arguments: Mapping[str, JSON], *, operation_id: str, context: InvocationContext
    ) -> ActionResult:
        try:
            return await self._invoke(name, arguments, context)
        except BusinessError as exc:
            if exc.code in {
                "invalid_memory",
                "document_erased",
                "document_index_invalid",
                "invalid_document_query",
                "invalid_evidence",
                "evidence_required",
                "memory_forgotten",
                "revision_conflict",
                "not_found",
                "person_deleted",
                "revision_required",
                "data_constraint",
                "memory_recall_stale",
                "memory_recall_budget",
            }:
                return ActionResult(Outcome.ERROR, exc.message, error_code=exc.code)
            raise

    async def _invoke(self, name: str, arguments: Mapping[str, JSON], context: InvocationContext) -> ActionResult:
        if name == "memory_search":
            if self._semantics is not None:
                result = await self._semantics.search(context.agent_id, str(arguments["query"]), run_id=context.run_id)
                rows = result["items"]
                identities = [str(row["id"]) for row in rows[:TOUCH_LIMIT]]
                if identities:
                    await self._db.write(lambda u: u.memory_touch(context.agent_id, identities))
                return ActionResult(
                    Outcome.OK,
                    "\n".join(f"{row['id']} revision={row['revision']}: {row['content']}" for row in rows)
                    or "No memories matched",
                    data=cast(dict[str, JSON], {**result, "entries": rows}),
                )

            def search(u: UnitOfWork) -> list[Record]:
                rows = u.search_memory(context.agent_id, str(arguments["query"]))
                u.capture_tool_memories(context.agent_id, context.run_id, [str(row["id"]) for row in rows])
                u.memory_touch(context.agent_id, [str(row["id"]) for row in rows[:TOUCH_LIMIT]])
                return rows

            rows = await self._db.write(search)
            return ActionResult(
                Outcome.OK,
                "\n".join(f"{row['id']} revision={row['revision']}: {row['content']}" for row in rows),
                data={
                    "entries": [
                        {"id": str(row["id"]), "revision": row["revision"], "content": str(row["content"])}
                        for row in rows
                    ]
                },
            )
        if name == "memory_remember":
            if not arguments.get("quote"):
                raise BusinessError(
                    "evidence_required", "Model-authored memory requires a source message and exact quote", status=422
                )
            run = await self._db.read(lambda u: u.get_run(context.agent_id, context.run_id))
            alternate = arguments.get("action_id") or arguments.get("document_revision_id")
            message_id = (
                str(arguments.get("message_id") or run["input_message_id"])
                if not alternate or arguments.get("message_id")
                else None
            )
            row = await self._db.write(
                lambda u: u.remember(
                    context.agent_id,
                    str(arguments["content"]),
                    message_id=message_id,
                    quote=str(arguments["quote"]),
                    model_authored=True,
                    action_id=str(arguments["action_id"]) if arguments.get("action_id") else None,
                    document_revision_id=str(arguments["document_revision_id"])
                    if arguments.get("document_revision_id")
                    else None,
                    source_run_id=context.run_id,
                    importance=cast(int, arguments.get("importance", 50)),
                )
            )
            return ActionResult(Outcome.OK, "Memory saved: " + str(row["id"]), Effect.APPLIED)
        if name == "memory_forget":
            revision = arguments.get("revision")
            if type(revision) is not int or revision < 1:
                raise BusinessError("revision_required", "Forgetting requires the observed memory revision", status=422)
            await self._db.write(lambda u: u.forget(context.agent_id, str(arguments["memory_id"]), revision))
            return ActionResult(Outcome.OK, "Memory forgotten", Effect.APPLIED)
        return ActionResult(Outcome.ERROR, "Unknown memory tool", error_code="tool_not_found")

    async def close(self, timeout_s: float) -> bool:
        return True
