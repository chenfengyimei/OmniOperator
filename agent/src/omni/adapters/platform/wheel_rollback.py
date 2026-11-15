"""Rollback drill: restore the update backup into a new sibling directory and verify it; never touches live home."""

from __future__ import annotations

import json
from pathlib import Path
from typing import cast

from ...application.ports import Record
from ...domain.contracts import BusinessError
from ..persistence.maintenance import durable_json, inspect_database, restore
from .paths import InstancePaths
from .permissions import private_path
from .wheel_backup import FORMAT as BACKUP_FORMAT

FORMAT = "omni-rollback-check-1"


def invalid(code: str = "rollback_invalid") -> BusinessError:
    return BusinessError(code, "Restore only a recorded update backup into a new directory", status=422)


def rollback_check(paths: InstancePaths, sha256: str) -> dict[str, object]:
    stage = paths.home / "updates" / sha256[:16]
    backup_path = stage / "backup.json"
    if not backup_path.is_file():
        raise invalid("rollback_not_backed_up")
    try:
        record = json.loads(backup_path.read_text(encoding="utf-8"))
    except (ValueError, OSError, UnicodeError):
        raise invalid("rollback_backup_invalid") from None
    if (
        not isinstance(record, dict)
        or cast(Record, record).get("format") != BACKUP_FORMAT
        or cast(Record, record).get("wheel_sha256") != sha256
        or not Path(str(cast(Record, record).get("destination", ""))).is_dir()
    ):
        raise invalid("rollback_backup_invalid")
    source = Path(str(cast(Record, record)["destination"]))
    target = paths.home.with_name(f"{paths.home.name}-rollback-{sha256[:16]}")
    result_path = stage / "rollback.json"
    if result_path.is_file():
        try:
            existing = json.loads(result_path.read_text(encoding="utf-8"))
        except (ValueError, OSError, UnicodeError):
            raise invalid("rollback_receipt_invalid") from None
        if (
            not isinstance(existing, dict)
            or cast(Record, existing).get("format") != FORMAT
            or cast(Record, existing).get("wheel_sha256") != sha256
            or not (target / "data/omni.db").is_file()
        ):
            raise invalid("rollback_receipt_invalid")
        return {"format": FORMAT, "written": False, **existing}
    if target.exists() or target.is_symlink():
        raise invalid("rollback_target_exists")
    restore(source, target)
    inspected = inspect_database(target / "data/omni.db")
    if inspected["integrity"] != "ok" or not inspected["schema"]:
        raise invalid("rollback_unverified")
    result = {
        "format": FORMAT,
        "wheel_sha256": sha256,
        "source": str(source),
        "target": str(target),
        "schema": inspected["schema"],
        "verified": True,
    }
    durable_json(result_path, cast(Record, result))
    private_path(result_path)
    return {"written": True, **result}
