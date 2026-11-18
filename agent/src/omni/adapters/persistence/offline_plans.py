"""Exclusive reviewed maintenance plans; domain callbacks own eligibility and transactional progress."""

from __future__ import annotations

import asyncio
import json
import os
import uuid
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path
from typing import cast

from omni.adapters.platform.runtime.paths import InstanceLock, InstancePaths
from omni.adapters.platform.runtime.permissions import private_path

from ...application.ports import Record, UnitOfWork
from ...domain.contracts import JSON, BusinessError
from ..object_store import publish_file, sync_directory
from ..primitives import SystemClock, UUIDs
from .database import Database
from .maintenance import create_snapshot, fingerprint, verify_snapshot
from .operations_read import offline_read

MAX_PLAN_BYTES = 65_536


def _unique_fields(pairs: list[tuple[str, object]]) -> Record:
    result: dict[str, JSON] = {}
    for key, value in pairs:
        if key in result:
            raise ValueError("Duplicate maintenance plan field")
        result[key] = cast(JSON, value)
    return result


@dataclass(frozen=True)
class PlanOperations:
    validate: Callable[[Record, str, bool], str]
    make: Callable[[UnitOfWork, str, int], Record]
    validate_current: Callable[[UnitOfWork, Record], None]
    get_batch: Callable[[UnitOfWork, str], Record | None]
    prepare: Callable[[UnitOfWork, Record, str, str], Record]
    apply_target: Callable[[UnitOfWork, Record, Record], None]
    complete: Callable[[UnitOfWork, str], Record]
    cancel: Callable[[UnitOfWork, Record, str], Record]


def _outside_home(paths: InstancePaths, path: Path) -> Path:
    path = path.absolute()
    home = paths.home.resolve()
    if path.resolve().is_relative_to(home) or home.is_relative_to(path.resolve()):
        raise BusinessError("unsafe_destination", "Maintenance plans and backups must be outside the instance home")
    if any(parent.is_symlink() or parent.is_junction() for parent in (path, *path.parents)):
        raise BusinessError("unsafe_destination", "Maintenance paths cannot contain links")
    return path


def read_plan(path: Path, operations: PlanOperations, *, allow_future: bool = False) -> Record:
    if path.is_symlink() or path.is_junction() or not path.is_file():
        raise BusinessError("invalid_retention_plan", "Plan must be a bounded regular file", status=422)
    try:
        with path.open("rb") as stream:
            data = stream.read(MAX_PLAN_BYTES + 1)
        if len(data) > MAX_PLAN_BYTES:
            raise ValueError
        plan = json.loads(data, object_pairs_hook=_unique_fields)
        if not isinstance(plan, dict):
            raise ValueError
        checked = cast(Record, plan)
        operations.validate(checked, SystemClock().now(), allow_future)
        return checked
    except (OSError, ValueError, UnicodeError, RecursionError) as exc:
        raise BusinessError("invalid_retention_plan", "Plan must be valid JSON up to 64 KiB", status=422) from exc


def create_plan(
    paths: InstancePaths, output: Path, operations: PlanOperations, *, after: str = "", limit: int = 20
) -> Record:
    output = _outside_home(paths, output)
    if output.exists():
        raise BusinessError("destination_exists", "Plan output cannot overwrite an existing file")
    lock = InstanceLock(paths)
    lock.acquire()
    temporary = output.with_name(f".{uuid.uuid4()}.partial")
    try:
        clock, ids = SystemClock(), UUIDs()
        plan = offline_read(paths.database, clock, ids, lambda u: operations.make(u, after, limit))
        plan.pop("verification")
        operations.validate(plan, clock.now(), False)
        targets = plan["targets"]
        if not isinstance(targets, list):
            raise BusinessError("invalid_retention_plan", "Plan targets must be a list", status=422)
        data = json.dumps(plan, ensure_ascii=False, indent=2).encode("utf-8")
        if len(data) > MAX_PLAN_BYTES:
            raise BusinessError("invalid_retention_plan", "Plan must be valid JSON up to 64 KiB", status=422)
        output.parent.mkdir(parents=True, exist_ok=True)
        with temporary.open("xb") as stream:
            private_path(temporary)
            stream.write(data)
            stream.flush()
            os.fsync(stream.fileno())
        # The external destination has no instance ownership fence. POSIX link
        # provides atomic publication without replacing a competing writer.
        if os.name == "nt":
            publish_file(temporary, output)
        else:
            os.link(temporary, output)
            temporary.unlink()
            sync_directory(output.parent)
        return {"plan_id": plan["id"], "targets": len(targets), "output": str(output), "applied": False}
    finally:
        try:
            temporary.unlink(missing_ok=True)
        finally:
            lock.close()


def apply_plan(paths: InstancePaths, plan: Record, backup_path: Path, operations: PlanOperations) -> Record:
    """One database actor per command; snapshot first, then one transaction per target."""
    clock, ids = SystemClock(), UUIDs()
    operations.validate(plan, clock.now(), False)
    identity = cast(str, plan["id"])
    targets = cast(list[Record], plan["targets"])
    backup_path = _outside_home(paths, backup_path)
    lock = InstanceLock(paths)
    lock.acquire()
    try:

        def preflight(unit: UnitOfWork) -> Record:
            operations.validate_current(unit, plan)
            return operations.get_batch(unit, identity) or {}

        batch = offline_read(paths.database, clock, ids, preflight)
        batch.pop("verification")
        if batch:
            if batch["backup_path"] != str(backup_path):
                raise BusinessError("retention_backup_changed", "Resume requires the original backup location")
        else:
            # An orphaned snapshot is never silently reused: unrelated writes
            # may have happened after capture and before durable batch binding.
            create_snapshot(paths.database, paths.objects, backup_path)
        verify_snapshot(backup_path)
        sha = fingerprint(backup_path / "manifest.json")
        if batch and sha != batch["backup_sha256"]:
            raise BusinessError("retention_backup_changed", "The bound backup manifest changed")

        def verify_original(unit: UnitOfWork) -> Record:
            operations.validate_current(unit, plan)
            return {"valid": True}

        offline_read(backup_path / "data/omni.db", clock, ids, verify_original)

        async def execute() -> Record:
            database = Database(paths.database, clock, ids, objects_root=paths.objects)
            try:
                await database.write(lambda unit: operations.prepare(unit, plan, str(backup_path), sha))
                for target in targets:
                    await database.write(lambda unit: operations.apply_target(unit, plan, target))
                return await database.write(lambda unit: operations.complete(unit, identity))
            finally:
                await database.close()

        return asyncio.run(execute())
    finally:
        lock.close()


def cancel_plan(paths: InstancePaths, plan: Record, reason: str, operations: PlanOperations) -> Record:
    """Cancellation checks bound ownership, never current target eligibility or wall-clock progress."""
    clock, ids = SystemClock(), UUIDs()
    operations.validate(plan, clock.now(), True)
    identity = cast(str, plan["id"])
    lock = InstanceLock(paths)
    lock.acquire()
    try:
        # Installed-head preflight stays read-only. New provenance or a pin may
        # be why the owner cancels; eligibility belongs only to application.
        offline_read(paths.database, clock, ids, lambda unit: operations.get_batch(unit, identity) or {})

        async def execute() -> Record:
            database = Database(paths.database, clock, ids, objects_root=paths.objects)
            try:
                return await database.write(lambda unit: operations.cancel(unit, plan, reason))
            finally:
                await database.close()

        return asyncio.run(execute())
    finally:
        lock.close()
