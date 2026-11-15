"""Offline reference release uses the shared verified maintenance lifecycle; never unlinks objects."""

from __future__ import annotations

from pathlib import Path
from typing import cast

from ...application.ports import Record
from ...domain.reference_release import validate_reference_plan
from ..platform.paths import InstancePaths
from .offline_plans import PlanOperations, apply_plan, cancel_plan, create_plan, read_plan

OPERATIONS = PlanOperations(
    validate=lambda plan, now, allow_future: validate_reference_plan(
        cast(dict[str, object], plan), now, allow_future=allow_future
    ),
    make=lambda u, after, limit: u.reference_plan(after, limit),
    validate_current=lambda u, plan: u.reference_validate(plan),
    get_batch=lambda u, identity: u.reference_batch(identity),
    prepare=lambda u, plan, path, sha: u.reference_prepare(plan, path, sha),
    apply_target=lambda u, plan, target: u.reference_release(plan, target),
    complete=lambda u, identity: u.reference_complete(identity),
    cancel=lambda u, plan, reason: u.reference_cancel(plan, reason),
)


def read_reference_plan(path: Path, *, allow_future: bool = False) -> Record:
    return read_plan(path, OPERATIONS, allow_future=allow_future)


def plan_reference_release(paths: InstancePaths, output: Path, *, after: str = "", limit: int = 20) -> Record:
    return create_plan(paths, output, OPERATIONS, after=after, limit=limit)


def apply_reference_release(paths: InstancePaths, plan: Record, backup_path: Path) -> Record:
    return apply_plan(paths, plan, backup_path, OPERATIONS)


def cancel_reference_release(paths: InstancePaths, plan: Record, reason: str) -> Record:
    return cancel_plan(paths, plan, reason, OPERATIONS)
