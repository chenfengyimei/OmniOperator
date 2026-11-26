"""Persist scoped OmniWeave admissions, execution plans and Worker dependencies."""

from __future__ import annotations

import hashlib
import json
from collections.abc import Mapping
from dataclasses import asdict
from typing import Literal, cast

from sqlalchemy import select, update

from ...application.ports import Record, UnitOfWork
from ...domain.contracts import JSON, BusinessError
from ...domain.delegation import SUMMARY_CHARS
from ...domain.omniweave import OBSERVE_DECISION, OWNER_WEB_SOURCE, TRIGGER_VERSION, checked_plan
from . import schema as s
from .repository_base import RepositoryBase


class OmniWeaveRepositories(RepositoryBase):
    def omniweave_observe(
        self, agent_id: str, key: str, run_id: str, main_session_id: str, *, duplicate: bool, dispatch: bool = False
    ) -> None:
        private_session = select(s.run_delegations.c.id).where(s.run_delegations.c.child_session_id == main_session_id)
        channel_session = select(s.qq_bindings.c.id).where(s.qq_bindings.c.session_id == main_session_id)
        source = self.connection.execute(
            select(s.inbox.c.id, s.inbox.c.request_fingerprint)
            .join(s.runs, s.runs.c.id == s.inbox.c.run_id)
            .join(s.sessions, s.sessions.c.id == s.runs.c.session_id)
            .where(
                s.inbox.c.agent_id == agent_id,
                s.inbox.c.channel == "chat",
                s.inbox.c.key == key,
                s.inbox.c.run_id == run_id,
                s.runs.c.agent_id == agent_id,
                s.runs.c.session_id == main_session_id,
                s.runs.c.parent_id.is_(None),
                s.runs.c.execution_kind == "model",
                s.sessions.c.agent_id == agent_id,
                ~private_session.exists(),
                ~channel_session.exists(),
            )
        ).one_or_none()
        if source is None:
            raise BusinessError("not_found", "Main Web conversation does not exist in this Agent space", status=404)
        # A replay keeps the original decision, including requests accepted before observation was enabled.
        if duplicate:
            return

        owner_id = self.connection.execute(select(s.instances.c.owner_id).where(s.instances.c.slot == 1)).scalar_one()
        if dispatch:
            bound_session = self.connection.execute(
                select(s.owa_threads.c.main_session_id)
                .join(s.owa_tickets, s.owa_tickets.c.thread_id == s.owa_threads.c.id)
                .join(s.owa_executions, s.owa_executions.c.ticket_id == s.owa_tickets.c.id)
                .where(s.owa_threads.c.agent_id == agent_id, s.owa_threads.c.owner_id == owner_id)
                .order_by(s.owa_executions.c.created_at, s.owa_executions.c.id)
                .limit(1)
            ).scalar_one_or_none()
            if bound_session is not None and bound_session != main_session_id:
                return
        thread_id = self.connection.execute(
            select(s.owa_threads.c.id).where(
                s.owa_threads.c.agent_id == agent_id,
                s.owa_threads.c.main_session_id == main_session_id,
                s.owa_threads.c.owner_id == owner_id,
            )
        ).scalar_one_or_none()
        if thread_id is None:
            thread = self._record(s.owa_threads, {"main_session_id": main_session_id, "owner_id": owner_id}, agent_id)
            thread_id = thread["id"]
        ticket = self._record(s.owa_tickets, {"thread_id": thread_id, "run_id": run_id}, agent_id)
        self._record(
            s.owa_triggers,
            {
                "ticket_id": ticket["id"],
                "inbox_id": source.id,
                "source": OWNER_WEB_SOURCE,
                "version": TRIGGER_VERSION,
                "payload_sha256": source.request_fingerprint,
                "decision": OBSERVE_DECISION,
            },
            agent_id,
        )
        if dispatch:
            self._record(s.owa_executions, {"ticket_id": ticket["id"]}, agent_id)

    def omniweave_router(self, agent_id: str, run_id: str) -> Record | None:
        row = (
            self.connection.execute(
                select(s.owa_executions, s.owa_threads.c.main_session_id)
                .join(s.owa_tickets, s.owa_tickets.c.id == s.owa_executions.c.ticket_id)
                .join(s.owa_threads, s.owa_threads.c.id == s.owa_tickets.c.thread_id)
                .where(s.owa_tickets.c.agent_id == agent_id, s.owa_tickets.c.run_id == run_id)
            )
            .mappings()
            .first()
        )
        return cast(Record, dict(row)) if row else None

    def omniweave_plan(self, agent_id: str, run_id: str, arguments: Record, catalog: Mapping[str, str]) -> Record:
        unit = cast(UnitOfWork, self)
        unit.assert_run_authorized(agent_id, run_id)
        execution = self.omniweave_router(agent_id, run_id)
        run = unit.get_run(agent_id, run_id)
        if execution is None or execution["cancelled_at"] or run["state"] != "running":
            raise BusinessError("owa_router_not_active", "This run cannot schedule an OmniWeave Ticket")
        tasks = checked_plan(arguments, cast(int, unit.run_settings(agent_id, run_id)["call_reservation_micro"]))
        if any(set(task.tools) - catalog.keys() for task in tasks):
            raise BusinessError("owa_worker_tool_unavailable", "Worker tool is unavailable in the installed catalog")
        fingerprint = hashlib.sha256(
            json.dumps(
                [asdict(task) for task in tasks], sort_keys=True, ensure_ascii=False, separators=(",", ":")
            ).encode()
        ).hexdigest()
        ticket_id, session_id = str(execution["ticket_id"]), str(execution["main_session_id"])
        if execution["plan_sha256"] == fingerprint:
            return self.omniweave_ticket(agent_id, session_id, ticket_id)
        if execution["plan_sha256"] is not None:
            raise BusinessError("owa_plan_conflict", "This Ticket already has a different Worker plan", status=409)
        unit.require_turn_capacity(len(tasks))
        self.connection.execute(
            update(s.owa_executions).where(s.owa_executions.c.id == execution["id"]).values(plan_sha256=fingerprint)
        )
        records: dict[str, Record] = {}
        for task in tasks:
            child = unit.delegation_create(
                agent_id, run_id, task.request, worker_tools={name: catalog[name] for name in task.tools}
            )
            records[task.key] = self._record(
                s.owa_tasks, {"ticket_id": ticket_id, "run_id": child["run_id"], "task_key": task.key}, agent_id
            )
        for task in tasks:
            for predecessor in task.needs:
                self._record(
                    s.owa_dependencies,
                    {"task_id": records[task.key]["id"], "needs_id": records[predecessor]["id"]},
                    agent_id,
                )
        unit.event(agent_id, run_id, "omniweave.planned", {"ticket_id": ticket_id, "task_count": len(tasks)})
        return self.omniweave_ticket(agent_id, session_id, ticket_id)

    def omniweave_ticket(
        self, agent_id: str, session_id: str, ticket_id: str = "", *, include_results: bool = False, run_id: str = ""
    ) -> Record:
        query = (
            select(s.owa_tickets, s.owa_executions.c.plan_sha256, s.owa_executions.c.cancelled_at)
            .join(s.owa_threads, s.owa_threads.c.id == s.owa_tickets.c.thread_id)
            .join(s.sessions, s.sessions.c.id == s.owa_threads.c.main_session_id)
            .outerjoin(s.owa_executions, s.owa_executions.c.ticket_id == s.owa_tickets.c.id)
            .where(
                s.owa_tickets.c.agent_id == agent_id,
                s.owa_threads.c.main_session_id == session_id,
                s.sessions.c.deleted_at.is_(None),
            )
        )
        if ticket_id:
            query = query.where(s.owa_tickets.c.id == ticket_id)
        elif run_id:
            query = query.where(s.owa_tickets.c.run_id == run_id)
        else:
            query = query.where(s.owa_executions.c.plan_sha256.is_not(None)).order_by(
                s.owa_tickets.c.created_at.desc(), s.owa_tickets.c.id.desc()
            )
        ticket = self.connection.execute(query.limit(1)).mappings().first()
        if not ticket:
            raise BusinessError("not_found", "Ticket does not exist in this main conversation", status=404)

        root_id = str(ticket["run_id"])
        unit = cast(UnitOfWork, self)
        rows = self.connection.execute(
            select(s.owa_tasks.c.task_key, s.owa_tasks.c.run_id)
            .where(s.owa_tasks.c.ticket_id == ticket["id"])
            .order_by(s.owa_tasks.c.task_key)
        ).all()
        tasks: list[JSON] = []
        summary_limit = SUMMARY_CHARS // max(1, len(rows))
        for key, child_id in rows:
            result = unit.delegation_result(agent_id, root_id, child_id)
            item: Record = {
                "key": key,
                "state": result["state"],
                "error_code": result["error_code"],
                "unknown_effect": result["unknown_effect"],
                "unknown_model_calls": result["unknown_model_calls"],
            }
            if include_results:
                summary = str(result["summary"])
                item["result"] = summary[:summary_limit]
                item["result_truncated"] = len(summary) > summary_limit or bool(result["summary_truncated"])
            tasks.append(item)
        return {
            "ticket_id": ticket["id"],
            "run_id": root_id,
            "plan_submitted": ticket["plan_sha256"] is not None,
            "cancel_requested": ticket["cancelled_at"] is not None,
            "tasks": tasks,
        }

    def omniweave_private_scope(self, agent_id: str, field: str, identity: str) -> bool:
        statement = select(s.owa_tasks.c.id).where(s.owa_tasks.c.agent_id == agent_id)
        if field == "run_id":
            statement = statement.where(s.owa_tasks.c.run_id == identity)
        else:
            statement = statement.join(s.runs, s.runs.c.id == s.owa_tasks.c.run_id)
            if field == "session_id":
                statement = statement.where(s.runs.c.session_id == identity)
            else:
                source = {"message_id": s.messages, "summary_id": s.summaries}[field]
                statement = statement.join(source, source.c.session_id == s.runs.c.session_id).where(
                    source.c.id == identity, source.c.agent_id == agent_id
                )
        return self.connection.execute(statement.limit(1)).first() is not None

    def omniweave_worker_gate(self, agent_id: str, run_id: str) -> Literal["legacy", "waiting", "blocked", "ready"]:
        task = (
            self.connection.execute(
                select(s.owa_tasks.c.id, s.owa_tickets.c.run_id, s.owa_executions.c.cancelled_at)
                .join(s.owa_tickets, s.owa_tickets.c.id == s.owa_tasks.c.ticket_id)
                .join(s.owa_executions, s.owa_executions.c.ticket_id == s.owa_tasks.c.ticket_id)
                .where(s.owa_tasks.c.agent_id == agent_id, s.owa_tasks.c.run_id == run_id)
            )
            .mappings()
            .first()
        )
        if task is None:
            return "legacy"
        if task["cancelled_at"] is not None:
            return "blocked"
        predecessor = s.owa_tasks.alias()
        needs = self.connection.execute(
            select(predecessor.c.run_id, s.runs.c.state)
            .select_from(s.owa_dependencies)
            .join(predecessor, predecessor.c.id == s.owa_dependencies.c.needs_id)
            .join(s.runs, s.runs.c.id == predecessor.c.run_id)
            .where(s.owa_dependencies.c.task_id == task["id"])
        ).all()
        unit = cast(UnitOfWork, self)
        if any(state in {"failed", "cancelled", "interrupted"} for _, state in needs):
            return "blocked"
        if any(state != "succeeded" for _, state in needs):
            return "waiting"
        for child_id, _state in needs:
            result = unit.delegation_result(agent_id, str(task["run_id"]), child_id)
            if result["unknown_effect"] or result["unknown_model_calls"]:
                return "blocked"
        return "ready"

    def omniweave_cancel(self, agent_id: str, run_id: str) -> None:
        self.connection.execute(
            update(s.owa_executions)
            .where(
                s.owa_executions.c.agent_id == agent_id,
                s.owa_executions.c.ticket_id.in_(select(s.owa_tickets.c.id).where(s.owa_tickets.c.run_id == run_id)),
                s.owa_executions.c.cancelled_at.is_(None),
            )
            .values(cancelled_at=self.clock.now())
        )
