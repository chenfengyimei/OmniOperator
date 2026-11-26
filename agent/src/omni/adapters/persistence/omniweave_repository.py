"""Persist scoped OmniWeave admissions, execution plans and Worker dependencies."""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from dataclasses import asdict
from datetime import datetime, timedelta
from typing import Literal, cast

from sqlalchemy import select, update

from ...application.ports import Record, UnitOfWork
from ...domain.contracts import JSON, BusinessError, ToolSpec
from ...domain.delegation import SUMMARY_CHARS, TREE_BUDGET, tool_hash
from ...domain.omniweave import (
    OBSERVE_DECISION,
    OWNER_WEB_SOURCE,
    ROOT_CALL_LIMIT,
    ROOT_DEADLINE_S,
    TRIGGER_VERSION,
    WORKER_PROFILES,
    checked_plan,
    goal_fingerprint,
    mandate_tool,
)
from . import schema as s
from .omniweave_authority import assert_mandate_action, mandate
from .repository_base import RepositoryBase


class OmniWeaveRepositories(RepositoryBase):
    def omniweave_assert_model_tools(self, agent_id: str, run_id: str, tools: Sequence[ToolSpec]) -> None:
        captured = mandate(self, agent_id, run_id)
        if captured is None or captured["task_id"] is None:
            return
        grants = cast(Record, captured["tools"])
        if any(grants.get(spec.name) != mandate_tool(spec) for spec in tools):
            raise BusinessError("owa_mandate_tool_denied", "The Worker model tool surface exceeds its Mandate")

    def omniweave_assert_action(self, agent_id: str, run_id: str, spec: ToolSpec) -> None:
        assert_mandate_action(self, agent_id, run_id, spec)

    def _owa_owner(self) -> str:
        return cast(
            str, self.connection.execute(select(s.instances.c.owner_id).where(s.instances.c.slot == 1)).scalar_one()
        )

    def _owa_thread(self, agent_id: str, owner_id: str, session_id: str) -> str:
        thread_id = self.connection.execute(
            select(s.owa_threads.c.id).where(
                s.owa_threads.c.agent_id == agent_id,
                s.owa_threads.c.main_session_id == session_id,
                s.owa_threads.c.owner_id == owner_id,
            )
        ).scalar_one_or_none()
        if thread_id is None:
            return str(
                self._record(s.owa_threads, {"main_session_id": session_id, "owner_id": owner_id}, agent_id)["id"]
            )
        return cast(str, thread_id)

    def omniweave_main_session(self, agent_id: str) -> str | None:
        if not self.connection.execute(select(s.agents.c.id).where(s.agents.c.id == agent_id)).first():
            raise BusinessError("not_found", "Agent space does not exist", status=404)
        return cast(
            str | None,
            self.connection.execute(
                select(s.owa_threads.c.main_session_id)
                .join(s.sessions, s.sessions.c.id == s.owa_threads.c.main_session_id)
                .join(s.instances, (s.instances.c.slot == 1) & (s.instances.c.owner_id == s.owa_threads.c.owner_id))
                .where(
                    s.owa_threads.c.agent_id == agent_id,
                    s.owa_threads.c.main_selected_at.is_not(None),
                    s.sessions.c.deleted_at.is_(None),
                )
            ).scalar_one_or_none(),
        )

    def omniweave_select_main(self, agent_id: str, session_id: str | None) -> str:
        current = self.omniweave_main_session(agent_id)
        if session_id is not None and session_id != current:
            eligible = self.connection.execute(
                select(s.sessions.c.id)
                .join(s.runs, s.runs.c.session_id == s.sessions.c.id)
                .join(s.inbox, s.inbox.c.run_id == s.runs.c.id)
                .where(
                    s.sessions.c.agent_id == agent_id,
                    s.sessions.c.id == session_id,
                    s.sessions.c.deleted_at.is_(None),
                    s.runs.c.parent_id.is_(None),
                    s.inbox.c.agent_id == agent_id,
                    s.inbox.c.channel == "chat",
                    ~select(s.qq_bindings.c.id).where(s.qq_bindings.c.session_id == session_id).exists(),
                    ~select(s.run_delegations.c.id).where(s.run_delegations.c.child_session_id == session_id).exists(),
                )
                .limit(1)
            ).first()
            if eligible is None:
                raise BusinessError(
                    "not_found", "Private Web conversation does not exist in this Agent space", status=404
                )
        if current is not None:
            if session_id is None or session_id == current:
                return current
            raise BusinessError(
                "owa_main_session_exists", "This Agent already has an active main conversation", status=409
            )
        if session_id is None:
            session_id = str(
                self._record(s.sessions, {"title": "OmniWeave", "revision": 1, "next_seq": 0}, agent_id)["id"]
            )
        thread_id = self._owa_thread(agent_id, self._owa_owner(), session_id)
        self.connection.execute(
            update(s.owa_threads)
            .where(s.owa_threads.c.agent_id == agent_id, s.owa_threads.c.main_selected_at.is_not(None))
            .values(main_selected_at=None)
        )
        self.connection.execute(
            update(s.owa_threads).where(s.owa_threads.c.id == thread_id).values(main_selected_at=self.clock.now())
        )
        self.audit(agent_id, "omniweave.main_session_selected", None, {"session_id": session_id})
        return session_id

    def omniweave_observe(
        self,
        agent_id: str,
        key: str,
        run_id: str,
        main_session_id: str,
        *,
        duplicate: bool,
        dispatch: bool = False,
        catalog: Mapping[str, ToolSpec] | None = None,
    ) -> None:
        # Replays keep their original routing. Only explicit selections enable new Router turns.
        if duplicate or (dispatch and self.omniweave_main_session(agent_id) != main_session_id):
            return
        private_session = select(s.run_delegations.c.id).where(s.run_delegations.c.child_session_id == main_session_id)
        channel_session = select(s.qq_bindings.c.id).where(s.qq_bindings.c.session_id == main_session_id)
        source = self.connection.execute(
            select(s.inbox.c.id, s.inbox.c.request_fingerprint, s.runs.c.policy_epoch, s.sessions.c.content_epoch)
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
        owner_id = self._owa_owner()
        thread_id = self._owa_thread(agent_id, owner_id, main_session_id)
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
        if catalog is not None:
            unit = cast(UnitOfWork, self)
            current = cast(Record, unit.settings(agent_id)["value"])
            disabled = set(cast(list[str], current["disabled_tools"]))
            self._record(
                s.owa_mandates,
                {
                    "ticket_id": ticket["id"],
                    "run_id": run_id,
                    "main_session_id": main_session_id,
                    "owner_id": owner_id,
                    "profile": "router",
                    "profile_version": 1,
                    "goal_version": 1,
                    "goal_sha256": source.request_fingerprint,
                    "policy_epoch": source.policy_epoch,
                    "history_epoch": self.connection.execute(select(s.history_identity.c.epoch)).scalar_one(),
                    "session_epoch": source.content_epoch,
                    "expires_at": (
                        datetime.fromisoformat(self.clock.now()) + timedelta(seconds=ROOT_DEADLINE_S)
                    ).isoformat(timespec="microseconds"),
                    "tools": {name: mandate_tool(spec) for name, spec in catalog.items() if name not in disabled},
                    "budget_micro": min(
                        TREE_BUDGET, cast(int, unit.run_settings(agent_id, run_id)["daily_budget_micro"]) or TREE_BUDGET
                    ),
                    "max_calls": ROOT_CALL_LIMIT,
                },
                agent_id,
            )
        if dispatch:
            self._record(s.owa_executions, {"ticket_id": ticket["id"], "mandate_required": True}, agent_id)

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

    def omniweave_plan(self, agent_id: str, run_id: str, arguments: Record, catalog: Mapping[str, ToolSpec]) -> Record:
        unit = cast(UnitOfWork, self)
        unit.assert_run_authorized(agent_id, run_id)
        execution = self.omniweave_router(agent_id, run_id)
        run = unit.get_run(agent_id, run_id)
        if execution is None or execution["cancelled_at"] or run["state"] != "running":
            raise BusinessError("owa_router_not_active", "This run cannot schedule an OmniWeave Ticket")
        tasks = checked_plan(arguments, cast(int, unit.run_settings(agent_id, run_id)["call_reservation_micro"]))
        fingerprint = goal_fingerprint(cast(list[JSON], [asdict(task) for task in tasks]))
        ticket_id, session_id = str(execution["ticket_id"]), str(execution["main_session_id"])
        if execution["plan_sha256"] == fingerprint:
            return self.omniweave_ticket(agent_id, session_id, ticket_id)
        if execution["plan_sha256"] is not None:
            raise BusinessError("owa_plan_conflict", "This Ticket already has a different Worker plan", status=409)
        root = mandate(self, agent_id, run_id)
        if root is None:
            raise BusinessError("owa_mandate_missing", "Submit a new owner turn to authorize a Worker plan")
        grants = cast(Record, root["tools"])
        if any(
            name not in catalog or grants.get(name) != mandate_tool(catalog[name])
            for task in tasks
            for name in task.tools
        ):
            raise BusinessError(
                "owa_worker_tool_not_authorized",
                "Worker tools must be allowed by the current Agent policy and root Mandate",
            )
        if any(cast(int, task.request["budget_micro"]) > cast(int, root["budget_micro"]) for task in tasks):
            raise BusinessError("owa_mandate_budget_denied", "Worker budget exceeds the root Mandate")
        unit.require_turn_capacity(len(tasks))
        self.connection.execute(
            update(s.owa_executions).where(s.owa_executions.c.id == execution["id"]).values(plan_sha256=fingerprint)
        )
        records: dict[str, Record] = {}
        now = datetime.fromisoformat(self.clock.now())
        for task in tasks:
            profile = WORKER_PROFILES[task.profile]
            child = unit.delegation_create(
                agent_id,
                run_id,
                task.request,
                worker_tools={name: tool_hash(catalog[name]) for name in task.tools},
                worker_profile=profile,
            )
            records[task.key] = self._record(
                s.owa_tasks, {"ticket_id": ticket_id, "run_id": child["run_id"], "task_key": task.key}, agent_id
            )
            deadline = str(root["expires_at"])
            if task.deadline_seconds is not None:
                deadline = min(
                    deadline, (now + timedelta(seconds=task.deadline_seconds)).isoformat(timespec="microseconds")
                )
            if deadline <= self.clock.now():
                raise BusinessError("owa_mandate_expired", "OmniWeave task authorization expired before admission")
            self._record(
                s.owa_mandates,
                {
                    **{
                        name: root[name]
                        for name in (
                            "main_session_id",
                            "owner_id",
                            "policy_epoch",
                            "history_epoch",
                            "session_epoch",
                            "goal_version",
                        )
                    },
                    "ticket_id": ticket_id,
                    "task_id": records[task.key]["id"],
                    "parent_id": root["id"],
                    "run_id": child["run_id"],
                    "profile": profile.name,
                    "profile_version": profile.version,
                    "goal_sha256": goal_fingerprint(task.request["goal"]),
                    "expires_at": deadline,
                    "tools": {name: grants[name] for name in task.tools},
                    "budget_micro": task.request["budget_micro"],
                    "max_calls": task.request["max_calls"],
                },
                agent_id,
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
            if captured := mandate(self, agent_id, child_id):
                item.update({name: captured[name] for name in ("profile", "profile_version", "goal_version")})
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
