"""Transaction-scoped repositories. No connection creation, DDL or external calls."""

from __future__ import annotations

import datetime as dt
import hashlib
import json
import re
from collections.abc import Mapping
from dataclasses import asdict
from typing import Any

from sqlalchemy import Connection, and_, func, insert, select, text, update

from ...application.ports import Clock, Ids, Record
from ...domain.contracts import ActionResult, BusinessError, ModelReply, RunState, ToolSpec, check_transition
from ...domain.settings import validate_settings
from . import schema as s


class Repositories:
    def __init__(self, connection: Connection, clock: Clock, ids: Ids) -> None:
        self.connection, self.clock, self.ids = connection, clock, ids

    def _record(self, table, values: Mapping[str, Any], agent_id: str | None = None) -> dict:
        row = {"id": self.ids.new(), "created_at": self.clock.now(), **values}
        if agent_id is not None:
            row["agent_id"] = agent_id
        self.connection.execute(insert(table).values(**row))
        return row

    def _one(self, table, agent_id: str, entity_id: str) -> dict:
        row = (
            self.connection.execute(select(table).where(table.c.id == entity_id, table.c.agent_id == agent_id))
            .mappings()
            .first()
        )
        if row is None:
            raise BusinessError("not_found", "Resource does not exist in this Agent space", status=404)
        return dict(row)

    def initialize(self) -> dict:
        current = self.connection.execute(select(s.instances)).mappings().first()
        if current:
            return dict(current)
        owner = self._record(s.principals, {"role": "owner", "name": "Owner"})
        instance = self._record(s.instances, {"slot": 1, "owner_id": owner["id"]})
        self.create_agent("Default")
        return instance

    def probe_write(self) -> None:
        self.connection.execute(update(s.instances).where(s.instances.c.slot == 1).values(slot=1))

    def create_agent(self, name: str) -> dict:
        if not name.strip() or len(name) > 100:
            raise BusinessError("invalid_agent_name", "Agent name must contain 1 to 100 characters", status=422)
        agent = self._record(s.agents, {"name": name, "enabled": True, "revision": 1})
        self.connection.execute(
            insert(s.settings).values(
                agent_id=agent["id"],
                revision=1,
                value={
                    "persona": "You are a helpful personal assistant.",
                    "model": "",
                    "workspace": "",
                    "daily_budget_micro": 1_000_000,
                    "call_reservation_micro": 10_000,
                    "max_steps": 12,
                    "credential_ref": "",
                    "model_base_url": "",
                    "disabled_tools": [],
                },
                updated_at=self.clock.now(),
            )
        )
        return agent

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

    def settings(self, agent_id: str) -> dict:
        row = self.connection.execute(select(s.settings).where(s.settings.c.agent_id == agent_id)).mappings().first()
        if row is None:
            raise BusinessError("agent_not_found", "Agent does not exist", status=404)
        return {"revision": row["revision"], "value": row["value"]}

    def settings_put(self, agent_id: str, value: Record, revision: int) -> dict:
        def check_no_secrets(data):
            if isinstance(data, dict):
                for key, child in data.items():
                    if key.lower() in {
                        "api_key",
                        "access_token",
                        "refresh_token",
                        "password",
                        "cookie",
                        "authorization",
                    }:
                        raise BusinessError(
                            "credential_reference_required",
                            "Store credentials through the credential endpoint",
                            status=422,
                        )
                    check_no_secrets(child)
            elif isinstance(data, list):
                for child in data:
                    check_no_secrets(child)

        check_no_secrets(value)
        validate_settings(value)
        self.settings(agent_id)
        changed = self.connection.execute(
            update(s.settings)
            .where(s.settings.c.agent_id == agent_id, s.settings.c.revision == revision)
            .values(value=value, revision=revision + 1, updated_at=self.clock.now())
        ).rowcount
        if changed != 1:
            raise BusinessError("revision_conflict", "Settings changed; reload before saving")
        self.audit(agent_id, "settings.changed", None, {"revision": revision + 1})
        return {"revision": revision + 1, "value": value}

    def audit(self, agent_id: str, kind: str, operation_id: str | None, data: dict) -> str:
        return self._record(s.audit, {"kind": kind, "operation_id": operation_id, "data": data}, agent_id)["id"]

    def _message(self, agent_id: str, session_id: str, role: str, text: str) -> dict:
        session = self._one(s.sessions, agent_id, session_id)
        if session["deleted_at"]:
            raise BusinessError("session_deleted", "Session has been deleted")
        seq = session["next_seq"] + 1
        self.connection.execute(update(s.sessions).where(s.sessions.c.id == session_id).values(next_seq=seq))
        return self._record(s.messages, {"session_id": session_id, "seq": seq, "role": role, "content": text}, agent_id)

    def accept_turn(self, agent_id: str, session_id: str | None, key: str, text: str, config: Record) -> dict:
        request_fingerprint = hashlib.sha256(
            json.dumps({"session_id": session_id, "text": text}, sort_keys=True).encode()
        ).hexdigest()
        prior = (
            self.connection.execute(
                select(s.inbox).where(s.inbox.c.agent_id == agent_id, s.inbox.c.channel == "chat", s.inbox.c.key == key)
            )
            .mappings()
            .first()
        )
        if prior:
            if prior["request_fingerprint"] != request_fingerprint:
                raise BusinessError("idempotency_conflict", "This key already identifies a different request")
            run = self.get_run(agent_id, prior["run_id"])
            return {"run_id": run["id"], "session_id": run["session_id"], "duplicate": True}
        if not key or len(key) > 200 or not text or len(text) > 200_000:
            raise BusinessError("invalid_turn", "A bounded message and idempotency key are required", status=422)
        settings = self.settings(agent_id)
        if session_id is None:
            session_id = self._record(s.sessions, {"title": text[:80], "revision": 1, "next_seq": 0}, agent_id)["id"]
        unfinished = self.connection.execute(
            select(func.count())
            .select_from(s.runs)
            .where(s.runs.c.state.in_(["queued", "running", "waiting_approval", "cancelling"]))
        ).scalar_one()
        queued = self.connection.execute(
            select(func.count()).select_from(s.runs).where(s.runs.c.state == "queued")
        ).scalar_one()
        if unfinished >= 68 or queued >= 64:
            raise BusinessError("queue_full", "Execution queue is full", retryable=True, status=429)
        assert session_id is not None
        message = self._message(agent_id, session_id, "user", text)
        run = self._record(
            s.runs,
            {
                "session_id": session_id,
                "state": "queued",
                "config_revision": settings["revision"],
                "input_message_id": message["id"],
                "config_snapshot": settings["value"],
                "last_seq": 0,
                "error_code": "",
            },
            agent_id,
        )
        self._record(
            s.inbox,
            {"channel": "chat", "key": key, "run_id": run["id"], "request_fingerprint": request_fingerprint},
            agent_id,
        )
        self.connection.execute(update(s.messages).where(s.messages.c.id == message["id"]).values(run_id=run["id"]))
        self._record(
            s.work_items,
            {"run_id": run["id"], "kind": "turn", "state": "pending", "payload": {}, "attempt": 0},
            agent_id,
        )
        self.event(agent_id, run["id"], "run.queued", {})
        return {"run_id": run["id"], "session_id": session_id, "duplicate": False}

    def get_run(self, agent_id: str, run_id: str) -> dict:
        return self._one(s.runs, agent_id, run_id)

    def transition(self, agent_id: str, run_id: str, state: str, error_code: str = "") -> None:
        run = self.get_run(agent_id, run_id)
        target = RunState(state)
        check_transition(run["state"], target)
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
        self.event(agent_id, run_id, "run." + state, {"error_code": error_code})

    def event(self, agent_id: str, run_id: str, kind: str, data: dict) -> int:
        run = self.get_run(agent_id, run_id)
        seq = run["last_seq"] + 1
        self.connection.execute(update(s.runs).where(s.runs.c.id == run_id).values(last_seq=seq))
        self.connection.execute(
            insert(s.run_events).values(
                agent_id=agent_id, run_id=run_id, seq=seq, created_at=self.clock.now(), kind=kind, data=data
            )
        )
        return seq

    def messages(self, agent_id: str, session_id: str, limit: int = 50) -> list[dict]:
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

    def context_messages(self, agent_id: str, run_id: str) -> list[dict]:
        run = self.get_run(agent_id, run_id)
        rows = (
            self.connection.execute(
                select(s.messages)
                .join(s.runs, s.runs.c.id == s.messages.c.run_id)
                .where(
                    s.messages.c.agent_id == agent_id,
                    s.messages.c.session_id == run["session_id"],
                    s.messages.c.deleted_at.is_(None),
                    (s.runs.c.id == run_id)
                    | ((s.runs.c.created_at < run["created_at"]) & s.runs.c.ended_at.is_not(None)),
                )
                .order_by(s.runs.c.created_at.desc(), s.messages.c.seq.desc())
                .limit(100)
            )
            .mappings()
            .all()
        )
        return [dict(row) for row in reversed(rows)]

    def snapshot(self, agent_id: str, run_id: str, message_ids: list[str], memory_ids: list[str]) -> str:
        run = self.get_run(agent_id, run_id)
        for message_id in message_ids:
            message = self._one(s.messages, agent_id, message_id)
            if message["session_id"] != run["session_id"]:
                raise BusinessError("invalid_context", "Context message belongs to another session")
        for memory_id in memory_ids:
            self._one(s.memory_entries, agent_id, memory_id)
        return self._record(
            s.context_snapshots, {"run_id": run_id, "message_ids": message_ids, "memory_ids": memory_ids}, agent_id
        )["id"]

    def finish(self, agent_id: str, run_id: str, text: str, delivery: Record | None = None) -> None:
        run = self.get_run(agent_id, run_id)
        check_transition(run["state"], RunState.SUCCEEDED)
        message = self._message(agent_id, run["session_id"], "assistant", text)
        self.connection.execute(update(s.messages).where(s.messages.c.id == message["id"]).values(run_id=run_id))
        if delivery:
            self._record(
                s.outbox,
                {
                    "run_id": run_id,
                    "channel": delivery["channel"],
                    "target": delivery["target"],
                    "state": "pending",
                    "message_id": message["id"],
                },
                agent_id,
            )
        self.transition(agent_id, run_id, "succeeded")
        self.audit(agent_id, "run.completed", run_id, {"message_id": message["id"]})

    def reserve_call(self, agent_id: str, run_id: str, amount: int, limit: int, model: str) -> str:
        self.get_run(agent_id, run_id)
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
        return self._record(
            s.model_calls,
            {"run_id": run_id, "model": model, "day": day, "state": "reserved", "reserved_micro": amount},
            agent_id,
        )["id"]

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
                ended_at=self.clock.now(),
            )
        )

    def prepare_action(self, agent_id: str, run_id: str, spec: ToolSpec, arguments: Record, policy: int) -> dict:
        run = self.get_run(agent_id, run_id)
        if run["state"] != "running":
            raise BusinessError("run_not_running", "Run no longer accepts actions")
        current = self.settings(agent_id)
        if current["revision"] != policy or spec.name in current["value"].get("disabled_tools", []):
            raise BusinessError("policy_changed", "Action policy changed; submit a new action")
        digest = hashlib.sha256(
            json.dumps(arguments, sort_keys=True, separators=(",", ":"), ensure_ascii=False).encode()
        ).hexdigest()
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
        if spec.writes:
            expires = (dt.datetime.fromisoformat(self.clock.now()) + dt.timedelta(seconds=180)).isoformat(
                timespec="microseconds"
            )
            self._record(s.approvals, {"action_id": action["id"], "state": "pending", "expires_at": expires}, agent_id)
            self.transition(agent_id, run_id, "waiting_approval")
        self.audit(
            agent_id,
            "action.prepared",
            action["id"],
            {"tool": spec.name, "arguments_sha256": digest, "policy_revision": policy},
        )
        self.event(
            agent_id,
            run_id,
            "action.prepared",
            {"action_id": action["id"], "tool": spec.name, "requires_approval": spec.writes},
        )
        return action

    def pending_approvals(self, agent_id: str) -> list[dict]:
        rows = self.connection.execute(
            select(s.approvals, s.actions.c.tool, s.actions.c.arguments, s.actions.c.run_id)
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
        action = self._one(s.actions, agent_id, approval["action_id"])
        if approval["state"] != "pending" or approval["expires_at"] <= self.clock.now() or action["state"] != "pending":
            raise BusinessError("approval_expired", "Approval is no longer pending")
        if self.settings(agent_id)["revision"] != policy or action["policy_revision"] != policy:
            raise BusinessError("policy_changed", "Approval belongs to an older policy")
        if self.get_run(agent_id, action["run_id"])["state"] != "waiting_approval":
            raise BusinessError("run_not_waiting", "Run no longer accepts approval")
        state = "approved" if allowed else "denied"
        self.connection.execute(
            update(s.approvals).where(s.approvals.c.id == approval_id).values(state=state, resolved_at=self.clock.now())
        )
        self.connection.execute(update(s.actions).where(s.actions.c.id == action["id"]).values(state=state))
        self.audit(agent_id, "approval." + state, action["id"], {"policy_revision": policy})
        self.transition(agent_id, action["run_id"], "running")

    def authorize_action(self, agent_id: str, action_id: str) -> dict:
        action = self._one(s.actions, agent_id, action_id)
        run = self.get_run(agent_id, action["run_id"])
        current = self.settings(agent_id)
        if run["state"] != "running" or action["state"] != "approved":
            raise BusinessError("action_not_authorized", "Action is not authorized")
        if action["policy_revision"] != current["revision"] or action["tool"] in current["value"].get(
            "disabled_tools", []
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
        self.connection.execute(update(s.actions).where(s.actions.c.id == action_id).values(state="executing"))
        self.audit(agent_id, "action.executing", action_id, {"tool": action["tool"]})
        return action

    def settle_action(self, agent_id: str, action_id: str, result: ActionResult) -> None:
        action = self._one(s.actions, agent_id, action_id)
        if action["state"] != "executing":
            raise BusinessError("action_already_settled", "Action is not executing")
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
        self.event(
            agent_id,
            action["run_id"],
            "action.completed",
            {"action_id": action_id, "status": result.status.value, "effect": result.effect.value},
        )

    def action(self, agent_id: str, action_id: str) -> dict:
        return self._one(s.actions, agent_id, action_id)

    def cancel(self, agent_id: str, run_id: str) -> None:
        run = self.get_run(agent_id, run_id)
        if run["state"] in {"succeeded", "failed", "cancelled", "interrupted", "cancelling"}:
            return
        target = "cancelled" if run["state"] == "queued" else "cancelling"
        self.transition(agent_id, run_id, target)
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
        self.audit(agent_id, "run.cancel_requested", run_id, {})

    def cancel_approvals(self) -> None:
        self.connection.execute(update(s.approvals).where(s.approvals.c.state == "pending").values(state="cancelled"))
        self.connection.execute(
            update(s.actions).where(s.actions.c.state.in_(["pending", "approved"])).values(state="cancelled")
        )

    def close_run_actions(self, agent_id: str, run_id: str) -> None:
        self.get_run(agent_id, run_id)
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

    def recover(self) -> dict:
        interrupted = (
            self.connection.execute(
                select(s.runs).where(s.runs.c.state.in_(["running", "waiting_approval", "cancelling"]))
            )
            .mappings()
            .all()
        )
        for run in interrupted:
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
        self.cancel_approvals()
        return {"interrupted_runs": len(interrupted), "unknown_actions": unknown}

    def remember(
        self, agent_id: str, content: str, *, kind: str = "fact", message_id: str | None = None, quote: str = ""
    ) -> dict:
        if not content.strip() or len(content) > 20_000:
            raise BusinessError("invalid_memory", "Memory must contain bounded text", status=422)
        source = None
        if message_id:
            message = self._one(s.messages, agent_id, message_id)
            if message["deleted_at"] or not quote or quote not in message["content"]:
                raise BusinessError("invalid_evidence", "Evidence must quote a live source message", status=422)
            source = {"message_id": message_id}
        else:
            source = {"manual_audit_id": self.audit(agent_id, "memory.authored", None, {})}
        entry = self._record(s.memory_entries, {"kind": kind, "content": content, "revision": 1}, agent_id)
        self._record(s.source_anchors, {"memory_id": entry["id"], "quote": quote, **source}, agent_id)
        self._record(s.memory_revisions, {"memory_id": entry["id"], "revision": 1, "content": content}, agent_id)
        return entry

    def forget(self, agent_id: str, memory_id: str) -> None:
        entry = self._one(s.memory_entries, agent_id, memory_id)
        if entry["deleted_at"]:
            return
        self.connection.execute(
            update(s.memory_entries)
            .where(s.memory_entries.c.id == memory_id)
            .values(content="", deleted_at=self.clock.now(), revision=entry["revision"] + 1)
        )
        self.connection.execute(
            update(s.memory_revisions).where(s.memory_revisions.c.memory_id == memory_id).values(content="")
        )
        self.connection.execute(
            update(s.source_anchors).where(s.source_anchors.c.memory_id == memory_id).values(quote="")
        )
        self._record(s.tombstones, {"memory_id": memory_id, "reason": "owner_requested"}, agent_id)
        self.audit(agent_id, "memory.forgotten", memory_id, {})

    def search_memory(self, agent_id: str, query: str, limit: int = 20) -> list[dict]:
        self.settings(agent_id)
        if not query.strip():
            return []
        tokens = re.findall(r"[^\W_]+", query, flags=re.UNICODE)[:20]
        if tokens:
            expression = " OR ".join('"' + token.replace('"', '""') + '"' for token in tokens)
            matches = (
                self.connection.execute(
                    text(
                        "SELECT m.* FROM memory_entries m JOIN memory_fts ON m.rowid=memory_fts.rowid "
                        "WHERE memory_fts MATCH :expression AND m.agent_id=:agent AND m.deleted_at IS NULL "
                        "ORDER BY bm25(memory_fts) LIMIT :limit"
                    ),
                    {"expression": expression, "agent": agent_id, "limit": min(100, max(1, limit))},
                )
                .mappings()
                .all()
            )
            if matches:
                return [dict(row) for row in matches]
        statement = (
            select(s.memory_entries)
            .where(
                s.memory_entries.c.agent_id == agent_id,
                s.memory_entries.c.deleted_at.is_(None),
                s.memory_entries.c.content.contains(query, autoescape=True),
            )
            .limit(min(100, max(1, limit)))
        )
        return [dict(row) for row in self.connection.execute(statement).mappings()]

    def query(self, kind: str, agent_id: str, *, after: int = 0, limit: int = 100, **filters: str) -> list[dict]:
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
            "presets": s.presets,
            "extensions": s.extensions,
            "schedules": s.schedules,
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
