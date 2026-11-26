"""Persist scoped OmniWeave Threads, Tickets and Triggers on the existing writer."""

from __future__ import annotations

from sqlalchemy import select

from ...domain.contracts import BusinessError
from ...domain.omniweave import OBSERVE_DECISION, OWNER_WEB_SOURCE, TRIGGER_VERSION
from . import schema as s
from .repository_base import RepositoryBase


class OmniWeaveRepositories(RepositoryBase):
    def omniweave_observe(self, agent_id: str, key: str, run_id: str, main_session_id: str, *, duplicate: bool) -> None:
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
