"""Read the execution and reconciliation scope of a versioned Ticket."""

from typing import Literal

from sqlalchemy import func, select, union_all
from sqlalchemy.sql.elements import ColumnElement
from sqlalchemy.sql.selectable import CompoundSelect

from ...domain.action_review import TERMINAL_RUNS
from ...domain.omniweave import JOB_LIVE_STATES
from . import schema as s
from .action_predicates import unresolved_action
from .repository_base import RepositoryBase


def current_goal() -> ColumnElement[bool]:
    tickets = s.owa_tickets
    later = tickets.alias("later_goal")
    root_id = func.coalesce(tickets.c.root_ticket_id, tickets.c.id)
    return (
        ~select(later.c.id)
        .where(later.c.root_ticket_id == root_id, later.c.goal_version > tickets.c.goal_version)
        .exists()
    )


def goal_runs(agent_id: str, root_ticket_id: str, *, before_version: int | None = None) -> CompoundSelect[tuple[str]]:
    tickets = s.owa_tickets
    versions = select(tickets.c.id, tickets.c.run_id).where(
        tickets.c.agent_id == agent_id, (tickets.c.id == root_ticket_id) | (tickets.c.root_ticket_id == root_ticket_id)
    )
    if before_version is not None:
        versions = versions.where(tickets.c.goal_version < before_version)
    scoped = versions.cte()
    return union_all(
        select(scoped.c.run_id),
        select(s.owa_task_attempts.c.run_id)
        .join(s.owa_tasks, s.owa_tasks.c.id == s.owa_task_attempts.c.task_id)
        .join(scoped, scoped.c.id == s.owa_tasks.c.ticket_id),
    )


def goal_job_runs(
    repo: RepositoryBase, agent_id: str, root_ticket_id: str, *, before_version: int | None = None
) -> list[str]:
    """Live managed-job runs inside the ticket family.

    Goal revisions cancel worker runs through delegation children, but a managed
    job executes on its own detached Run; without this projection the revision
    would leave a superseded job executing against an old goal version.
    """
    tickets = s.owa_tickets
    versions = select(tickets.c.id).where(
        tickets.c.agent_id == agent_id, (tickets.c.id == root_ticket_id) | (tickets.c.root_ticket_id == root_ticket_id)
    )
    if before_version is not None:
        versions = versions.where(tickets.c.goal_version < before_version)
    scoped = versions.cte()
    rows = repo.connection.execute(
        select(s.owa_jobs.c.run_id)
        .join(s.owa_tasks, s.owa_tasks.c.id == s.owa_jobs.c.task_id)
        .join(scoped, scoped.c.id == s.owa_tasks.c.ticket_id)
        .where(s.owa_jobs.c.run_id.is_not(None), s.owa_jobs.c.state.in_(JOB_LIVE_STATES))
    ).all()
    return [str(row[0]) for row in rows]


def prior_goal_blocker(
    repo: RepositoryBase, agent_id: str, root_ticket_id: str, version: int
) -> Literal["draining", "needs_review"] | None:
    if version == 1:
        return None
    return goal_blocker(repo, goal_runs(agent_id, root_ticket_id, before_version=version))


def goal_blocker(
    repo: RepositoryBase, family: CompoundSelect[tuple[str]]
) -> Literal["draining", "needs_review"] | None:
    draining = (
        select(s.runs.c.id).where(s.runs.c.id.in_(family), s.runs.c.state.not_in(TERMINAL_RUNS)).exists(),
        select(s.leases.c.resource).where(s.leases.c.run_id.in_(family), s.leases.c.state == "held").exists(),
    )
    unsettled = (
        select(s.actions.c.id).where(s.actions.c.run_id.in_(family), unresolved_action()).exists(),
        select(s.model_calls.c.id)
        .where(s.model_calls.c.run_id.in_(family), s.model_calls.c.state.in_(("reserved", "unknown")))
        .exists(),
        select(s.outbox.c.id)
        .where(
            s.outbox.c.run_id.in_(family),
            s.outbox.c.state.in_(
                ("awaiting_approval", "approved", "claimed", "sending", "retry_wait", "unknown", "blocked")
            ),
        )
        .exists(),
    )
    live, leases, *review = repo.connection.execute(select(*draining, *unsettled)).one()
    return "draining" if live or leases else "needs_review" if any(review) else None
