"""Execution-body maintenance bindings; shared offline infrastructure owns files and lifecycle."""

from __future__ import annotations

from pathlib import Path

from typing import cast

from ...application.ports import Record
from ...domain.retention import validate_retention_plan
from ..platform.paths import InstancePaths
from .offline_plans import PlanOperations, apply_plan, cancel_plan, create_plan, read_plan

OPERATIONS = PlanOperations(
    validate=lambda plan, now, allow_future: validate_retention_plan(
        cast(dict[str, object], plan), now, allow_future=allow_future
    ),
    make=lambda unit, after, limit: unit.retention_plan(after, limit),
    validate_current=lambda unit, plan: unit.retention_validate(plan),
    get_batch=lambda unit, identity: unit.retention_batch(identity),
    prepare=lambda unit, plan, path, sha: unit.retention_prepare(plan, path, sha),
    apply_target=lambda unit, plan, target: unit.retention_prune(plan, target),
    complete=lambda unit, identity: unit.retention_complete(identity),
    cancel=lambda unit, plan, reason: unit.retention_cancel(plan, reason),
)


def read_pruning_plan(path: Path, *, allow_future: bool = False) -> Record:
    return read_plan(path, OPERATIONS, allow_future=allow_future)


def plan_pruning(paths: InstancePaths, output: Path, *, after: str = "", limit: int = 20) -> Record:
    return create_plan(paths, output, OPERATIONS, after=after, limit=limit)


def apply_pruning(paths: InstancePaths, plan: Record, backup_path: Path) -> Record:
    return apply_plan(paths, plan, backup_path, OPERATIONS)


def cancel_pruning(paths: InstancePaths, plan: Record, reason: str) -> Record:
    return cancel_plan(paths, plan, reason, OPERATIONS)
