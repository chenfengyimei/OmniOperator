"""Parent-source authority and session invalidation closure for single-level delegated runs."""

from __future__ import annotations

from typing import cast

from sqlalchemy import select

from ...application.ports import Record, UnitOfWork
from ...domain.contracts import BusinessError
from . import schema as s
from .repository_base import RepositoryBase


def link(repo: RepositoryBase, agent_id: str, child_id: str) -> Record | None:
    row = (
        repo.connection.execute(
            select(s.run_delegations).where(
                s.run_delegations.c.agent_id == agent_id, s.run_delegations.c.child_run_id == child_id
            )
        )
        .mappings()
        .first()
    )
    return cast(Record, dict(row)) if row else None


def authorize(repo: RepositoryBase, agent_id: str, child_id: str) -> None:
    unit = cast(UnitOfWork, repo)
    row = unit.delegation_link(agent_id, child_id)
    if row is None:
        if unit.delegation_children(agent_id, child_id):
            enabled = repo.connection.execute(
                select(s.agents.c.enabled).where(s.agents.c.id == agent_id)
            ).scalar_one_or_none()
            if not enabled:
                raise BusinessError("agent_disabled", "Agent is disabled")
        return
    parent_id = str(row["parent_run_id"])
    parent = unit.get_run(agent_id, parent_id)
    child = unit.get_run(agent_id, child_id)
    session = repo.one(s.sessions, agent_id, str(row["parent_session_id"]))
    worker = unit.omniweave_private_scope(agent_id, "run_id", child_id)
    parent_states = ("running",)
    if worker:
        execution = unit.omniweave_router(agent_id, parent_id)
        if execution is None or execution["cancelled_at"] or not execution["plan_sha256"]:
            raise BusinessError("delegation_source_invalidated", "The owning Ticket no longer authorizes this Worker")
        parent_states = ("running", "waiting_approval", "succeeded")
    if (
        (not worker and row["boot_id"] != repo.boot_id)
        or child["parent_id"] != parent_id
        or child["session_id"] != row["child_session_id"]
        or any(
            child[key] != parent[key]
            for key in ("settings_revision_id", "model_revision_id", "model_permission_epoch", "policy_epoch")
        )
        or parent["state"] not in parent_states
        or parent["session_id"] != session["id"]
        or session["deleted_at"]
        or session["content_epoch"] != row["parent_epoch"]
    ):
        raise BusinessError("delegation_source_invalidated", "Parent run or its captured source is no longer active")
    enabled = repo.connection.execute(select(s.agents.c.enabled).where(s.agents.c.id == agent_id)).scalar_one_or_none()
    if not enabled:
        raise BusinessError("agent_disabled", "Agent is disabled")
    if parent["state"] != "succeeded":
        unit.assert_run_lease(agent_id, parent_id)
    unit.assert_model_authorized(agent_id, parent_id)
    if parent["policy_epoch"] != unit.settings(agent_id)["policy_revision"]:
        raise BusinessError("policy_changed", "Parent action policy changed during delegation")


def related_sessions(repo: RepositoryBase, agent_id: str, sessions: set[str]) -> set[str]:
    # Depth is one and at most four children belong to a parent. Two passes
    # include a child's parent first, then all siblings of that same parent.
    result = set(sessions)
    for _ in range(2):
        rows = repo.connection.execute(
            select(s.run_delegations.c.parent_session_id, s.run_delegations.c.child_session_id).where(
                s.run_delegations.c.agent_id == agent_id,
                s.run_delegations.c.parent_session_id.in_(result) | s.run_delegations.c.child_session_id.in_(result),
            )
        )
        for parent, child in rows:
            result.update((parent, child))
    return result
