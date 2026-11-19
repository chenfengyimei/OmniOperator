"""Structured clarification requests that pause the current run for the owner and resume the same run.

Clarify reuses the ``waiting_approval`` pause state (the run coroutine stays alive, its work row keeps
running and the session lease keeps renewing) rather than adding a parallel state, so cancel, lease and
boot-recovery semantics are unchanged. Resolution transitions the SAME run back to running; a queued
turn is never a continuation. Requests are immutable evidence with a one-way state machine.
"""

from __future__ import annotations

import datetime as dt
from typing import cast

from sqlalchemy import select, update

from ...application.ports import Record, UnitOfWork
from ...domain.contracts import JSON, BusinessError
from . import schema as s
from .repository_base import RepositoryBase


class ClarifyRepositories(RepositoryBase):
    def clarify_request(
        self, agent_id: str, run_id: str, questions: list[dict[str, JSON]], policy_revision: int, timeout_s: float
    ) -> Record:
        uow = cast(UnitOfWork, self)
        run = uow.get_run(agent_id, run_id)
        if run["state"] != "running":
            raise BusinessError("run_not_running", "Run no longer accepts clarification", status=409)
        if uow.settings(agent_id)["policy_revision"] != policy_revision:
            raise BusinessError("policy_changed", "Clarification belongs to an older policy", status=409)
        expires = (dt.datetime.fromisoformat(self.clock.now()) + dt.timedelta(seconds=timeout_s)).isoformat(
            timespec="microseconds"
        )
        request = self._record(
            s.clarifications,
            {
                "run_id": run_id,
                "questions": questions,
                "state": "pending",
                "outcome": None,
                "notice": None,
                "revision": 1,
                "policy_revision": policy_revision,
                "expires_at": expires,
                "resolved_at": None,
            },
            agent_id,
        )
        uow.transition(agent_id, run_id, "waiting_approval")
        self.audit(agent_id, "clarify.requested", cast(str, request["id"]), {"run_id": run_id})
        return request

    def clarification(self, agent_id: str, clarification_id: str) -> Record:
        return self._one(s.clarifications, agent_id, clarification_id)

    def _resolve(
        self,
        agent_id: str,
        clarification_id: str,
        *,
        state: str,
        answers: dict[str, JSON] | None,
        outcome: str,
        notice: str | None,
        revision: int | None,
        policy_revision: int | None,
    ) -> Record:
        uow = cast(UnitOfWork, self)
        request = self._one(s.clarifications, agent_id, clarification_id)
        if request["state"] != "pending":
            raise BusinessError("clarify_resolved", "Clarification is no longer pending", status=409)
        run_id = cast(str, request["run_id"])
        run = uow.get_run(agent_id, run_id)
        if run["state"] != "waiting_approval":
            raise BusinessError("run_not_waiting", "Run is not waiting for clarification", status=409)
        if revision is not None:
            if cast(int, request["revision"]) != revision:
                raise BusinessError("revision_conflict", "Clarification revision changed", status=409)
            if cast(str, request["expires_at"]) <= self.clock.now():
                raise BusinessError("clarify_expired", "Clarification timed out", status=410)
        if policy_revision is not None:
            if uow.settings(agent_id)["policy_revision"] != policy_revision:
                raise BusinessError("policy_changed", "Clarification belongs to an older policy", status=409)
            if cast(int, request["policy_revision"]) != policy_revision:
                raise BusinessError("policy_changed", "Clarification belongs to an older policy", status=409)
        self.connection.execute(
            update(s.clarifications)
            .where(s.clarifications.c.id == clarification_id)
            .values(
                state=state,
                answers=answers,
                outcome=outcome,
                notice=notice,
                resolved_at=self.clock.now(),
                revision=s.clarifications.c.revision + 1,
            )
        )
        uow.transition(agent_id, run_id, "running")
        self.audit(agent_id, "clarify." + outcome, clarification_id, {"run_id": run_id})
        return self._one(s.clarifications, agent_id, clarification_id)

    def clarify_respond(
        self, agent_id: str, clarification_id: str, answers: dict[str, JSON], revision: int, policy_revision: int
    ) -> Record:
        return self._resolve(
            agent_id,
            clarification_id,
            state="answered",
            answers=answers,
            outcome="submitted",
            notice=None,
            revision=revision,
            policy_revision=policy_revision,
        )

    def clarify_cancel(self, agent_id: str, clarification_id: str, revision: int, notice: str = "") -> Record:
        return self._resolve(
            agent_id,
            clarification_id,
            state="cancelled",
            answers=None,
            outcome="cancelled",
            notice=notice or "Owner cancelled the clarification.",
            revision=revision,
            policy_revision=None,
        )

    def clarify_timeout(self, agent_id: str, clarification_id: str) -> Record:
        return self._resolve(
            agent_id,
            clarification_id,
            state="timed_out",
            answers=None,
            outcome="timed_out",
            notice="Clarification timed out before the owner responded.",
            revision=None,
            policy_revision=None,
        )

    def pending_clarifications(self, agent_id: str) -> list[Record]:
        rows = (
            self.connection.execute(
                select(s.clarifications)
                .where(
                    s.clarifications.c.agent_id == agent_id,
                    s.clarifications.c.state == "pending",
                    s.clarifications.c.expires_at > self.clock.now(),
                )
                .order_by(s.clarifications.c.created_at)
                .limit(100)
            )
            .mappings()
            .all()
        )
        return [cast(Record, dict(row)) for row in rows]

    def cancel_run_clarifications(self, agent_id: str, run_id: str) -> None:
        pending = (
            self.connection.execute(
                select(s.clarifications.c.id).where(
                    s.clarifications.c.agent_id == agent_id,
                    s.clarifications.c.run_id == run_id,
                    s.clarifications.c.state == "pending",
                )
            )
            .scalars()
            .all()
        )
        for clarification_id in pending:
            self.connection.execute(
                update(s.clarifications)
                .where(s.clarifications.c.id == clarification_id)
                .values(
                    state="cancelled",
                    outcome="cancelled",
                    notice="Run was cancelled.",
                    resolved_at=self.clock.now(),
                    revision=s.clarifications.c.revision + 1,
                )
            )
