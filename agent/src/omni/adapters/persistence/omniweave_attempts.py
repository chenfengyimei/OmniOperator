"""Read Worker attempt identities and allocate later Runs in the original Session."""

from typing import cast

from sqlalchemy import func, select, update
from sqlalchemy.sql.elements import ColumnElement
from sqlalchemy.sql.selectable import ScalarSelect

from ...application.ports import Record, UnitOfWork
from . import schema as s
from .repository_base import RepositoryBase


def task_for_run(repo: RepositoryBase, agent_id: str, run_id: str) -> Record | None:
    row = (
        repo.connection.execute(
            select(s.owa_tasks, s.owa_task_attempts.c.attempt_no)
            .join(s.owa_task_attempts, s.owa_task_attempts.c.task_id == s.owa_tasks.c.id)
            .where(s.owa_task_attempts.c.agent_id == agent_id, s.owa_task_attempts.c.run_id == run_id)
        )
        .mappings()
        .first()
    )
    return cast(Record, dict(row)) if row else None


def latest_run_id(task_id: str | ColumnElement[str]) -> ScalarSelect[str]:
    return (
        select(s.owa_task_attempts.c.run_id)
        .where(s.owa_task_attempts.c.task_id == task_id)
        .order_by(s.owa_task_attempts.c.attempt_no.desc())
        .limit(1)
        .scalar_subquery()
    )


def latest_goal_version(task_id: str | ColumnElement[str]) -> ScalarSelect[int]:
    return (
        select(s.owa_task_attempts.c.goal_version)
        .where(s.owa_task_attempts.c.task_id == task_id)
        .order_by(s.owa_task_attempts.c.attempt_no.desc())
        .limit(1)
        .scalar_subquery()
    )


class OmniWeaveAttemptRepositories(RepositoryBase):
    def _enqueue_owa_attempt(self, agent_id: str, task_id: str, previous: Record, text: str, goal_version: int) -> str:
        unit = cast(UnitOfWork, self)
        previous_id, session_id = str(previous["id"]), str(previous["session_id"])
        route = unit.run_route(agent_id, previous_id)
        seq = self.connection.execute(
            update(s.sessions)
            .where(s.sessions.c.id == session_id, s.sessions.c.agent_id == agent_id)
            .values(next_seq=s.sessions.c.next_seq + 1)
            .returning(s.sessions.c.next_seq)
        ).scalar_one()
        message = self._record(
            s.messages, {"session_id": session_id, "seq": seq, "role": "user", "content": text}, agent_id
        )
        captured = {
            key: previous[key]
            for key in (
                "session_id",
                "parent_id",
                "config_revision",
                "settings_revision_id",
                "model_revision_id",
                "model_permission_epoch",
                "policy_epoch",
                "skill_capture_epoch",
                "extension_capture_epoch",
            )
        }
        child = self._record(
            s.runs,
            {
                **captured,
                "input_message_id": message["id"],
                "state": "queued",
                "execution_kind": "model",
                "last_seq": 0,
                "error_code": "",
            },
            agent_id,
        )
        run_id = str(child["id"])
        self.connection.execute(update(s.messages).where(s.messages.c.id == message["id"]).values(run_id=run_id))
        self._record(
            s.work_items, {"run_id": run_id, "kind": "turn", "state": "pending", "payload": {}, "attempt": 0}, agent_id
        )
        self._record(
            s.run_routes,
            {
                "run_id": run_id,
                "policy_id": route.get("policy_id"),
                "requested_mode": "agent",
                "initial_mode": "agent",
                "source": "baseline",
                "reason": "delegated",
                "allow_escalation": False,
                "reservation_micro": route["reservation_micro"],
                "kernel": route.get("kernel", {}),
            },
            agent_id,
        )
        number = self.connection.execute(
            select(func.max(s.owa_task_attempts.c.attempt_no)).where(s.owa_task_attempts.c.task_id == task_id)
        ).scalar_one()
        self._record(
            s.owa_task_attempts,
            {"task_id": task_id, "run_id": run_id, "attempt_no": number + 1, "goal_version": goal_version},
            agent_id,
        )
        unit.event(agent_id, run_id, "run.queued", {"parent_run_id": previous["parent_id"]})
        return run_id
