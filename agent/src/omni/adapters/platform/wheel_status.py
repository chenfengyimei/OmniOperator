"""Read-only installation/update status projection; never mutates, plans, switches or migrates."""

from __future__ import annotations

import json
from pathlib import Path
from typing import cast

from ...domain.contracts import BusinessError
from ...domain.installation import validate_installation
from ..persistence.maintenance import read_schema
from ..persistence.migration_config import head as migration_head
from omni.adapters.platform.runtime.paths import InstancePaths

FORMAT = "omni-installation-status-1"


def _read(path: Path) -> dict[str, object] | None:
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except (ValueError, OSError, UnicodeError):
        return None
    return cast(dict[str, object], value) if isinstance(value, dict) else None


def _next_step(stage: Path, pending: bool) -> str:
    if not (stage / "staged.json").is_file():
        return "artifact/stage"
    if not (stage / "prepared.json").is_file():
        return "prepare"
    if not (stage / "backup.json").is_file():
        return "backup"
    if not (stage / "rollback.json").is_file():
        return "rollback-check"
    if pending and not (stage / "migrate.json").is_file():
        return "migrate"
    return "switch"


def _runtime(paths: InstancePaths) -> dict[str, object]:
    record = _read(paths.home / "runtime" / "current.json") or {}
    previous = _read(paths.home / "runtime" / "previous.json") or {}
    rollback = _read(paths.home / "runtime" / "rollback-live.json") or {}
    removal = _read(paths.home / "runtime" / "uninstall.json") or {}
    cleanup_pending = removal.get("format") == "omni-uninstall-1" and removal.get("phase") == "deactivated"
    return {
        "active": bool(record.get("wheel_sha256")),
        "wheel_sha256": record.get("wheel_sha256"),
        "mode": record.get("mode"),
        "python": record.get("python"),
        "tree": record.get("tree"),
        "previous": previous.get("wheel_sha256"),
        "rollback_live": rollback.get("written") is True,
        "cleanup_pending": cleanup_pending,
        "cleanup_candidate": removal.get("wheel_sha256") if cleanup_pending else None,
    }


def installation_status(paths: InstancePaths) -> dict[str, object]:
    manifest_path = paths.home / "installation.json"
    manifest: dict[str, str] | None = None
    problem = ""
    if manifest_path.is_symlink():
        problem = "manifest_link"
    elif manifest_path.is_file():
        try:
            manifest = validate_installation(json.loads(manifest_path.read_text(encoding="utf-8")))
        except (ValueError, BusinessError, UnicodeError):
            problem = "manifest_invalid"
    database = {
        "exists": paths.database.is_file(),
        "wal": (paths.database.parent / "omni.db-wal").is_file(),
        "schema": "",
        "problem": "",
    }
    if database["exists"]:
        try:
            database["schema"] = read_schema(paths.database)
        except BusinessError:
            database["problem"] = "database_unreadable"
    expected = migration_head()
    pending = bool(database["schema"]) and database["schema"] != expected
    runtime = _runtime(paths)
    candidates: list[dict[str, object]] = []
    updates = paths.home / "updates"
    if updates.is_dir() and not updates.is_symlink():
        for stage in sorted(updates.iterdir()):
            if not stage.is_dir() or stage.is_symlink() or len(stage.name) != 16:
                continue
            staged = _read(stage / "staged.json")
            prepared = _read(stage / "prepared.json")
            candidates.append(
                {
                    "stage": stage.name,
                    "wheel_sha256": (staged or prepared or {}).get("wheel_sha256", ""),
                    "staged": staged is not None,
                    "prepared": prepared is not None,
                    "environment": (stage / "environment.json").is_file(),
                    "backup": (stage / "backup.json").is_file(),
                    "rollback": (stage / "rollback.json").is_file(),
                    "migrate": (stage / "migrate.json").is_file(),
                    "active": stage.name == str(runtime.get("wheel_sha256", ""))[:16],
                    "next_step": "uninstall"
                    if stage.name == str(runtime.get("cleanup_candidate", ""))[:16]
                    else _next_step(stage, pending),
                }
            )
    return {
        "format": FORMAT,
        "home": str(paths.home),
        "exists": paths.home.is_dir(),
        "manifest": manifest,
        "manifest_problem": problem,
        "database": database,
        "expected_schema": expected,
        "upgrade_required": pending,
        "runtime": runtime,
        "candidates": candidates,
    }
