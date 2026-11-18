"""Offline audit-pruning bindings; shared infrastructure owns locks, verified backups and plan files."""

from __future__ import annotations

from dataclasses import replace
from pathlib import Path
from typing import cast

from omni.adapters.platform.runtime.paths import InstancePaths

from ...application.ports import Record, UnitOfWork
from ...domain.audit_retention import validate_audit_plan
from .offline_plans import PlanOperations, apply_plan, cancel_plan, create_plan, read_plan


def _validate(plan: Record, now: str, allow_future: bool) -> str:
    return validate_audit_plan(cast(dict[str, object], plan), now, allow_future=allow_future)


OPERATIONS = PlanOperations(
    validate=_validate,
    make=lambda unit, cursor, limit: unit.audit_retention_plan("", cursor=cursor, limit=limit),
    validate_current=lambda unit, plan: unit.audit_retention_validate(plan),
    get_batch=lambda unit, identity: unit.audit_retention_batch(identity),
    prepare=lambda unit, plan, path, sha: unit.audit_retention_prepare(plan, path, sha),
    apply_target=lambda unit, plan, target: unit.audit_retention_delete(plan, target),
    complete=lambda unit, identity: unit.audit_retention_complete(identity),
    cancel=lambda unit, plan, reason: unit.audit_retention_cancel(plan, reason),
)


def read_audit_plan(path: Path, *, allow_future: bool = False) -> Record:
    return read_plan(path, OPERATIONS, allow_future=allow_future)


def plan_audit_retention(
    paths: InstancePaths,
    output: Path,
    *,
    agent_id: str,
    cursor: str = "",
    limit: int = 20,
    as_of: str = "",
) -> Record:
    def make(unit: UnitOfWork, after: str, page_limit: int) -> Record:
        return unit.audit_retention_plan(agent_id, cursor=after, limit=page_limit, as_of=as_of)

    operations = replace(OPERATIONS, make=make)
    return create_plan(paths, output, operations, after=cursor, limit=limit)


def apply_audit_retention(paths: InstancePaths, plan: Record, backup_path: Path) -> Record:
    return apply_plan(paths, plan, backup_path, OPERATIONS)


def cancel_audit_retention(paths: InstancePaths, plan: Record, reason: str) -> Record:
    return cancel_plan(paths, plan, reason, OPERATIONS)
