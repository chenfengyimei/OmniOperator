"""Session task snapshots in the shared transaction; never dispatches or approves work."""

from __future__ import annotations

from typing import cast

from sqlalchemy import select, update

from ...application.ports import Record, UnitOfWork
from ...domain.contracts import JSON, BusinessError
from ...domain.todos import TODO_LIST, invalid, revision, update_items
from . import schema as s
from .retention_repository import RetentionRepositories


class TodoRepositories(RetentionRepositories):
    def todo_get(self, agent_id: str, session_id: str) -> Record:
        session = self._one(s.sessions, agent_id, session_id)
        if session["deleted_at"]:
            raise BusinessError("not_found", "Session no longer exists", status=404)
        row = (
            self.connection.execute(
                select(s.session_todos).where(
                    s.session_todos.c.agent_id == agent_id, s.session_todos.c.session_id == session_id
                )
            )
            .mappings()
            .first()
        )
        if row is None:
            return {"session_id": session_id, "revision": 0, "todos": []}
        if row["session_epoch"] != session["content_epoch"]:
            raise BusinessError("context_invalidated", "Task plan source epoch changed")
        items = update_items([], row["items"])
        return {"session_id": session_id, "revision": row["revision"], "todos": cast(list[JSON], items)}

    def todo_for_run(self, agent_id: str, run_id: str) -> Record:
        enabled = self.connection.execute(
            select(s.agents.c.enabled).where(s.agents.c.id == agent_id)
        ).scalar_one_or_none()
        if not enabled:
            raise BusinessError("agent_disabled", "Agent is disabled")
        self.assert_context(agent_id, run_id)
        self.assert_model_authorized(agent_id, run_id)
        run = self._one(s.runs, agent_id, run_id)
        if run["state"] != "running" or run.get("execution_kind") == "owner":
            raise BusinessError("run_not_running", "Task planning requires an active model run")
        unit = cast(UnitOfWork, self)
        settings = unit.settings(agent_id)
        if (
            run["policy_epoch"] != settings["policy_revision"]
            or TODO_LIST.name in cast(list[JSON], cast(Record, settings["value"])["disabled_tools"])
            or unit.qq_run(agent_id, run_id)
            or unit.run_route(agent_id, run_id)["mode"] != "agent"
        ):
            raise BusinessError("todo_not_available", "Task planning is not available under this run's policy")
        return self.todo_get(agent_id, str(run["session_id"]))

    def todo_update(self, agent_id: str, run_id: str, request: Record) -> Record:
        current = self.todo_for_run(agent_id, run_id)
        if "todos" not in request or set(request) - {"todos", "revision", "merge"}:
            raise invalid()
        expected = revision(request.get("revision"))
        if current["revision"] != expected:
            raise BusinessError("revision_conflict", "Read the current task plan before updating")
        items = update_items(
            cast(list[dict[str, str]], current["todos"]),
            request["todos"],
            merge=cast(bool, request.get("merge", False)),
        )
        if items == current["todos"]:
            return current
        session_id = str(current["session_id"])
        session = self._one(s.sessions, agent_id, session_id)
        values = {"items": items, "revision": expected + 1, "updated_at": self.clock.now()}
        if expected:
            self.connection.execute(
                update(s.session_todos)
                .where(s.session_todos.c.agent_id == agent_id, s.session_todos.c.session_id == session_id)
                .values(**values)
            )
        else:
            self._record(
                s.session_todos,
                {**values, "session_id": session_id, "session_epoch": session["content_epoch"]},
                agent_id,
            )
        self.audit(agent_id, "todo.updated", run_id, {"revision": expected + 1, "count": len(items)})
        cast(UnitOfWork, self).event(agent_id, run_id, "todo.updated", {"revision": expected + 1, "count": len(items)})
        return {"session_id": session_id, "revision": expected + 1, "todos": cast(list[JSON], items)}
