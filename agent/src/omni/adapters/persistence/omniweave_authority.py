"""Check immutable OmniWeave authority against the live V2 execution fences."""

from typing import cast

from sqlalchemy import select

from ...application.ports import Record, UnitOfWork
from ...domain.contracts import BusinessError, ToolSpec
from ...domain.omniweave import OWA_PLAN, OWA_STATUS, WORKER_PROFILES, mandate_tool
from . import schema as s
from .repository_base import RepositoryBase


def mandate(repo: RepositoryBase, agent_id: str, run_id: str) -> Record | None:
    row = (
        repo.connection.execute(
            select(s.owa_mandates)
            .where(s.owa_mandates.c.agent_id == agent_id, s.owa_mandates.c.run_id == run_id)
            .order_by(s.owa_mandates.c.goal_version.desc())
            .limit(1)
        )
        .mappings()
        .first()
    )
    return cast(Record, dict(row)) if row else None


def authorize_mandate(
    repo: RepositoryBase, agent_id: str, run_id: str, execution: Record, delegation: Record | None = None
) -> None:
    if not execution["mandate_required"]:
        return  # Previously admitted work still uses its captured V2 authority.
    captured = mandate(repo, agent_id, run_id)
    if captured is None or captured["ticket_id"] != execution["ticket_id"]:
        raise BusinessError("owa_mandate_missing", "This Run has no scoped OmniWeave Mandate")
    if str(captured["expires_at"]) <= repo.clock.now():
        raise BusinessError("owa_mandate_expired", "OmniWeave task authorization expired")
    current = cast(UnitOfWork, repo).settings(agent_id)
    identity = repo.connection.execute(
        select(s.instances.c.owner_id, s.history_identity.c.epoch)
        .select_from(s.instances.join(s.history_identity, s.history_identity.c.slot == s.instances.c.slot))
        .where(s.instances.c.slot == 1)
    ).one()
    session = repo.one(s.sessions, agent_id, str(captured["main_session_id"]))
    if (
        execution["cancelled_at"] is not None
        or captured["policy_epoch"] != current["policy_revision"]
        or (captured["owner_id"], captured["history_epoch"]) != tuple(identity)
        or session["deleted_at"] is not None
        or captured["session_epoch"] != session["content_epoch"]
    ):
        raise BusinessError("owa_mandate_revoked", "OmniWeave authority no longer matches the owner, policy or source")
    if delegation is not None:
        profile = WORKER_PROFILES.get(str(captured["profile"]))
        grants = cast(dict[str, Record], captured["tools"])
        if (
            profile is None
            or captured["profile_version"] != profile.version
            or set(grants) - set(profile.tools)
            or {name: grant["sha256"] for name, grant in grants.items()} != delegation["tools"]
        ):
            raise BusinessError("owa_profile_changed", "The captured Worker Profile or capability set has changed")


def assert_mandate_action(repo: RepositoryBase, agent_id: str, run_id: str, spec: ToolSpec) -> None:
    captured = mandate(repo, agent_id, run_id)
    if captured is None:
        return
    # Observation carries provenance but must leave its ordinary Web Run alone.
    unit = cast(UnitOfWork, repo)
    if captured["task_id"] is None:
        if unit.omniweave_router(agent_id, run_id) is None:
            return
        allowed = spec in (OWA_PLAN, OWA_STATUS)
    else:
        allowed = cast(Record, captured["tools"]).get(spec.name) == mandate_tool(spec)
    if not allowed:
        raise BusinessError("owa_mandate_tool_denied", "This tool is outside the Run's OmniWeave Mandate")
    unit.assert_run_authorized(agent_id, run_id)
