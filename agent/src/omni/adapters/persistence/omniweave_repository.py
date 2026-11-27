"""Persist scoped OmniWeave admissions, execution plans and Worker dependencies."""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from dataclasses import asdict
from datetime import datetime, timedelta
from typing import Literal, cast

from sqlalchemy import func, select, update

from ...application.ports import Record, TurnCancellation, UnitOfWork
from ...domain.contracts import JSON, BusinessError, ToolSpec
from ...domain.delegation import SUMMARY_CHARS, TREE_BUDGET, tool_hash
from ...domain.omniweave import (
    MAX_GOAL_VERSION,
    OBSERVE_DECISION,
    OWNER_WEB_SOURCE,
    REPLY_PEER,
    REQUEST_PEER,
    ROOT_CALL_LIMIT,
    ROOT_DEADLINE_S,
    TRIGGER_VERSION,
    WORKER_PROFILES,
    GoalUpdate,
    WorkerTask,
    checked_plan,
    goal_fingerprint,
    mandate_tool,
)
from ...domain.omniweave_artifacts import PUBLISH_ARTIFACT, READ_SHARED, SUBSCRIBE_ARTIFACTS
from . import schema as s
from .omniweave_attempts import latest_run_id
from .omniweave_authority import assert_mandate_action, mandate
from .omniweave_delivery import OmniWeaveDeliveryRepositories
from .omniweave_goals import current_goal, prior_goal_blocker
from .omniweave_mailbox import waiting_for_peer


class OmniWeaveRepositories(OmniWeaveDeliveryRepositories):
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
        goal_update: GoalUpdate | None = None,
    ) -> TurnCancellation | None:
        # Replays keep their original routing. Only explicit selections enable new Router turns.
        if duplicate:
            return None
        selected = dispatch and self.omniweave_main_session(agent_id) == main_session_id
        if goal_update is not None and not selected:
            raise BusinessError("not_found", "Goal updates belong to the selected main conversation", status=404)
        if dispatch and not selected:
            return None
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
        unit = cast(UnitOfWork, self)
        goal: Record = {}
        cancellation = None
        goal_version, goal_sha256 = 1, source.request_fingerprint
        if goal_update is not None:
            prior = self._owa_goal(agent_id, thread_id, goal_update)
            prior_mandate = mandate(self, agent_id, str(prior["run_id"]))
            goal_version = goal_update.expected_goal_version + 1
            goal_sha256 = goal_fingerprint(
                [prior_mandate["goal_sha256"] if prior_mandate else prior["payload_sha256"], source.request_fingerprint]
            )
            goal = {
                "root_ticket_id": goal_update.ticket_id,
                "goal_version": goal_version,
            }
        else:
            prior = None
        ticket = self._record(s.owa_tickets, {"thread_id": thread_id, "run_id": run_id, **goal}, agent_id)
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
        if prior is not None and goal_update is not None:
            prior_run = str(prior["run_id"])
            runs = [prior_run, *unit.delegation_children(agent_id, prior_run)]
            unit.cancel(agent_id, prior_run)
            outbound = [delivery for identity in runs for delivery in unit.delivery_cancel_run(agent_id, identity)]
            cancellation = (outbound, runs)
            unit.event(
                agent_id,
                run_id,
                "omniweave.goal_updated",
                {"ticket_id": goal_update.ticket_id, "goal_version": goal_version},
            )
        if catalog is not None:
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
                    "goal_version": goal_version,
                    "goal_sha256": goal_sha256,
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
        return cancellation

    def _owa_goal(self, agent_id: str, thread_id: str, update: GoalUpdate) -> Record:
        tickets, root = s.owa_tickets, s.owa_tickets.alias("goal_origin")
        prior = (
            self.connection.execute(
                select(
                    tickets.c.run_id,
                    tickets.c.goal_version,
                    s.owa_executions.c.cancelled_at,
                    s.owa_triggers.c.payload_sha256,
                )
                .join(s.owa_executions, s.owa_executions.c.ticket_id == tickets.c.id)
                .join(s.owa_triggers, s.owa_triggers.c.ticket_id == tickets.c.id)
                .join(root, root.c.id == func.coalesce(tickets.c.root_ticket_id, tickets.c.id))
                .join(s.runs, s.runs.c.id == root.c.run_id)
                .join(s.messages, s.messages.c.id == s.runs.c.input_message_id)
                .where(
                    tickets.c.agent_id == agent_id,
                    tickets.c.thread_id == thread_id,
                    s.messages.c.deleted_at.is_(None),
                    ((tickets.c.id == update.ticket_id) & tickets.c.root_ticket_id.is_(None))
                    | (tickets.c.root_ticket_id == update.ticket_id),
                )
                .order_by(tickets.c.goal_version.desc())
                .limit(1)
            )
            .mappings()
            .first()
        )
        if prior is None:
            raise BusinessError("not_found", "Ticket does not exist in this main conversation", status=404)
        if prior["goal_version"] != update.expected_goal_version:
            raise BusinessError(
                "revision_conflict", "The Ticket goal has changed. Refresh before updating it.", status=409
            )
        if prior["cancelled_at"] is not None:
            raise BusinessError("owa_ticket_cancelled", "A cancelled Ticket requires a new request", status=409)
        if prior["goal_version"] >= MAX_GOAL_VERSION:
            raise BusinessError("owa_goal_limit", "This Ticket has reached its goal version limit", status=409)
        return cast(Record, dict(prior))

    def omniweave_router(self, agent_id: str, run_id: str) -> Record | None:
        row = (
            self.connection.execute(
                select(
                    s.owa_executions,
                    s.owa_threads.c.main_session_id,
                    s.owa_tickets.c.goal_version,
                    func.coalesce(s.owa_tickets.c.root_ticket_id, s.owa_tickets.c.id).label("root_ticket_id"),
                )
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
        root_id = str(execution["root_ticket_id"])
        if execution["plan_sha256"] == fingerprint:
            return self.omniweave_ticket(agent_id, session_id, root_id)
        if execution["plan_sha256"] is not None:
            raise BusinessError("owa_plan_conflict", "This Ticket already has a different Worker plan", status=409)
        root = mandate(self, agent_id, run_id)
        if root is None:
            raise BusinessError("owa_mandate_missing", "Submit a new owner turn to authorize a Worker plan")
        grants = cast(Record, root["tools"])
        inbound = {peer for task in tasks for peer in task.peers}
        artifact_inbound = {reader for task in tasks for reader in task.artifact_readers}
        toolsets: dict[str, tuple[str, ...]] = {}
        for task in tasks:
            tools = task.tools
            if task.peers:
                tools += (REQUEST_PEER.name,)
            if task.key in inbound:
                tools += (REPLY_PEER.name,)
            if task.artifact_readers:
                tools += (PUBLISH_ARTIFACT.name,)
            if task.key in artifact_inbound:
                tools += (READ_SHARED.name, SUBSCRIBE_ARTIFACTS.name)
            toolsets[task.key] = tools
        if any(
            name not in catalog or grants.get(name) != mandate_tool(catalog[name])
            for names in toolsets.values()
            for name in names
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
        retained = [task for task in tasks if task.reuse is not None]
        sources: dict[str, str] = {}
        for task in retained:
            sources[task.key] = self._owa_validate_reuse(
                agent_id, ticket_id, root_id, cast(int, execution["goal_version"]), task
            )
        now = datetime.fromisoformat(self.clock.now())
        for task in tasks:
            if task.reuse is not None:
                continue
            profile = WORKER_PROFILES[task.profile]
            child = unit.delegation_create(
                agent_id,
                run_id,
                task.request,
                worker_tools={name: tool_hash(catalog[name]) for name in toolsets[task.key]},
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
                    "tools": {name: grants[name] for name in toolsets[task.key]},
                    "budget_micro": task.request["budget_micro"],
                    "max_calls": task.request["max_calls"],
                },
                agent_id,
            )
        for task in tasks:
            for reader in task.artifact_readers:
                self._record(
                    s.owa_artifact_links,
                    {"producer_id": records[task.key]["id"], "reader_id": records[reader]["id"]},
                    agent_id,
                )
            for peer in task.peers:
                self._record(
                    s.owa_peer_links,
                    {"sender_id": records[task.key]["id"], "recipient_id": records[peer]["id"]},
                    agent_id,
                )
            for predecessor in task.needs:
                self._record(
                    s.owa_dependencies,
                    {"task_id": records[task.key]["id"], "needs_id": records[predecessor]["id"]},
                    agent_id,
                )
        for task in retained:
            prior_version = cast(tuple[str, int], task.reuse)[1]
            self._record(
                s.owa_task_reuse,
                {
                    "ticket_id": ticket_id,
                    "task_key": task.key,
                    "source_task_id": sources[task.key],
                    "source_goal_version": prior_version,
                },
                agent_id,
            )
        unit.event(
            agent_id,
            run_id,
            "omniweave.planned",
            {
                "ticket_id": root_id,
                "task_count": len(tasks),
                "goal_version": execution["goal_version"],
                "reuse_count": len(retained),
            },
        )
        return self.omniweave_ticket(agent_id, session_id, root_id)

    def _owa_validate_reuse(self, agent_id: str, ticket_id: str, root_id: str, version: int, task: WorkerTask) -> str:
        invalid = BusinessError(
            "owa_reuse_invalid",
            "Reused evidence must come from an accepted earlier goal version of this Ticket",
            status=422,
        )
        if task.reuse is None:
            raise invalid
        prior_key, prior_version = task.reuse
        source = self.connection.execute(
            select(s.owa_tasks.c.id)
            .join(s.owa_tickets, s.owa_tickets.c.id == s.owa_tasks.c.ticket_id)
            .where(
                s.owa_tickets.c.agent_id == agent_id,
                s.owa_tasks.c.task_key == prior_key,
                s.owa_tickets.c.goal_version == prior_version,
                func.coalesce(s.owa_tickets.c.root_ticket_id, s.owa_tickets.c.id) == root_id,
            )
        ).scalar_one_or_none()
        if source is None or prior_version >= version:
            raise invalid
        run_id = self.connection.execute(select(latest_run_id(str(source)))).scalar_one_or_none()
        if run_id is None:
            raise invalid
        run = self._one(s.runs, agent_id, run_id)
        completed = cast(UnitOfWork, self).run_completion(agent_id, run_id)["state"]
        if run["state"] != "succeeded" or completed not in ("verified", "not_required"):
            raise BusinessError("owa_reuse_unverified", "Reused Worker evidence has not passed acceptance", status=422)
        return str(source)

    def omniweave_ticket_options(self, agent_id: str, session_id: str) -> list[Record]:
        if self.omniweave_main_session(agent_id) != session_id:
            raise BusinessError("not_found", "Ticket options belong to the selected main conversation", status=404)
        tickets, root = s.owa_tickets, s.owa_tickets.alias("original_goal")
        root_id = func.coalesce(tickets.c.root_ticket_id, tickets.c.id)
        rows = self.connection.execute(
            select(
                root_id.label("ticket_id"),
                tickets.c.goal_version,
                func.substr(s.messages.c.content, 1, 80).label("title"),
            )
            .select_from(tickets)
            .join(s.owa_threads, s.owa_threads.c.id == tickets.c.thread_id)
            .join(s.owa_executions, s.owa_executions.c.ticket_id == tickets.c.id)
            .join(root, root.c.id == root_id)
            .join(s.runs, s.runs.c.id == root.c.run_id)
            .join(s.messages, s.messages.c.id == s.runs.c.input_message_id)
            .where(
                tickets.c.agent_id == agent_id,
                s.owa_threads.c.main_session_id == session_id,
                s.owa_executions.c.plan_sha256.is_not(None) | (tickets.c.goal_version > 1),
                s.owa_executions.c.cancelled_at.is_(None),
                s.messages.c.deleted_at.is_(None),
                current_goal(),
            )
            .order_by(tickets.c.created_at.desc(), tickets.c.id.desc())
            .limit(50)
        ).mappings()
        return [cast(Record, dict(row)) for row in rows]

    def omniweave_ticket(
        self, agent_id: str, session_id: str, ticket_id: str = "", *, include_results: bool = False, run_id: str = ""
    ) -> Record:
        tickets = s.owa_tickets
        root_id = func.coalesce(tickets.c.root_ticket_id, tickets.c.id)
        query = (
            select(tickets, s.owa_executions.c.plan_sha256, s.owa_executions.c.cancelled_at, root_id.label("public_id"))
            .join(s.owa_threads, s.owa_threads.c.id == s.owa_tickets.c.thread_id)
            .join(s.sessions, s.sessions.c.id == s.owa_threads.c.main_session_id)
            .outerjoin(s.owa_executions, s.owa_executions.c.ticket_id == s.owa_tickets.c.id)
            .where(
                s.owa_tickets.c.agent_id == agent_id,
                s.owa_threads.c.main_session_id == session_id,
                s.sessions.c.deleted_at.is_(None),
                current_goal(),
            )
        )
        if ticket_id:
            query = query.where(root_id == ticket_id)
        elif run_id:
            original = tickets.alias("requested_turn")
            query = query.where(
                root_id
                == select(func.coalesce(original.c.root_ticket_id, original.c.id))
                .where(original.c.agent_id == agent_id, original.c.run_id == run_id)
                .scalar_subquery()
            )
        else:
            query = query.where(s.owa_executions.c.plan_sha256.is_not(None) | (tickets.c.goal_version > 1)).order_by(
                s.owa_tickets.c.created_at.desc(), s.owa_tickets.c.id.desc()
            )
        ticket = self.connection.execute(query.limit(1)).mappings().first()
        if not ticket:
            raise BusinessError("not_found", "Ticket does not exist in this main conversation", status=404)

        delivery = (
            self.connection.execute(
                select(s.owa_deliveries.c.run_id, s.owa_deliveries.c.message_id, s.owa_deliveries.c.outcome)
                .join(s.messages, s.messages.c.id == s.owa_deliveries.c.message_id)
                .where(s.owa_deliveries.c.ticket_id == ticket["id"], s.messages.c.deleted_at.is_(None))
            )
            .mappings()
            .first()
        )
        root_id = str(ticket["run_id"])
        unit = cast(UnitOfWork, self)
        rows = self.connection.execute(
            select(s.owa_tasks.c.task_key, latest_run_id(s.owa_tasks.c.id))
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
            if item["state"] == "succeeded" and waiting_for_peer(self, agent_id, child_id):
                item["state"] = "waiting_peer"
                if include_results:
                    result["summary"] = ""
            if include_results:
                summary = str(result["summary"])
                item["result"] = summary[:summary_limit]
                item["result_truncated"] = len(summary) > summary_limit or bool(result["summary_truncated"])
            tasks.append(item)
        for key, source_id, source_version, parent in self.connection.execute(
            select(
                s.owa_task_reuse.c.task_key,
                s.owa_task_reuse.c.source_task_id,
                s.owa_task_reuse.c.source_goal_version,
                s.owa_tickets.c.run_id,
            )
            .join(s.owa_tasks, s.owa_tasks.c.id == s.owa_task_reuse.c.source_task_id)
            .join(s.owa_tickets, s.owa_tickets.c.id == s.owa_tasks.c.ticket_id)
            .where(s.owa_task_reuse.c.agent_id == agent_id, s.owa_task_reuse.c.ticket_id == ticket["id"])
            .order_by(s.owa_task_reuse.c.task_key)
        ).all():
            child_id = self.connection.execute(select(latest_run_id(str(source_id)))).scalar_one_or_none()
            item: Record = {
                "key": key,
                "state": "succeeded",
                "error_code": "",
                "unknown_effect": False,
                "unknown_model_calls": False,
                "reused_from_version": source_version,
            }
            if child_id is not None:
                if captured := mandate(self, agent_id, child_id):
                    item.update({name: captured[name] for name in ("profile", "profile_version", "goal_version")})
                if include_results:
                    result = unit.delegation_result(agent_id, str(parent), child_id)
                    summary = str(result["summary"])
                    item["result"] = summary[:summary_limit]
                    item["result_truncated"] = len(summary) > summary_limit or bool(result["summary_truncated"])
            tasks.append(item)
        return {
            "ticket_id": ticket["public_id"],
            "run_id": root_id,
            "goal_version": ticket["goal_version"],
            "prior_work": prior_goal_blocker(
                self, agent_id, str(ticket["public_id"]), cast(int, ticket["goal_version"])
            ),
            "plan_submitted": ticket["plan_sha256"] is not None,
            "cancel_requested": ticket["cancelled_at"] is not None,
            "delivery": cast(Record, dict(delivery)) if delivery is not None else None,
            "tasks": tasks,
            "artifacts": self._owa_public_artifacts(agent_id, str(ticket["id"])),
        }

    def omniweave_private_scope(self, agent_id: str, field: str, identity: str) -> bool:
        statement = select(s.owa_task_attempts.c.id).where(s.owa_task_attempts.c.agent_id == agent_id)
        if field == "run_id":
            statement = statement.where(s.owa_task_attempts.c.run_id == identity)
        else:
            statement = statement.join(s.runs, s.runs.c.id == s.owa_task_attempts.c.run_id)
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
                select(
                    s.owa_tasks.c.id,
                    s.owa_tickets.c.run_id,
                    s.owa_executions.c.cancelled_at,
                    s.owa_tickets.c.root_ticket_id,
                    s.owa_tickets.c.goal_version,
                )
                .join(s.owa_tickets, s.owa_tickets.c.id == s.owa_tasks.c.ticket_id)
                .join(s.owa_executions, s.owa_executions.c.ticket_id == s.owa_tasks.c.ticket_id)
                .join(s.owa_task_attempts, s.owa_task_attempts.c.task_id == s.owa_tasks.c.id)
                .where(s.owa_tasks.c.agent_id == agent_id, s.owa_task_attempts.c.run_id == run_id)
            )
            .mappings()
            .first()
        )
        if task is None:
            return "legacy"
        if task["cancelled_at"] is not None:
            return "blocked"
        if task["root_ticket_id"] and prior_goal_blocker(
            self, agent_id, str(task["root_ticket_id"]), cast(int, task["goal_version"])
        ):
            return "waiting"
        predecessor = s.owa_tasks.alias()
        needs = self.connection.execute(
            select(s.runs.c.id, s.runs.c.state)
            .select_from(s.owa_dependencies)
            .join(predecessor, predecessor.c.id == s.owa_dependencies.c.needs_id)
            .join(s.runs, s.runs.c.id == latest_run_id(predecessor.c.id))
            .where(s.owa_dependencies.c.task_id == task["id"])
        ).all()
        unit = cast(UnitOfWork, self)
        if any(state in {"failed", "cancelled", "interrupted"} for _, state in needs):
            return "blocked"
        if any(state != "succeeded" for _, state in needs):
            return "waiting"
        if any(waiting_for_peer(self, agent_id, child_id) for child_id, _state in needs):
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
        pending = self.connection.execute(
            select(s.owa_deliveries.c.run_id)
            .join(s.owa_tickets, s.owa_tickets.c.id == s.owa_deliveries.c.ticket_id)
            .where(
                s.owa_deliveries.c.agent_id == agent_id,
                s.owa_tickets.c.run_id == run_id,
                s.owa_deliveries.c.message_id.is_(None),
            )
        ).scalar_one_or_none()
        if pending:
            cast(UnitOfWork, self).cancel(agent_id, pending)
