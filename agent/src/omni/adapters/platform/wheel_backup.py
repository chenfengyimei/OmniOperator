"""Execute only the backup step of a reviewed switch plan; no stop, switch, migration or rollback."""

from __future__ import annotations

import hashlib
import json
from pathlib import Path
from typing import cast

from ...application.ports import Record
from ...domain.contracts import BusinessError
from ..persistence.maintenance import backup, durable_json
from .paths import InstancePaths
from .permissions import private_path
from .wheel_switch import switch_plan

FORMAT = "omni-update-backup-1"


def invalid(code: str = "update_backup_invalid") -> BusinessError:
    return BusinessError(code, "Back up only a prepared, sealed and idle candidate", status=422)


def backup_candidate(paths: InstancePaths, sha256: str) -> dict[str, object]:
    stage = paths.home / "updates" / sha256[:16]
    record_path = stage / "backup.json"
    if record_path.is_file():
        try:
            record = json.loads(record_path.read_text(encoding="utf-8"))
        except (ValueError, OSError, UnicodeError):
            raise invalid("update_backup_receipt_invalid") from None
        if (
            not isinstance(record, dict)
            or cast(Record, record).get("format") != FORMAT
            or cast(Record, record).get("wheel_sha256") != sha256
            or not Path(str(cast(Record, record).get("destination", ""))).is_dir()
        ):
            raise invalid("update_backup_receipt_invalid")
        return {
            "format": FORMAT,
            "written": False,
            "destination": record["destination"],
            "manifest_sha256": record["manifest_sha256"],
            "backup": None,
        }
    plan = switch_plan(paths, sha256)
    destination = paths.home.with_name(f"{paths.home.name}-backup-{sha256[:16]}")
    report = backup(paths, destination)
    manifest = destination / "manifest.json"
    if not manifest.is_file():
        raise invalid("update_backup_incomplete")
    digest = hashlib.sha256(manifest.read_bytes()).hexdigest()
    record = {
        "format": FORMAT,
        "wheel_sha256": sha256,
        "destination": str(destination),
        "manifest_sha256": digest,
        "plan": plan["format"],
    }
    durable_json(record_path, cast(Record, record))
    private_path(record_path)
    return {
        "format": FORMAT,
        "written": True,
        "destination": str(destination),
        "manifest_sha256": digest,
        "backup": report,
    }
