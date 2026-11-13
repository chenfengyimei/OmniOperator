"""Exclusive plan/apply collection; commit unreferenced metadata before unlinking files."""

from __future__ import annotations

import asyncio
import datetime as dt
import hashlib
import json
import uuid
from collections.abc import Mapping
from pathlib import Path
from typing import cast

from ...application.ports import Record, UnitOfWork
from ...domain.contracts import JSON, BusinessError
from ...domain.objects import Blob
from ..object_store import checked_path, sync_directory
from ..platform.paths import InstanceLock, InstancePaths
from ..primitives import SystemClock, UUIDs
from .database import Database
from .maintenance import durable_json
from .object_inventory import MAX_INVENTORY, physical_keys, registry, signature


def _instant(now: dt.datetime | None) -> dt.datetime:
    chosen = now or dt.datetime.now(dt.timezone.utc)
    if chosen.tzinfo is None:
        raise BusinessError("invalid_time", "Maintenance requires timezone-aware time")
    return chosen.astimezone(dt.timezone.utc)


def _plan(paths: InstancePaths, now: dt.datetime) -> dict[str, object]:
    instance, schema, rows, references = registry(paths.database)
    by_key = {
        Blob(cast(str, row["agent_id"]), cast(str, row["sha256"]), cast(int, row["bytes"])).key: row for row in rows
    }
    entries: list[Record] = []
    protected = 0
    for key, row in by_key.items():
        if (
            row["pinned"]
            or row["id"] in references
            or not row["expires_at"]
            or dt.datetime.fromisoformat(cast(str, row["expires_at"])) > now
        ):
            protected += 1
            continue
        entries.append(
            {"key": key, "object": row, "reason": "expired_unreferenced", "file": signature(paths.objects, key)}
        )
    cutoff = (now - dt.timedelta(days=1)).timestamp()
    for key in physical_keys(paths.objects):
        if key in by_key:
            continue
        path = checked_path(paths.objects, key)
        if path.stat().st_mtime > cutoff:
            protected += 1
            continue
        current = signature(paths.objects, key)
        if key.endswith(".blob") and current and current["sha256"] != path.stem:
            raise BusinessError("object_integrity_failed", "An unregistered blob does not match its content address")
        entries.append(
            {
                "key": key,
                "object": None,
                "reason": "interrupted_write" if key.endswith(".partial") else "unregistered_blob",
                "file": current,
            }
        )
    if len(entries) > MAX_INVENTORY:
        raise BusinessError("inventory_limit", "Collection plan exceeds its limit")
    return {
        "format": "omni-object-gc-1",
        "plan_id": str(uuid.uuid4()),
        "instance_id": instance,
        "schema": schema,
        "created_at": now.isoformat(timespec="microseconds"),
        "orphan_grace_hours": 24,
        "entries": sorted(entries, key=lambda entry: cast(str, entry["key"])),
        "protected": protected,
    }


def plan_collection(paths: InstancePaths, *, now: dt.datetime | None = None) -> dict[str, object]:
    lock = InstanceLock(paths)
    lock.acquire()
    try:
        return _plan(paths, _instant(now))
    finally:
        lock.close()


def _journal_path(paths: InstancePaths, plan_id: str) -> Path:
    try:
        if str(uuid.UUID(plan_id)) != plan_id:
            raise ValueError
    except (ValueError, AttributeError) as exc:
        raise BusinessError("invalid_gc_plan", "Collection plan ID is invalid", status=422) from exc
    directory = paths.control / "collection"
    if directory.is_symlink() or directory.is_junction():
        raise BusinessError("unsafe_gc_journal", "Collection journal directory cannot be a link")
    directory.mkdir(mode=0o700, parents=True, exist_ok=True)
    path = directory / (plan_id + ".json")
    if path.is_symlink() or path.is_junction():
        raise BusinessError("unsafe_gc_journal", "Collection journal cannot be a link")
    return path


def apply_collection(
    paths: InstancePaths, plan: Mapping[str, object], *, now: dt.datetime | None = None
) -> dict[str, object]:
    if (
        not isinstance(plan, dict)
        or plan.get("format") != "omni-object-gc-1"
        or not isinstance(plan.get("entries"), list)
        or len(cast(list[object], plan["entries"])) > MAX_INVENTORY
    ):
        raise BusinessError("invalid_gc_plan", "A bounded collection plan is required", status=422)
    instant = _instant(now)
    lock = InstanceLock(paths)
    lock.acquire()
    try:
        current = _plan(paths, instant)
        if (current["instance_id"], current["schema"]) != (plan.get("instance_id"), plan.get("schema")):
            raise BusinessError("stale_gc_plan", "Collection plan belongs to another instance or schema")
        path = _journal_path(paths, cast(str, plan.get("plan_id", "")))
        digest = hashlib.sha256(json.dumps(plan, sort_keys=True, separators=(",", ":")).encode()).hexdigest()
        previous: Record | None = cast(Record, json.loads(path.read_text(encoding="utf-8"))) if path.exists() else None
        if previous and previous.get("plan_sha256") != digest:
            raise BusinessError("stale_gc_plan", "Collection plan changed after its first application")
        resumed = previous is not None
        current_entries = cast(list[Record], current["entries"])
        candidates = {cast(str, entry["key"]): entry for entry in current_entries}
        _, _, registered, _ = registry(paths.database)
        by_key = {
            Blob(cast(str, row["agent_id"]), cast(str, row["sha256"]), cast(int, row["bytes"])).key: row
            for row in registered
        }
        keys: set[str] = set()
        for entry in cast(list[object], plan["entries"]):
            if (
                not isinstance(entry, dict)
                or not isinstance(cast(dict[str, object], entry).get("key"), str)
                or cast(dict[str, object], entry)["key"] in keys
            ):
                raise BusinessError("invalid_gc_plan", "Collection targets must be unique controlled paths", status=422)
            key = cast(str, entry["key"])
            keys.add(key)
            # A caller-supplied path must be a currently eligible candidate, or
            # an unchanged leftover from this exact durable collection journal.
            actual = candidates.get(key)
            if actual == entry:
                continue
            if (
                resumed
                and key not in by_key
                and signature(paths.objects, key) in (cast(dict[str, object], entry).get("file"), None)
            ):
                continue
            raise BusinessError("stale_gc_plan", "A collection target, reference or fingerprint changed")
        journal: Record = {
            "plan_sha256": digest,
            "plan": cast(Record, dict(plan)),
            "complete": False,
            "phase": "applying",
            "residual": [],
        }
        durable_json(path, journal)
        status_path = path.with_suffix(".status.json")
        durable_json(
            status_path,
            cast(Record, {"plan_id": plan["plan_id"], "complete": False, "phase": "applying"}),
        )

        class MaintenanceClock(SystemClock):
            def now(self) -> str:
                return instant.isoformat(timespec="microseconds")

        async def remove_metadata() -> int:
            database = Database(paths.database, MaintenanceClock(), UUIDs(), objects_root=paths.objects)
            try:

                def remove(u: UnitOfWork) -> int:
                    removed = 0
                    for entry in cast(list[Record], plan["entries"]):
                        row = cast(Record | None, entry["object"])
                        if row and cast(str, entry["key"]) in by_key:
                            u.object_remove(
                                cast(str, row["agent_id"]), cast(str, row["id"]), cast(int, row["revision"])
                            )
                            removed += 1
                    return removed

                return await database.write(remove)
            finally:
                await database.close()

        removed = asyncio.run(remove_metadata())
        journal["phase"] = "metadata_committed"
        durable_json(path, journal)
        unlinked, residual = 0, cast(list[Record], [])
        for entry in cast(list[Record], plan["entries"]):
            target = checked_path(paths.objects, cast(str, entry["key"]))
            try:
                if target.exists():
                    if signature(paths.objects, cast(str, entry["key"])) != entry["file"]:
                        raise BusinessError("object_changed", "Object changed after collection commit")
                    target.unlink()
                    sync_directory(target.parent)
                    unlinked += 1
            except (OSError, BusinessError) as exc:
                residual.append(
                    {
                        "key": entry["key"],
                        "error_code": exc.code if isinstance(exc, BusinessError) else "object_unlink_failed",
                    }
                )
        journal.update(
            phase="finished",
            complete=not residual,
            residual=cast(list[JSON], residual),
            removed_metadata=removed,
            unlinked_files=unlinked,
        )
        durable_json(path, journal)
        durable_json(
            status_path,
            cast(
                Record,
                {
                    "plan_id": plan["plan_id"],
                    "complete": not residual,
                    "phase": "finished",
                    "residual_count": len(residual),
                },
            ),
        )
        return {
            "plan_id": plan["plan_id"],
            "complete": not residual,
            "resumed": resumed,
            "removed_metadata": removed,
            "unlinked_files": unlinked,
            "residual": residual,
        }
    finally:
        lock.close()
