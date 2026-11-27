"""Transaction-scoped repositories. No connection creation, DDL or external calls."""

from __future__ import annotations

import datetime as dt
import hashlib
import json
from dataclasses import asdict
from typing import Any, cast

from sqlalchemy import and_, insert, literal_column, select, update

from ...application.ports import Record
from ...domain.contracts import (
    JSON,
    ActionResult,
    BusinessError,
    ModelReply,
    Outcome,
    RunState,
    ToolSpec,
    check_transition,
)
from ...domain.omniweave import GoalUpdate
from ...domain.run_recovery import arguments_digest
from ...domain.verification_stop import completion_report
from . import schema as s
from .action_review_repository import ActionReviewRepositories
from .android_repository import AndroidRepositories
from .android_unicode_repository import AndroidUnicodeRepositories
from .audit_pruning_repository import AuditPruningRepositories
from .background_review_repository import BackgroundReviewRepositories
from .bilibili_repository import BilibiliRepositories
from .clarify_repository import ClarifyRepositories
from .context_repository import ContextRepositories
from .delegation_repository import DelegationRepositories
from .event_retention_repository import EventRetentionRepositories
from .extension_repository import ExtensionRepositories
from .history_repository import HistoryRepositories
from .history_sequence import allocate_history
from .maintenance_repository import MaintenanceRepositories
from .mcp_repository import MCPRepositories
from .media_repository import MediaRepositories
from .memory_organization_repository import MemoryOrganizationRepositories
from .memory_semantic_repository import MemorySemanticRepositories
from .object_repository import ObjectRepositories
from .omniweave_repository import OmniWeaveRepositories
from .operations_repository import OperationsRepositories
from .people_repository import PeopleRepositories
from .qq_repository import QQRepositories
from .reference_release_repository import ReferenceReleaseRepositories
from .routing_repository import RoutingRepositories
from .run_checkpoint_repository import RunCheckpointRepositories
from .schedule_repository import ScheduleRepositories
from .session_search_repository import SessionSearchRepositories
from .skill_repository import SkillRepositories
from .terminal_review_repository import TerminalReviewRepositories
from .todo_repository import TodoRepositories


def _json_excerpt(arguments: Record) -> str:
    """工具入参摘录（调用链可视化，对齐 V1 _args_excerpt）：JSON 文本过长截断。"""
    raw = json.dumps(arguments, ensure_ascii=False, separators=(",", ":"))
    return raw[:240] + ("…" if len(raw) > 240 else "")


def _text_excerpt(text: str) -> str:
    """工具结果摘录（对齐 V1 _result_excerpt）：不把长输出整段塞进事件流。"""
    raw = str(text or "")
    return raw[:240] + ("…" if len(raw) > 240 else "")


from .reflection_repository import ReflectionRepositories


class Repositories(
    ReflectionRepositories,
    DelegationRepositories,
    TodoRepositories,
    SkillRepositories,
    MemorySemanticRepositories,
    RoutingRepositories,
    RunCheckpointRepositories,
    AndroidUnicodeRepositories,
    AndroidRepositories,
    MediaRepositories,
    BilibiliRepositories,
    ClarifyRepositories,
    QQRepositories,
    TerminalReviewRepositories,
    ActionReviewRepositories,
    MCPRepositories,
    ExtensionRepositories,
    MemoryOrganizationRepositories,
    BackgroundReviewRepositories,
    MaintenanceRepositories,
    AuditPruningRepositories,
    EventRetentionRepositories,
    HistoryRepositories,
    SessionSearchRepositories,
    ObjectRepositories,
    OmniWeaveRepositories,
    PeopleRepositories,
    ContextRepositories,
    ScheduleRepositories,
    OperationsRepositories,
    ReferenceReleaseRepositories,
):
    def instance_identity(self) -> Record:
        current = self.connection.execute(select(s.instances).where(s.instances.c.slot == 1)).mappings().first()
        if current is None:
            raise BusinessError("instance_not_initialized", "Initialize the V2 instance before deploying a service")
        return {"instance_id": current["id"]}

    def initialize(self) -> Record:
        current = self.connection.execute(select(s.instances)).mappings().first()
        if current:
            return dict(current)
        owner = self._record(s.principals, {"role": "owner", "name": "Owner"})
        instance = self._record(s.instances, {"slot": 1, "owner_id": owner["id"]})
        self.create_agent("Default")
        return instance

    def probe_write(self) -> None:
        self.connection.execute(update(s.instances).where(s.instances.c.slot == 1).values(slot=1))

    def create_agent(self, name: str) -> Record:
        if not name.strip() or len(name) > 100:
            raise BusinessError("invalid_agent_name", "Agent name must contain 1 to 100 characters", status=422)
        agent = self._record(s.agents, {"name": name, "enabled": True, "revision": 1})
        model = self.configuration_create(cast(str, agent["id"]), "model", "Default model", True, {})
        value = {
            "persona": "You are a helpful personal assistant.",
            "workspace": "",
            "daily_budget_micro": 1_000_000,
            "call_reservation_micro": 10_000,
            "max_steps": 12,
            "disabled_tools": [],
            "model_profile_id": model["id"],
        }
        self.connection.execute(
            insert(s.settings).values(
                agent_id=agent["id"],
                revision=1,
                permission_epoch=1,
                updated_at=self.clock.now(),
                **{k: v for k, v in value.items() if k != "disabled_tools"},
            )
        )
        self._settings_history_record(cast(str, agent["id"]), value, 1, 1)
        self.ensure_owner(cast(str, agent["id"]))
        return agent

    def get_agent(self, agent_id: str) -> Record:
        row = self.connection.execute(select(s.agents).where(s.agents.c.id == agent_id)).mappings().first()
        if row is None:
            raise BusinessError("not_found", "Agent space does not exist", status=404)
        return cast(Record, dict(row))

    def rename_agent(self, agent_id: str, name: str, revision: int) -> Record:
        from ...domain.agents import name_value, revision_value

        name, revision = name_value(name), revision_value(revision)
        current = self.get_agent(agent_id)
        if current["revision"] != revision:
            raise BusinessError("revision_conflict", "Agent space changed; reload before renaming", status=409)
        if current["name"] == name:
            return current
        row = (
            self.connection.execute(
                update(s.agents)
                .where(s.agents.c.id == agent_id, s.agents.c.revision == revision)
                .values(name=name, revision=revision + 1)
                .returning(s.agents)
            )
            .mappings()
            .first()
        )
        if row is None:
            raise BusinessError("revision_conflict", "Agent space changed; reload before renaming", status=409)
        self.audit(agent_id, "agent.renamed", None, {"revision": revision + 1})
        return cast(Record, dict(row))

    def issue_token(self, token_hash: str, expires_at: str) -> None:
        self.connection.execute(
            insert(s.auth_sessions).values(token_hash=token_hash, created_at=self.clock.now(), expires_at=expires_at)
        )

    def token_valid(self, token_hash: str) -> bool:
        return (
            self.connection.execute(
                select(s.auth_sessions.c.token_hash).where(
                    s.auth_sessions.c.token_hash == token_hash,
                    s.auth_sessions.c.expires_at > self.clock.now(),
                    s.auth_sessions.c.revoked_at.is_(None),
                )
            ).first()
            is not None
        )

    def token_sessions(self, limit: int = 50) -> list[Record]:
        if type(limit) is not int or not 1 <= limit <= 50:
            raise ValueError("Token session limit is not bounded")
        return [
            dict(row)
            for row in self.connection.execute(
                select(s.auth_sessions).order_by(s.auth_sessions.c.created_at.desc()).limit(limit)
            ).mappings()
        ]

    def revoke_other_tokens(self, keep_hash: str) -> None:
        self.connection.execute(
            update(s.auth_sessions)
            .where(s.auth_sessions.c.revoked_at.is_(None), s.auth_sessions.c.token_hash != keep_hash)
            .values(revoked_at=self.clock.now())
        )

    def _message(self, agent_id: str, session_id: str, role: str, text: str) -> Record:
        session = self._one(s.sessions, agent_id, session_id)
        if session["deleted_at"]:
            raise BusinessError("session_deleted", "Session has been deleted")
        seq = cast(int, session["next_seq"]) + 1
        self.connection.execute(update(s.sessions).where(s.sessions.c.id == session_id).values(next_seq=seq))
        return self._record(s.messages, {"session_id": session_id, "seq": seq, "role": role, "content": text}, agent_id)

    def accept_turn(
        self,
        agent_id: str,
        session_id: str | None,
        key: str,
        text: str,
        config: Record,
        *,
        model_required: bool = True,
        origin: str = "chat",
        image_ids: tuple[str, ...] = (),
        mode: str | None = None,
        skill_ids: tuple[str, ...] = (),
        skill_platform: str = "",
        goal_update: GoalUpdate | None = None,
    ) -> Record:
        if origin not in {"chat", "qq"}:
            raise BusinessError("invalid_turn_origin", "Unknown turn origin")
        from ...domain.routing import requested

        route_mode = requested(mode)
        from ...domain.skills import identifiers

        selected_skills = identifiers(skill_ids)
        if selected_skills and (origin != "chat" or not model_required):
            raise BusinessError("skill_channel_unsupported", "This channel cannot activate owner skills", status=422)
        from ...domain.images import image_ids as checked_image_ids

        images = checked_image_ids(image_ids)
        if images and (origin != "chat" or not model_required):
            raise BusinessError("image_channel_unsupported", "This channel cannot attach image messages", status=422)
        if session_id and self.omniweave_private_scope(agent_id, "session_id", session_id):
            raise BusinessError("not_found", "Session does not exist in this Agent space", status=404)
        if (
            session_id
            and origin != "qq"
            and self.connection.execute(
                select(s.qq_bindings.c.id).where(
                    s.qq_bindings.c.agent_id == agent_id, s.qq_bindings.c.session_id == session_id
                )
            ).first()
        ):
            raise BusinessError("channel_session_readonly", "Use the QQ channel to write to its conversation")
        channel = origin if model_required else "owner_command"
        request_fingerprint = hashlib.sha256(
            json.dumps(
                {
                    "session_id": session_id,
                    "text": text,
                    **({"image_ids": images} if images else {}),
                    **({"mode": route_mode} if route_mode != "default" else {}),
                    **({"skill_ids": selected_skills} if selected_skills else {}),
                    **(
                        {"goal_update": [goal_update.ticket_id, goal_update.expected_goal_version]}
                        if goal_update
                        else {}
                    ),
                },
                sort_keys=True,
            ).encode()
        ).hexdigest()
        prior = (
            self.connection.execute(
                select(s.inbox).where(
                    s.inbox.c.agent_id == agent_id, s.inbox.c.channel == channel, s.inbox.c.key == key
                )
            )
            .mappings()
            .first()
        )
        if prior:
            if prior["request_fingerprint"] != request_fingerprint:
                raise BusinessError("idempotency_conflict", "This key already identifies a different request")
            run = self.get_run(agent_id, cast(str, prior["run_id"]))
            return {"run_id": run["id"], "session_id": run["session_id"], "duplicate": True}
        if not key or len(key) > 200 or (not text and not images) or len(text) > 200_000:
            raise BusinessError("invalid_turn", "A bounded message and idempotency key are required", status=422)
        settings = self.settings(agent_id)
        # RoutingRepositories 尚未纳入 strict；用局部 Any 桥接其未类型化解析结果。
        routing: Any = self
        route = (
            cast(Record, routing.route_resolve(agent_id, text, route_mode, bool(images), origin == "qq"))
            if model_required
            else None
        )
        captured = self.capture_configuration(
            agent_id, model_required=model_required, model_id=cast(str, route["model_id"]) if route else None
        )
        if images:
            self.image_admit(agent_id, cast(str, captured["model_revision_id"]), images)
        if session_id is None:
            session_id = cast(
                str,
                self._record(
                    s.sessions, {"title": text[:80] or "Image conversation", "revision": 1, "next_seq": 0}, agent_id
                )["id"],
            )
        self.require_turn_capacity(1)
        assert session_id is not None
        message = self._message(agent_id, session_id, "user", text)
        for position, object_id in enumerate(images):
            self._record(
                s.message_parts,
                {"message_id": message["id"], "position": position, "kind": "image", "object_id": object_id},
                agent_id,
            )
        run = self._record(
            s.runs,
            {
                "session_id": session_id,
                "state": "queued",
                "execution_kind": "model" if model_required else "owner",
                "config_revision": settings["revision"],
                "input_message_id": message["id"],
                **captured,
                "last_seq": 0,
                "error_code": "",
            },
            agent_id,
        )
        if route:
            routing.route_attach(agent_id, cast(str, run["id"]), route)
        if model_required and origin != "qq":
            self.extension_capture_run(agent_id, cast(str, run["id"]))
            self.skill_capture_run(agent_id, cast(str, run["id"]), selected_skills, skill_platform)
        self._record(
            s.inbox,
            {"channel": channel, "key": key, "run_id": run["id"], "request_fingerprint": request_fingerprint},
            agent_id,
        )
        self.connection.execute(update(s.messages).where(s.messages.c.id == message["id"]).values(run_id=run["id"]))
        self._record(
            s.work_items,
            {"run_id": run["id"], "kind": "turn", "state": "pending", "payload": {}, "attempt": 0},
            agent_id,
        )
        self.event(agent_id, cast(str, run["id"]), "run.queued", {})
        if route:
            self.event(
                agent_id,
                cast(str, run["id"]),
                "routing.selected",
                {"mode": route["mode"], "source": route["source"], "reason": route["reason"]},
            )
        return {"run_id": run["id"], "session_id": session_id, "duplicate": False}

    def owner_command(self, agent_id: str, run_id: str) -> Record | None:
        row = (
            self.connection.execute(
                select(s.owner_commands).where(
                    s.owner_commands.c.agent_id == agent_id, s.owner_commands.c.run_id == run_id
                )
            )
            .mappings()
            .first()
        )
        return dict(row) if row else None

    def owner_command_replay(self, agent_id: str, request_id: str, fingerprint: str) -> Record | None:
        row = (
            self.connection.execute(
                select(s.owner_commands).where(
                    s.owner_commands.c.agent_id == agent_id, s.owner_commands.c.request_id == request_id
                )
            )
            .mappings()
            .first()
        )
        if row:
            if row["request_sha256"] != fingerprint:
                raise BusinessError("idempotency_conflict", "This request already identifies another owner action")
            run = self.get_run(agent_id, row["run_id"])
            return {"run_id": run["id"], "session_id": run["session_id"], "duplicate": True}
        return None

    def accept_owner_command(
        self,
        agent_id: str,
        session_id: str,
        request_id: str,
        fingerprint: str,
        arguments: Record,
        *,
        tool: str = "desktop_semantic",
    ) -> Record:
        if tool not in {"desktop_semantic", "android_input", "android_restore_ime"}:
            raise BusinessError("owner_tool_invalid", "Owner command capability is not registered")
        prior = self.owner_command_replay(agent_id, request_id, fingerprint)
        if prior:
            return prior
        session = self._one(s.sessions, agent_id, session_id)
        if session["deleted_at"]:
            raise BusinessError("session_deleted", "Conversation was deleted")
        receipt = self.accept_turn(
            agent_id,
            session_id,
            "owner-command/" + request_id,
            "Owner requested " + tool + " " + str(arguments["kind"]),
            {},
            model_required=False,
        )
        self._record(
            s.owner_commands,
            {
                "run_id": receipt["run_id"],
                "request_id": request_id,
                "request_sha256": fingerprint,
                "boot_id": self.boot_id,
                "session_epoch": session["content_epoch"],
                "tool": tool,
                "arguments": arguments,
            },
            agent_id,
        )
        self.audit(
            agent_id,
            "owner.command_queued",
            cast(str, receipt["run_id"]),
            {"request_id": request_id, "request_sha256": fingerprint, "tool": tool},
        )
        return receipt

    def assert_run_authorized(self, agent_id: str, run_id: str) -> None:
        self.assert_context(agent_id, run_id)
        if self.get_run(agent_id, run_id).get("execution_kind") != "owner":
            self.assert_model_authorized(agent_id, run_id)

    def desktop_dispatch(self, agent_id: str, run_id: str, action_id: str, arguments: Record) -> None:
        self.owner_dispatch(agent_id, run_id, action_id, arguments, "desktop_semantic")

    def owner_dispatch(self, agent_id: str, run_id: str, action_id: str, arguments: Record, tool: str) -> None:
        command = self.owner_command(agent_id, run_id)
        if not command or command["action_id"] != action_id or command["tool"] != tool:
            raise BusinessError("desktop_binding_changed", "Desktop action binding changed")
        self.approved_dispatch(agent_id, run_id, action_id, arguments, tool)

    def approved_dispatch(self, agent_id: str, run_id: str, action_id: str, arguments: Record, tool: str) -> None:
        self.assert_run_authorized(agent_id, run_id)
        reviews: Any = self
        action, run = cast(Record, reviews.action(agent_id, action_id)), self.get_run(agent_id, run_id)
        settings = self.settings(agent_id)
        if action["run_id"] != run_id or action["tool"] != tool or action["arguments"] != arguments:
            raise BusinessError("action_binding_changed", "Approved action binding changed")
        if (
            run["state"] != "running"
            or action["state"] != "executing"
            or action["approval_state"] != "approved"
            or str(action["approval_expires_at"]) <= self.clock.now()
        ):
            raise BusinessError(
                "desktop_action_revoked" if tool == "desktop_semantic" else "action_authority_revoked",
                "Action is no longer approved",
            )
        settings_value = cast(Record, settings["value"])
        if settings["policy_revision"] != action["policy_revision"] or action["tool"] in cast(
            list[object], settings_value["disabled_tools"]
        ):
            raise BusinessError("policy_changed", "Action policy was revoked")

    def get_run(self, agent_id: str, run_id: str) -> Record:
        return self._one(s.runs, agent_id, run_id)

    def transition(self, agent_id: str, run_id: str, state: str, error_code: str = "") -> None:
        run = self.get_run(agent_id, run_id)
        target = RunState(state)
        check_transition(cast(str, run["state"]), target)
        if state == "running":
            work = self._work(agent_id, run_id)
            if work["state"] == "pending" and not self._claim_run(agent_id, run_id):
                raise BusinessError("run_claim_unavailable", "Run cannot acquire its session")
            self.assert_run_lease(agent_id, run_id)
        ended = (
            self.clock.now()
            if target in {RunState.SUCCEEDED, RunState.FAILED, RunState.CANCELLED, RunState.INTERRUPTED}
            else None
        )
        self.connection.execute(
            update(s.runs)
            .where(s.runs.c.id == run_id, s.runs.c.state == run["state"])
            .values(state=state, ended_at=ended, error_code=error_code)
        )
        if ended or state == "running":
            work_state = (
                "running"
                if not ended
                else "done"
                if state == "succeeded"
                else "cancelled"
                if state == "cancelled"
                else "failed"
            )
            self.connection.execute(
                update(s.work_items).where(s.work_items.c.run_id == run_id).values(state=work_state)
            )
            if ended:
                work = self._work(agent_id, run_id)
                if work["claim_token"]:
                    self.lease_release(
                        agent_id,
                        "session/" + cast(str, run["session_id"]),
                        cast(str, work["claim_token"]),
                        cast(int, work["lease_fence"]),
                    )
        self.event(agent_id, run_id, "run." + state, {"error_code": error_code})

    def event(self, agent_id: str, run_id: str, kind: str, data: Record) -> int:
        run = self.get_run(agent_id, run_id)
        seq = cast(int, run["last_seq"]) + 1
        self.connection.execute(update(s.runs).where(s.runs.c.id == run_id).values(last_seq=seq))
        self.connection.execute(
            insert(s.run_events).values(
                sequence=allocate_history(self.connection, "events"),
                agent_id=agent_id,
                run_id=run_id,
                seq=seq,
                created_at=self.clock.now(),
                kind=kind,
                data=data,
            )
        )
        return seq

    def messages(self, agent_id: str, session_id: str, limit: int = 50) -> list[Record]:
        self._one(s.sessions, agent_id, session_id)
        rows = (
            self.connection.execute(
                select(s.messages)
                .where(
                    s.messages.c.agent_id == agent_id,
                    s.messages.c.session_id == session_id,
                    s.messages.c.deleted_at.is_(None),
                )
                .order_by(s.messages.c.seq.desc())
                .limit(min(200, max(1, limit)))
            )
            .mappings()
            .all()
        )
        return [dict(row) for row in reversed(rows)]

    def run_context(self, agent_id: str, run_id: str) -> list[Record]:
        self.get_run(agent_id, run_id)
        snapshots = self.connection.execute(
            select(s.context_snapshots.c.id)
            .where(s.context_snapshots.c.agent_id == agent_id, s.context_snapshots.c.run_id == run_id)
            .limit(32)
        ).scalars()
        return [row for snapshot in snapshots for row in self.context_memories(agent_id, snapshot)]

    def finish(self, agent_id: str, run_id: str, text: str, delivery: Record | None = None) -> None:
        self.assert_run_authorized(agent_id, run_id)
        qq_authority: Any = self
        channel_delivery = cast("Record | None", qq_authority.qq_run_authorized(agent_id, run_id))
        if channel_delivery:
            if delivery is not None and delivery != channel_delivery:
                raise BusinessError("qq_delivery_mismatch", "QQ replies must use their captured conversation")
            delivery = channel_delivery
        if delivery and self.owner_command(agent_id, run_id):
            raise BusinessError("owner_command_delivery_forbidden", "Owner commands do not create external deliveries")
        run = self.get_run(agent_id, run_id)
        check_transition(cast(str, run["state"]), RunState.SUCCEEDED)
        message = self._message(agent_id, cast(str, run["session_id"]), "assistant", text)
        self.connection.execute(update(s.messages).where(s.messages.c.id == message["id"]).values(run_id=run_id))
        if delivery:
            deliveries: Any = self
            deliveries.delivery_create(
                agent_id,
                cast(str, message["id"]),
                str(delivery["config_id"]),
                str(delivery["target"]),
                str(delivery.get("key") or "run-output:" + run_id),
                str(delivery["adapter_id"]),
                str(delivery["account_key"]),
                str(delivery["config_revision_id"]),
                allow_running=True,
            )
        self.transition(agent_id, run_id, "succeeded")
        self.audit(agent_id, "run.completed", run_id, {"message_id": message["id"]})

    def reserve_call(
        self, agent_id: str, run_id: str, amount: int, limit: int, model: str, *, purpose: str = "turn"
    ) -> str:
        self.get_run(agent_id, run_id)
        self.delegation_check_budget(agent_id, run_id, amount)
        if amount < 0 or limit < 0:
            raise BusinessError("invalid_budget", "Budget cannot be negative", status=422)
        day = self.clock.now()[:10]
        key = and_(
            s.budget_accounts.c.agent_id == agent_id,
            s.budget_accounts.c.day == day,
            s.budget_accounts.c.currency == "USD",
        )
        account = self.connection.execute(select(s.budget_accounts).where(key)).mappings().first()
        if account is None:
            self.connection.execute(
                insert(s.budget_accounts).values(
                    agent_id=agent_id, day=day, currency="USD", held_micro=0, spent_micro=0
                )
            )
        condition = (
            key
            if limit == 0
            else and_(key, s.budget_accounts.c.held_micro + s.budget_accounts.c.spent_micro + amount <= limit)
        )
        changed = self.connection.execute(
            update(s.budget_accounts).where(condition).values(held_micro=s.budget_accounts.c.held_micro + amount)
        ).rowcount
        if changed != 1:
            raise BusinessError("budget_exceeded", "Daily model budget is exhausted")
        return cast(
            str,
            self._record(
                s.model_calls,
                {
                    "run_id": run_id,
                    "model": model,
                    "day": day,
                    "state": "reserved",
                    "reserved_micro": amount,
                    "purpose": purpose,
                },
                agent_id,
            )["id"],
        )

    def _call_route(self, agent_id: str, call_id: str) -> tuple[int, str] | None:
        row = (
            self.connection.execute(
                select(s.model_call_routes.c.ordinal, s.model_call_routes.c.mode).where(
                    s.model_call_routes.c.call_id == call_id,
                    s.model_call_routes.c.agent_id == agent_id,
                )
            )
            .mappings()
            .first()
        )
        return (cast(int, row["ordinal"]), cast(str, row["mode"])) if row is not None else None

    def begin_call(self, agent_id: str, call_id: str) -> None:
        """实时调用链：主对话调用在请求发出前落「模型调用第 N 步 · 调用中」事件。

        摘要/预检调用不发，避免扰乱工具链；与 settle_call 的 completed 事件按 call_id 配对。
        """
        call = self._one(s.model_calls, agent_id, call_id)
        if call["purpose"] != "turn":
            return
        route = self._call_route(agent_id, call_id)
        self.event(
            agent_id,
            cast(str, call["run_id"]),
            "model.started",
            {
                "call_id": call_id,
                "step": route[0] if route else None,
                "mode": route[1] if route else None,
            },
        )

    def settle_call(
        self, agent_id: str, call_id: str, reply: ModelReply | None, *, known_not_sent: bool = False
    ) -> None:
        call = self._one(s.model_calls, agent_id, call_id)
        if call["state"] not in {"reserved", "unknown"}:
            raise BusinessError("already_settled", "Model call was already settled")
        cost = 0 if known_not_sent else reply.cost_micro if reply else None
        state = "not_sent" if known_not_sent else "settled" if cost is not None else "unknown"
        if cost is not None:
            if cost < 0:
                raise BusinessError("invalid_cost", "Model cost cannot be negative", status=422)
            self.connection.execute(
                update(s.budget_accounts)
                .where(
                    s.budget_accounts.c.agent_id == agent_id,
                    s.budget_accounts.c.day == call["day"],
                    s.budget_accounts.c.currency == "USD",
                )
                .values(
                    held_micro=s.budget_accounts.c.held_micro - call["reserved_micro"],
                    spent_micro=s.budget_accounts.c.spent_micro + cost,
                )
            )
        self.connection.execute(
            update(s.model_calls)
            .where(s.model_calls.c.id == call_id)
            .values(
                state=state,
                cost_micro=cost,
                input_tokens=reply.input_tokens if reply else None,
                output_tokens=reply.output_tokens if reply else None,
                cached_prompt_tokens=reply.cached_prompt_tokens if reply else None,
                cache_creation_tokens=reply.cache_creation_tokens if reply else None,
                ended_at=self.clock.now(),
            )
        )
        # 调用链可视化（对齐 V1 model_end）：主对话调用落「模型调用第 N 步」事件，
        # 带档位、返回字数与上报 token；摘要/预检调用不发，不扰乱工具链。
        if call["purpose"] == "turn" and not known_not_sent:
            route = self._call_route(agent_id, call_id)
            self.event(
                agent_id,
                cast(str, call["run_id"]),
                "model.completed",
                {
                    "call_id": call_id,
                    "step": route[0] if route else None,
                    "mode": route[1] if route else None,
                    "text_chars": len(reply.text) if reply else 0,
                    "tool_calls": [c.name for c in reply.calls] if reply else [],
                    "input_tokens": reply.input_tokens if reply else None,
                    "output_tokens": reply.output_tokens if reply else None,
                    "state": state,
                },
            )

    def prepare_action(self, agent_id: str, run_id: str, spec: ToolSpec, arguments: Record, policy: int) -> Record:
        run = self.get_run(agent_id, run_id)
        if run["state"] != "running":
            raise BusinessError("run_not_running", "Run no longer accepts actions")
        self.omniweave_assert_action(agent_id, run_id, spec)
        current = self.settings(agent_id)
        current_value = cast(Record, current["value"])
        if current["policy_revision"] != policy or spec.name in cast(
            list[object], current_value.get("disabled_tools", [])
        ):
            raise BusinessError("policy_changed", "Action policy changed; submit a new action")
        digest = arguments_digest(arguments)
        action = self._record(
            s.actions,
            {
                "run_id": run_id,
                "tool": spec.name,
                "arguments": arguments,
                "arguments_sha256": digest,
                "writes": spec.writes,
                "state": "pending" if spec.writes else "approved",
                "effect": "none",
                "policy_revision": policy,
            },
            agent_id,
        )
        self.extension_bind_action(agent_id, run_id, cast(str, action["id"]), spec)
        self.mcp_bind_action(agent_id, run_id, cast(str, action["id"]), spec)
        command = self.owner_command(agent_id, run_id)
        if command:
            if command["tool"] != spec.name or command["arguments"] != arguments or command["action_id"] is not None:
                raise BusinessError("owner_command_changed", "Owner command cannot be replaced or replayed")
            self.connection.execute(
                update(s.owner_commands)
                .where(s.owner_commands.c.id == command["id"])
                .values(arguments=None, action_id=action["id"])
            )
        if spec.writes:
            expires = (dt.datetime.fromisoformat(self.clock.now()) + dt.timedelta(seconds=180)).isoformat(
                timespec="microseconds"
            )
            self._record(s.approvals, {"action_id": action["id"], "state": "pending", "expires_at": expires}, agent_id)
            self.transition(agent_id, run_id, "waiting_approval")
        self.audit(
            agent_id,
            "action.prepared",
            cast(str, action["id"]),
            {"tool": spec.name, "arguments_sha256": digest, "policy_revision": policy},
        )
        self.event(
            agent_id,
            run_id,
            "action.prepared",
            {
                "action_id": action["id"],
                "tool": spec.name,
                "requires_approval": spec.writes,
                # 调用链可视化（对齐 V1）：入参摘录只进本库事件流——模型上下文
                # 全文本来就在这里，不新增隐私面；截断防长参数撑爆事件。
                "args_excerpt": _json_excerpt(arguments),
                "arguments_sha256": digest,
            },
        )
        return action

    def pending_approvals(self, agent_id: str) -> list[Record]:
        rows = self.connection.execute(
            select(
                s.approvals, s.actions.c.tool, s.actions.c.arguments, s.actions.c.run_id, s.actions.c.policy_revision
            )
            .join(s.actions, s.actions.c.id == s.approvals.c.action_id)
            .where(
                s.approvals.c.agent_id == agent_id,
                s.approvals.c.state == "pending",
                s.approvals.c.expires_at > self.clock.now(),
            )
            .limit(100)
        ).mappings()
        return [dict(row) for row in rows]

    def resolve_approval(self, agent_id: str, approval_id: str, allowed: bool, policy: int) -> None:
        approval = self._one(s.approvals, agent_id, approval_id)
        action = self._one(s.actions, agent_id, cast(str, approval["action_id"]))
        if (
            approval["state"] != "pending"
            or cast(str, approval["expires_at"]) <= self.clock.now()
            or action["state"] != "pending"
        ):
            raise BusinessError("approval_expired", "Approval is no longer pending")
        bindings: Any = self
        bindings.extension_assert_action(agent_id, cast(str, action["id"]))
        bindings.mcp_assert_action(agent_id, cast(str, action["id"]))
        if self.settings(agent_id)["policy_revision"] != policy or action["policy_revision"] != policy:
            raise BusinessError("policy_changed", "Approval belongs to an older policy")
        if self.get_run(agent_id, cast(str, action["run_id"]))["state"] != "waiting_approval":
            raise BusinessError("run_not_waiting", "Run no longer accepts approval")
        state = "approved" if allowed else "denied"
        self.connection.execute(
            update(s.approvals).where(s.approvals.c.id == approval_id).values(state=state, resolved_at=self.clock.now())
        )
        self.connection.execute(update(s.actions).where(s.actions.c.id == action["id"]).values(state=state))
        self.audit(agent_id, "approval." + state, cast(str, action["id"]), {"policy_revision": policy})
        self.transition(agent_id, cast(str, action["run_id"]), "running")

    def authorize_action(self, agent_id: str, action_id: str) -> Record:
        self.assert_run_authorized(agent_id, cast(str, self._one(s.actions, agent_id, action_id)["run_id"]))
        action = self._one(s.actions, agent_id, action_id)
        run = self.get_run(agent_id, cast(str, action["run_id"]))
        current = self.settings(agent_id)
        if run["state"] != "running" or action["state"] != "approved":
            raise BusinessError("action_not_authorized", "Action is not authorized")
        authorize_bindings: Any = self
        authorize_bindings.extension_assert_action(agent_id, action_id)
        authorize_bindings.mcp_assert_action(agent_id, action_id)
        authorize_value = cast(Record, current["value"])
        if action["policy_revision"] != current["policy_revision"] or action["tool"] in cast(
            list[object], authorize_value.get("disabled_tools", [])
        ):
            raise BusinessError("policy_changed", "Action policy was revoked")
        if action["writes"]:
            approval = (
                self.connection.execute(select(s.approvals).where(s.approvals.c.action_id == action_id))
                .mappings()
                .one()
            )
            if approval["state"] != "approved" or approval["expires_at"] <= self.clock.now():
                raise BusinessError("approval_expired", "Action approval has expired")
        self.connection.execute(
            update(s.actions).where(s.actions.c.id == action_id).values(state="executing", started_at=self.clock.now())
        )
        self.audit(agent_id, "action.executing", action_id, {"tool": action["tool"]})
        # 实时调用链：执行开始即落事件，运行中界面能看到「正在执行」的工具行。
        self.event(agent_id, cast(str, action["run_id"]), "action.executing", {"action_id": action_id})
        return action

    def extension_dispatch(self, agent_id: str, run_id: str, action_id: str, name: str) -> Record:
        self.assert_context(agent_id, run_id)
        self.assert_model_authorized(agent_id, run_id)
        action, current = self._one(s.actions, agent_id, action_id), self.settings(agent_id)
        dispatch_value = cast(Record, current["value"])
        if action["policy_revision"] != current["policy_revision"] or name in cast(
            list[object], dispatch_value["disabled_tools"]
        ):
            raise BusinessError("policy_changed", "Action policy changed before plugin dispatch")
        approval = (
            self.connection.execute(
                select(s.approvals).where(
                    s.approvals.c.agent_id == agent_id,
                    s.approvals.c.action_id == action_id,
                )
            )
            .mappings()
            .first()
        )
        if approval is None or approval["state"] != "approved" or approval["expires_at"] <= self.clock.now():
            raise BusinessError("approval_expired", "Plugin action approval expired before dispatch")
        dispatch_bindings: Any = self
        return cast(
            Record,
            {
                **dispatch_bindings._extension_dispatch_binding(agent_id, run_id, action_id, name),
                "arguments_sha256": action["arguments_sha256"],
            },
        )

    def _extension_revoke(self, slot: Record) -> list[str]:
        waiting = list(
            self.connection.execute(
                select(s.runs.c.id)
                .join(s.actions, and_(s.actions.c.run_id == s.runs.c.id, s.actions.c.agent_id == s.runs.c.agent_id))
                .join(
                    s.action_extension_bindings,
                    and_(
                        s.action_extension_bindings.c.action_id == s.actions.c.id,
                        s.action_extension_bindings.c.agent_id == s.actions.c.agent_id,
                    ),
                )
                .join(
                    s.run_extension_tools,
                    and_(
                        s.run_extension_tools.c.id == s.action_extension_bindings.c.run_tool_id,
                        s.run_extension_tools.c.agent_id == s.action_extension_bindings.c.agent_id,
                    ),
                )
                .where(
                    s.runs.c.agent_id == slot["agent_id"],
                    s.runs.c.state == "waiting_approval",
                    s.actions.c.state.in_(["pending", "approved"]),
                    s.run_extension_tools.c.extension_id == slot["id"],
                )
                .distinct()
            ).scalars()
        )
        parent: Any = super()
        executing = cast(list[str], parent._extension_revoke(slot))
        for run_id in waiting:
            try:
                self.transition(cast(str, slot["agent_id"]), run_id, "running")
            except BusinessError as exc:
                # Revocation must commit even when an old worker has lost its lease;
                # the ordinary run recovery path owns that interrupted execution.
                if exc.code not in {"run_lease_lost", "previous_outcome_unknown"}:
                    raise
        return executing

    def settle_action(self, agent_id: str, action_id: str, result: ActionResult) -> None:
        action = self._one(s.actions, agent_id, action_id)
        if action["state"] != "executing":
            raise BusinessError("action_already_settled", "Action is not executing")
        erased = self.connection.execute(
            select(s.context_documents.c.id)
            .join(s.document_revisions, s.document_revisions.c.id == s.context_documents.c.document_revision_id)
            .where(s.context_documents.c.action_id == action_id, s.document_revisions.c.redacted_at.is_not(None))
        ).first()
        if erased and not action["writes"]:
            result = ActionResult(Outcome.ERROR, "Source document erased", error_code="context_invalidated")
        for object_id in result.artifacts:
            self.object_link(agent_id, object_id, "action", action_id)
        self.connection.execute(
            update(s.actions)
            .where(s.actions.c.id == action_id)
            .values(
                state=result.status.value, effect=result.effect.value, result=asdict(result), ended_at=self.clock.now()
            )
        )
        self.audit(
            agent_id,
            "action." + result.status.value,
            action_id,
            {"effect": result.effect.value, "error_code": result.error_code},
        )
        elapsed_ms: int | None = None
        if action.get("started_at"):
            try:
                delta = (
                    dt.datetime.fromisoformat(self.clock.now()) - dt.datetime.fromisoformat(str(action["started_at"]))
                ).total_seconds() * 1000
                if delta >= 0:
                    elapsed_ms = int(delta)
            except ValueError:
                elapsed_ms = None
        self.event(
            agent_id,
            cast(str, action["run_id"]),
            "action.completed",
            {
                "action_id": action_id,
                "status": result.status.value,
                "effect": result.effect.value,
                # 调用链可视化（对齐 V1）：结果摘录与执行耗时（不含审批等待）。
                "excerpt": _text_excerpt(result.text),
                "elapsed_ms": elapsed_ms,
                "error_code": result.error_code,
            },
        )

    def action(self, agent_id: str, action_id: str) -> Record:
        row = (
            self.connection.execute(
                select(
                    s.actions,
                    s.approvals.c.state.label("approval_state"),
                    s.approvals.c.expires_at.label("approval_expires_at"),
                )
                .outerjoin(s.approvals, s.approvals.c.action_id == s.actions.c.id)
                .where(s.actions.c.agent_id == agent_id, s.actions.c.id == action_id)
            )
            .mappings()
            .first()
        )
        if row is None:
            raise BusinessError("not_found", "Action does not exist in this Agent space", status=404)
        return dict(row)

    def run_applied_writes(self, agent_id: str, run_id: str) -> list[Record]:
        rows = (
            self.connection.execute(
                select(s.actions.c.tool, s.actions.c.result)
                .where(
                    s.actions.c.agent_id == agent_id,
                    s.actions.c.run_id == run_id,
                    s.actions.c.writes.is_(True),
                    s.actions.c.effect == "applied",
                )
                .order_by(s.actions.c.created_at)
                .limit(200)
            )
            .mappings()
            .all()
        )
        evidence: list[Record] = []
        for row in rows:
            result = row["result"]
            verification = ""
            if isinstance(result, dict):
                raw = cast(Record, result).get("verification")
                verification = raw if isinstance(raw, str) else ""
            evidence.append(cast(Record, {"tool": str(row["tool"]), "verification": verification}))
        return evidence

    def run_completion(self, agent_id: str, run_id: str) -> Record:
        actions = self.connection.execute(
            select(
                s.actions.c.id,
                s.actions.c.tool,
                s.actions.c.state,
                s.actions.c.effect,
                s.actions.c.writes,
                s.actions.c.result,
            )
            .where(s.actions.c.agent_id == agent_id, s.actions.c.run_id == run_id)
            .order_by(literal_column("actions.rowid"))
            .limit(201)
        ).mappings()
        rows = [dict(row) for row in actions]
        objects: dict[str, list[JSON]] = {}
        for row in self.connection.execute(
            select(s.action_objects.c.action_id, s.objects.c.id, s.objects.c.sha256)
            .join(s.actions, s.actions.c.id == s.action_objects.c.action_id)
            .join(s.objects, s.objects.c.id == s.action_objects.c.object_id)
            .where(
                s.actions.c.agent_id == agent_id,
                s.actions.c.run_id == run_id,
                s.objects.c.agent_id == agent_id,
                s.actions.c.tool.startswith("ext__", autoescape=True),
            )
            .order_by(s.objects.c.id)
            .limit(201)
        ):
            objects.setdefault(row.action_id, []).append({"id": row.id, "sha256": row.sha256})
        return completion_report(rows[:200], objects, limited=len(rows) > 200 or sum(map(len, objects.values())) > 200)

    def cancel(self, agent_id: str, run_id: str) -> None:
        self.omniweave_cancel(agent_id, run_id)
        for child_id in self.delegation_children(agent_id, run_id):
            self.cancel(agent_id, child_id)
        run = self.get_run(agent_id, run_id)
        if run["state"] in {"succeeded", "failed", "cancelled", "interrupted", "cancelling"}:
            return
        target = "cancelled" if run["state"] == "queued" else "cancelling"
        self.transition(agent_id, run_id, target)
        self.connection.execute(
            update(s.owner_commands)
            .where(s.owner_commands.c.run_id == run_id, s.owner_commands.c.agent_id == agent_id)
            .values(arguments=None)
        )
        action_ids = select(s.actions.c.id).where(s.actions.c.run_id == run_id, s.actions.c.agent_id == agent_id)
        self.connection.execute(
            update(s.approvals)
            .where(s.approvals.c.action_id.in_(action_ids), s.approvals.c.state == "pending")
            .values(state="cancelled")
        )
        self.connection.execute(
            update(s.actions)
            .where(s.actions.c.run_id == run_id, s.actions.c.state.in_(["pending", "approved"]))
            .values(state="cancelled")
        )
        self.cancel_run_clarifications(agent_id, run_id)
        self.audit(agent_id, "run.cancel_requested", run_id, {})

    def cancel_approvals(self) -> None:
        self.connection.execute(update(s.approvals).where(s.approvals.c.state == "pending").values(state="cancelled"))
        self.connection.execute(
            update(s.actions).where(s.actions.c.state.in_(["pending", "approved"])).values(state="cancelled")
        )
        self.connection.execute(
            update(s.clarifications)
            .where(s.clarifications.c.state == "pending")
            .values(
                state="cancelled",
                outcome="cancelled",
                notice="Run was interrupted.",
                resolved_at=self.clock.now(),
                revision=s.clarifications.c.revision + 1,
            )
        )

    def close_run_actions(self, agent_id: str, run_id: str) -> int:
        self.get_run(agent_id, run_id)
        self.connection.execute(
            update(s.owner_commands)
            .where(s.owner_commands.c.run_id == run_id, s.owner_commands.c.agent_id == agent_id)
            .values(arguments=None)
        )
        ids = select(s.actions.c.id).where(s.actions.c.agent_id == agent_id, s.actions.c.run_id == run_id)
        self.connection.execute(
            update(s.approvals)
            .where(s.approvals.c.action_id.in_(ids), s.approvals.c.state == "pending")
            .values(state="cancelled", resolved_at=self.clock.now())
        )
        self.connection.execute(
            update(s.actions)
            .where(
                s.actions.c.agent_id == agent_id,
                s.actions.c.run_id == run_id,
                s.actions.c.state.in_(["pending", "approved"]),
            )
            .values(state="cancelled", ended_at=self.clock.now())
        )
        unresolved = (
            self.connection.execute(
                select(s.actions.c.id).where(
                    s.actions.c.agent_id == agent_id, s.actions.c.run_id == run_id, s.actions.c.state == "executing"
                )
            )
            .scalars()
            .all()
        )
        if unresolved:
            self.connection.execute(
                update(s.actions)
                .where(s.actions.c.id.in_(unresolved))
                .values(state="unknown", effect="unknown", ended_at=self.clock.now())
            )
            for action_id in unresolved:
                self.audit(agent_id, "action.unknown", action_id, {"error_code": "run_interrupted"})
                self.event(
                    agent_id,
                    run_id,
                    "action.completed",
                    {"action_id": action_id, "status": "unknown", "effect": "unknown"},
                )
        return len(unresolved)

    def recover(self) -> Record:
        self.delegation_recover()
        self.memory_semantic_recover()
        self.terminal_recover()
        recovered_credentials = self.credential_recover()
        self.delivery_recover()
        stale_commands = (
            self.connection.execute(
                select(s.runs)
                .join(s.owner_commands, s.owner_commands.c.run_id == s.runs.c.id)
                .where(s.owner_commands.c.boot_id != self.boot_id, s.runs.c.state == "queued")
            )
            .mappings()
            .all()
        )
        for run in stale_commands:
            self.transition(run["agent_id"], run["id"], "interrupted", "desktop_binding_expired")
        self.connection.execute(
            update(s.owner_commands).where(s.owner_commands.c.boot_id != self.boot_id).values(arguments=None)
        )
        # A committed claim is not an execution. Only a claim with no external
        # intent can safely return to pending after an exclusive process restart.
        claimed = (
            self.connection.execute(
                select(s.work_items)
                .join(s.runs, s.runs.c.id == s.work_items.c.run_id)
                .where(
                    s.work_items.c.kind == "turn",
                    s.work_items.c.state == "claimed",
                    s.runs.c.state == "queued",
                )
            )
            .mappings()
            .all()
        )
        for work in claimed:
            has_intent = any(
                self.connection.execute(
                    select(table.c.id)
                    .where(
                        table.c.run_id == work["run_id"],
                        table.c.state.in_(states),
                    )
                    .limit(1)
                ).first()
                for table, states in (
                    (s.model_calls, ("reserved", "unknown")),
                    (s.actions, ("executing", "unknown")),
                    (s.outbox, ("sending", "unknown")),
                )
            )
            if has_intent:
                self.transition(work["agent_id"], work["run_id"], "interrupted", "claim_has_external_intent")
                continue
            self.connection.execute(
                update(s.work_items)
                .where(s.work_items.c.id == work["id"])
                .values(
                    state="pending",
                    claimed_by=None,
                    claim_token=None,
                    lease_fence=None,
                )
            )
        interrupted = (
            self.connection.execute(
                select(s.runs).where(s.runs.c.state.in_(["running", "waiting_approval", "cancelling"]))
            )
            .mappings()
            .all()
        )
        resumed = 0
        for run in interrupted:
            if self.requeue_checkpoint(cast(Record, dict(run))):
                resumed += 1
                continue
            self.transition(run["agent_id"], run["id"], "interrupted", "process_interrupted")
            self.audit(run["agent_id"], "run.recovered", run["id"], {})
        unknown = self.connection.execute(
            update(s.actions)
            .where(s.actions.c.state == "executing")
            .values(state="unknown", effect="unknown", ended_at=self.clock.now())
        ).rowcount
        self.connection.execute(update(s.outbox).where(s.outbox.c.state == "sending").values(state="unknown"))
        self.connection.execute(
            update(s.model_calls).where(s.model_calls.c.state == "reserved").values(state="unknown")
        )
        self.connection.execute(
            update(s.summaries)
            .where(s.summaries.c.state == "building")
            .values(state="unknown", content="", error_code="process_interrupted")
        )
        self.connection.execute(
            update(s.document_index_builds)
            .where(s.document_index_builds.c.state == "building")
            .values(state="failed", error_code="process_interrupted", completed_at=self.clock.now())
        )
        self.cancel_approvals()
        self.connection.execute(update(s.leases).where(s.leases.c.state == "held").values(state="released"))
        return {
            "interrupted_runs": len(interrupted) - resumed,
            "resumed_runs": resumed,
            "unknown_actions": unknown,
            "recovered_credentials": recovered_credentials,
        }

    def query(self, kind: str, agent_id: str, *, after: int = 0, limit: int = 100, **filters: str) -> list[Record]:
        tables = {
            "agents": s.agents,
            "sessions": s.sessions,
            "runs": s.runs,
            "events": s.run_events,
            "actions": s.actions,
            "audit": s.audit,
            "memory": s.memory_entries,
            "work": s.work_items,
            "calls": s.model_calls,
            "outbox": s.outbox,
            "extensions": s.extensions,
            "schedules": s.schedules,
            "summaries": s.summaries,
        }
        if kind not in tables:
            raise BusinessError("invalid_query", "Unknown query", status=422)
        table = tables[kind]
        statement = select(table)
        if kind != "agents":
            self.settings(agent_id)
            statement = statement.where(table.c.agent_id == agent_id)
        if "deleted_at" in table.c:
            statement = statement.where(table.c.deleted_at.is_(None))
        if kind == "memory":
            statement = statement.where(table.c.kind.not_in(["profile", "resident"]))
        if kind == "runs":
            statement = statement.where(s.runs.c.id.not_in(select(s.owa_task_attempts.c.run_id)))
        if kind in {"sessions", "summaries"}:
            private_sessions = select(s.runs.c.session_id).join(s.owa_tasks, s.owa_tasks.c.run_id == s.runs.c.id)
            session = s.sessions.c.id if kind == "sessions" else s.summaries.c.session_id
            statement = statement.where(session.not_in(private_sessions))
        cursor = table.c.sequence if "sequence" in table.c else None
        if cursor is not None:
            statement = statement.where(cursor > after).order_by(cursor)
        else:
            statement = statement.order_by(table.c.created_at, table.c.id)
        for name, value in filters.items():
            if name not in {"run_id", "session_id", "state", "kind"} or name not in table.c:
                raise BusinessError("invalid_filter", "Unsupported query filter", status=422)
            statement = statement.where(table.c[name] == value)
        return [dict(row) for row in self.connection.execute(statement.limit(min(200, max(1, limit)))).mappings()]
