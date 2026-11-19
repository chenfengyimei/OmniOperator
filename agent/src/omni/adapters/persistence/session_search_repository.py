"""Bounded read-only history recall over the shared session/message store.

Every query is Agent-scoped, skips erased messages (deleted_at) and anything at/below the session
erasure floor (context_floor_seq), and hides delegation-child sessions from discovery/browse because
they are not the user's interactive history. No FTS index exists for messages, so discovery uses
bounded token LIKE matching grouped by session in the domain layer.
"""

from __future__ import annotations

from typing import cast

from sqlalchemy import and_, func, or_, select

from ...application.ports import Record
from ...domain.contracts import BusinessError
from . import schema as s
from .repository_base import RepositoryBase


class SessionSearchRepositories(RepositoryBase):
    def _agent_exists(self, agent_id: str) -> None:
        if not self.connection.execute(select(s.agents.c.id).where(s.agents.c.id == agent_id)).first():
            raise BusinessError("not_found", "Agent does not exist", status=404)

    def _live_session(self, agent_id: str, session_id: str) -> Record:
        session = self._one(s.sessions, agent_id, session_id)
        if session["deleted_at"]:
            raise BusinessError("session_deleted", "Session has been erased", status=410)
        return session

    def _hidden_sessions(self, agent_id: str) -> set[str]:
        rows = self.connection.execute(
            select(s.run_delegations.c.child_session_id).where(s.run_delegations.c.agent_id == agent_id)
        ).scalars()
        return {str(row) for row in rows}

    def _live_messages(self, agent_id: str, session_id: str, floor: int) -> list[Record]:
        rows = (
            self.connection.execute(
                select(s.messages)
                .where(
                    s.messages.c.agent_id == agent_id,
                    s.messages.c.session_id == session_id,
                    s.messages.c.deleted_at.is_(None),
                    s.messages.c.seq > floor,
                )
                .order_by(s.messages.c.seq)
            )
            .mappings()
            .all()
        )
        return [cast(Record, dict(row)) for row in rows]

    def session_search_discover(
        self,
        agent_id: str,
        tokens: tuple[str, ...],
        *,
        roles: tuple[str, ...],
        exclude: tuple[str, ...],
        current_session_id: str,
        scan_limit: int,
    ) -> list[Record]:
        self._agent_exists(agent_id)
        if not tokens:
            return []
        hidden = self._hidden_sessions(agent_id)
        skipped = hidden | set(exclude)
        if current_session_id:
            skipped.add(current_session_id)
        statement = (
            select(s.messages, s.sessions.c.title, s.sessions.c.created_at.label("session_created_at"))
            .join(
                s.sessions,
                and_(s.messages.c.session_id == s.sessions.c.id, s.messages.c.agent_id == s.sessions.c.agent_id),
            )
            .where(
                s.messages.c.agent_id == agent_id,
                s.messages.c.deleted_at.is_(None),
                s.sessions.c.deleted_at.is_(None),
                s.messages.c.seq > s.sessions.c.context_floor_seq,
                s.messages.c.role.in_(roles),
                or_(*[s.messages.c.content.contains(token, autoescape=True) for token in tokens]),
            )
            .order_by(s.sessions.c.created_at.desc(), s.messages.c.seq.asc())
            .limit(min(300, max(1, scan_limit)))
        )
        if skipped:
            statement = statement.where(s.sessions.c.id.not_in(skipped))
        return [cast(Record, dict(row)) for row in self.connection.execute(statement).mappings()]

    def session_search_scroll(self, agent_id: str, session_id: str, around_seq: int, window: int) -> Record:
        self._agent_exists(agent_id)
        session = self._live_session(agent_id, session_id)
        floor = cast(int, session["context_floor_seq"])
        anchor = (
            self.connection.execute(
                select(s.messages).where(
                    s.messages.c.agent_id == agent_id,
                    s.messages.c.session_id == session_id,
                    s.messages.c.seq == around_seq,
                )
            )
            .mappings()
            .first()
        )
        if anchor is None:
            raise BusinessError("not_found", "Anchor message is not in this session", status=404)
        if anchor["deleted_at"] or around_seq <= floor:
            raise BusinessError("context_invalidated", "Anchor was erased or predates an erasure boundary", status=410)
        low, high = around_seq - window, around_seq + window
        rows = (
            self.connection.execute(
                select(s.messages)
                .where(
                    s.messages.c.agent_id == agent_id,
                    s.messages.c.session_id == session_id,
                    s.messages.c.deleted_at.is_(None),
                    s.messages.c.seq > floor,
                    s.messages.c.seq >= low,
                    s.messages.c.seq <= high,
                )
                .order_by(s.messages.c.seq)
            )
            .mappings()
            .all()
        )

        def count(below: bool) -> int:
            predicate = s.messages.c.seq < low if below else s.messages.c.seq > high
            return self.connection.execute(
                select(func.count())
                .select_from(s.messages)
                .where(
                    s.messages.c.agent_id == agent_id,
                    s.messages.c.session_id == session_id,
                    s.messages.c.deleted_at.is_(None),
                    s.messages.c.seq > floor,
                    predicate,
                )
            ).scalar_one()

        return cast(
            Record,
            {
                "session_id": session_id,
                "title": session["title"],
                "created_at": session["created_at"],
                "around_seq": around_seq,
                "window": window,
                "messages_before": count(True),
                "messages_after": count(False),
                "messages": [cast(Record, dict(row)) for row in rows],
            },
        )

    def session_search_read(self, agent_id: str, session_id: str, head: int, tail: int) -> Record:
        self._agent_exists(agent_id)
        session = self._live_session(agent_id, session_id)
        floor = cast(int, session["context_floor_seq"])
        total = self.connection.execute(
            select(func.count())
            .select_from(s.messages)
            .where(
                s.messages.c.agent_id == agent_id,
                s.messages.c.session_id == session_id,
                s.messages.c.deleted_at.is_(None),
                s.messages.c.seq > floor,
            )
        ).scalar_one()
        truncated = total > head + tail
        if not truncated:
            rows = self._live_messages(agent_id, session_id, floor)[: head + tail]
        else:
            base = select(s.messages).where(
                s.messages.c.agent_id == agent_id,
                s.messages.c.session_id == session_id,
                s.messages.c.deleted_at.is_(None),
                s.messages.c.seq > floor,
            )
            head_rows = (
                [
                    cast(Record, dict(row))
                    for row in self.connection.execute(base.order_by(s.messages.c.seq).limit(head)).mappings()
                ]
                if head
                else []
            )
            tail_rows = (
                [
                    cast(Record, dict(row))
                    for row in self.connection.execute(base.order_by(s.messages.c.seq.desc()).limit(tail)).mappings()
                ]
                if tail
                else []
            )
            rows = head_rows + list(reversed(tail_rows))
        return cast(
            Record,
            {
                "session_id": session_id,
                "title": session["title"],
                "created_at": session["created_at"],
                "message_count": total,
                "truncated": truncated,
                "head": head,
                "tail": tail,
                "messages": rows,
            },
        )

    def session_search_browse(self, agent_id: str, limit: int, current_session_id: str) -> list[Record]:
        self._agent_exists(agent_id)
        hidden = self._hidden_sessions(agent_id)
        statement = (
            select(s.sessions)
            .where(s.sessions.c.agent_id == agent_id, s.sessions.c.deleted_at.is_(None))
            .order_by(s.sessions.c.created_at.desc())
            .limit(min(10, max(1, limit)))
        )
        if current_session_id:
            statement = statement.where(s.sessions.c.id != current_session_id)
        if hidden:
            statement = statement.where(s.sessions.c.id.not_in(hidden))
        results: list[Record] = []
        for session in self.connection.execute(statement).mappings():
            session_id = str(session["id"])
            floor = cast(int, session["context_floor_seq"])
            count = self.connection.execute(
                select(func.count())
                .select_from(s.messages)
                .where(
                    s.messages.c.agent_id == agent_id,
                    s.messages.c.session_id == session_id,
                    s.messages.c.deleted_at.is_(None),
                    s.messages.c.seq > floor,
                )
            ).scalar_one()
            preview_row = (
                self.connection.execute(
                    select(s.messages.c.content)
                    .where(
                        s.messages.c.agent_id == agent_id,
                        s.messages.c.session_id == session_id,
                        s.messages.c.deleted_at.is_(None),
                        s.messages.c.seq > floor,
                        s.messages.c.role.in_(("user", "assistant")),
                    )
                    .order_by(s.messages.c.seq)
                    .limit(1)
                )
                .scalars()
                .first()
            )
            results.append(
                {
                    "session_id": session_id,
                    "title": session["title"],
                    "created_at": session["created_at"],
                    "message_count": count,
                    "preview": str(preview_row) if preview_row is not None else "",
                }
            )
        return results
