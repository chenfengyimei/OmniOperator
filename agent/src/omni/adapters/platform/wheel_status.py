"""Read-only installation/update status projection; never mutates, plans, switches or migrates."""

from __future__ import annotations

import json
from pathlib import Path

from ...domain.contracts import BusinessError
from ...domain.installation import validate_installation
from ..persistence.maintenance import read_schema
from ..persistence.migration_config import head as migration_head
from .paths import InstancePaths

FORMAT = "omni-installation-status-1"


def _read(path: Path) -> dict[str, object] | None:
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except (ValueError, OSError, UnicodeError):
        return None
    return value if isinstance(value, dict) else None


def _next_step(stage: Path) -> str:
    if not (stage / "staged.json").is_file():
        return "artifact/stage"
    if not (stage / "prepared.json").is_file():
        return "prepare"
    if not (stage / "backup.json").is_file():
        return "backup"
    if not (stage / "rollback.json").is_file():
        return "rollback-check"
    return "switch"


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
    candidates = []
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
                    "backup": (stage / "backup.json").is_file(),
                    "rollback": (stage / "rollback.json").is_file(),
                    "next_step": _next_step(stage),
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
        "upgrade_required": bool(database["schema"]) and database["schema"] != expected,
        "candidates": candidates,
    }
