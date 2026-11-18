"""Model task-plan control with scoped transactions; creates no external action or background task."""

from __future__ import annotations

import json
from collections.abc import Callable, Mapping
from typing import cast

from jsonschema import Draft202012Validator

from ..domain.contracts import JSON, ActionResult, BusinessError, InvocationContext, Outcome
from ..domain.todos import TODO_LIST, notice
from .ports import DatabasePort, Record, UnitOfWork


class TodoService:
    def __init__(self, database: DatabasePort) -> None:
        self._db = database

    async def notice(self, agent_id: str, run_id: str) -> str:
        result = await self._db.read(lambda u: u.todo_for_run(agent_id, run_id))
        return notice(cast(int, result["revision"]), cast(list[dict[str, str]], result["todos"]))

    async def invoke(
        self, name: str, arguments: Mapping[str, JSON], *, operation_id: str, context: InvocationContext
    ) -> ActionResult:
        if name != TODO_LIST.name:
            return ActionResult(Outcome.ERROR, "Unknown task-plan control", error_code="tool_not_found")
        validator = Draft202012Validator(TODO_LIST.parameters)
        valid = cast(Callable[[object], bool], getattr(validator, "is_valid"))
        if not valid(arguments) or ("todos" not in arguments and arguments):
            return ActionResult(Outcome.ERROR, "Invalid task-plan arguments", error_code="invalid_todos")

        def operate(u: UnitOfWork) -> Record:
            current = u.todo_for_run(context.agent_id, context.run_id)
            if current["session_id"] != context.session_id:
                raise BusinessError("todo_scope_changed", "Task plan belongs to a different session")
            return u.todo_update(context.agent_id, context.run_id, dict(arguments)) if "todos" in arguments else current

        try:
            result = await self._db.write(operate) if "todos" in arguments else await self._db.read(operate)
            return ActionResult(Outcome.OK, json.dumps(result, ensure_ascii=False), data=result)
        except BusinessError as exc:
            if exc.code in {"invalid_todos", "todo_revision_required", "revision_conflict"}:
                return ActionResult(Outcome.ERROR, exc.message, error_code=exc.code)
            raise

    async def close(self, timeout_s: float = 30) -> bool:
        return True
