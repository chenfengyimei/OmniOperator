"""Context barriers invalidate derived views without deleting original conversations."""

from __future__ import annotations

from typing import cast

from sqlalchemy import delete, or_, select, update

from ...domain.contracts import BusinessError
from . import schema as s
from .delivery_repository import DeliveryRepositories


class RetentionRepositories(DeliveryRepositories):
    def assert_context(self, agent_id: str, run_id: str) -> None:
        self.assert_run_lease(agent_id, run_id)
        run = self._one(s.runs, agent_id, run_id)
        session = self._one(s.sessions, agent_id, str(run["session_id"]))
        if run.get("execution_kind") == "owner":
            command = (
                self.connection.execute(
                    select(s.owner_commands).where(
                        s.owner_commands.c.run_id == run_id, s.owner_commands.c.agent_id == agent_id
                    )
                )
                .mappings()
                .first()
            )
            if (
                not command
                or command["boot_id"] != self.boot_id
                or command["session_epoch"] != session["content_epoch"]
                or session["deleted_at"]
            ):
                raise BusinessError(
                    "context_invalidated", "Owner action authorization expired or its source was erased"
                )
        row = (
            self.connection.execute(
                select(s.context_snapshots)
                .where(s.context_snapshots.c.run_id == run_id, s.context_snapshots.c.agent_id == agent_id)
                .order_by(s.context_snapshots.c.ordinal.desc())
                .limit(1)
            )
            .mappings()
            .first()
        )
        if row and row["session_epoch"] != session["content_epoch"]:
            raise BusinessError("context_invalidated", "Source content was erased; submit a new turn")

    def _invalidate_sessions(self, agent_id: str, sessions: set[str]) -> set[str]:
        derived: set[str] = set()
        for session_id in sessions:
            session = self._one(s.sessions, agent_id, session_id)
            self.connection.execute(
                update(s.sessions)
                .where(s.sessions.c.id == session_id)
                .values(content_epoch=s.sessions.c.content_epoch + 1, context_floor_seq=session["next_seq"])
            )
            self.connection.execute(
                update(s.summaries)
                .where(s.summaries.c.session_id == session_id)
                .values(
                    content="", state="invalidated", error_code="source_erased", revision=s.summaries.c.revision + 1
                )
            )
            self.delivery_invalidate_session(agent_id, session_id)
            self.connection.execute(
                update(s.session_todos)
                .where(s.session_todos.c.agent_id == agent_id, s.session_todos.c.session_id == session_id)
                .values(
                    items=[],
                    revision=s.session_todos.c.revision + 1,
                    session_epoch=cast(int, session["content_epoch"]) + 1,
                    updated_at=self.clock.now(),
                )
            )
            self.connection.execute(
                update(s.owner_commands)
                .where(s.owner_commands.c.run_id.in_(select(s.runs.c.id).where(s.runs.c.session_id == session_id)))
                .values(arguments=None)
            )
            message_ids = select(s.messages.c.id).where(s.messages.c.session_id == session_id)
            action_ids = (
                select(s.actions.c.id)
                .join(s.runs, s.runs.c.id == s.actions.c.run_id)
                .where(s.runs.c.session_id == session_id)
            )
            derived.update(
                self.connection.execute(
                    select(s.source_anchors.c.memory_id).where(
                        s.source_anchors.c.agent_id == agent_id,
                        or_(
                            s.source_anchors.c.message_id.in_(message_ids), s.source_anchors.c.action_id.in_(action_ids)
                        ),
                    )
                ).scalars()
            )
            self.connection.execute(
                update(s.work_items)
                .where(
                    s.work_items.c.run_id.in_(select(s.runs.c.id).where(s.runs.c.session_id == session_id)),
                    s.work_items.c.kind.in_(["summary", "memory_review"]),
                    s.work_items.c.state.in_(["pending", "running"]),
                )
                .values(state="cancelled")
            )
            self.audit(agent_id, "context.invalidated", session_id, {"floor_seq": session["next_seq"]})
        return derived

    def _memory_sessions(self, agent_id: str, memory_id: str) -> set[str]:
        sources = select(s.source_anchors.c.message_id).where(s.source_anchors.c.memory_id == memory_id)
        sessions = set(
            self.connection.execute(select(s.messages.c.session_id).where(s.messages.c.id.in_(sources))).scalars()
        )
        sessions.update(
            self.connection.execute(
                select(s.runs.c.session_id)
                .join(s.context_snapshots, s.context_snapshots.c.run_id == s.runs.c.id)
                .join(s.context_memories, s.context_memories.c.snapshot_id == s.context_snapshots.c.id)
                .where(s.context_memories.c.memory_id == memory_id, s.context_memories.c.agent_id == agent_id)
            ).scalars()
        )
        sessions.update(
            self.connection.execute(
                select(s.runs.c.session_id)
                .join(s.actions, s.actions.c.run_id == s.runs.c.id)
                .join(s.source_anchors, s.source_anchors.c.action_id == s.actions.c.id)
                .where(s.source_anchors.c.agent_id == agent_id, s.source_anchors.c.memory_id == memory_id)
            ).scalars()
        )
        entry = self._one(s.memory_entries, agent_id, memory_id)
        if entry["session_id"]:
            sessions.add(str(entry["session_id"]))
        return sessions

    def erase_message_record(self, agent_id: str, message_id: str) -> tuple[str, set[str]]:
        message = self._one(s.messages, agent_id, message_id)
        if message["deleted_at"]:
            return str(message["session_id"]), set()
        derived = self._invalidate_sessions(agent_id, {str(message["session_id"])})
        self.connection.execute(
            update(s.messages).where(s.messages.c.id == message_id).values(content="", deleted_at=self.clock.now())
        )
        self.connection.execute(delete(s.message_parts).where(s.message_parts.c.message_id == message_id))
        self.audit(agent_id, "message.erased", message_id, {})
        return str(message["session_id"]), derived
