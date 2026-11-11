"""Read-only switch plan for a prepared candidate; never stops, switches, migrates or rolls back."""

from __future__ import annotations

import json
from pathlib import Path

from ...domain.contracts import BusinessError
from ...domain.installation import validate_installation
from ..persistence.maintenance import read_schema
from ..persistence.migration_config import head as migration_head
from .paths import InstanceLock, InstancePaths
from .wheel_prepare import FORMAT as PREPARED_FORMAT

FORMAT = "omni-switch-plan-1"


def invalid(code: str = "switch_plan_invalid") -> BusinessError:
    return BusinessError(code, "Prepare one verified candidate before planning a switch", status=422)


def _schema(database: Path) -> str:
    return read_schema(database)


def switch_plan(paths: InstancePaths, sha256: str) -> dict[str, object]:
    stage = paths.home / "updates" / sha256[:16]
    prepared_path = stage / "prepared.json"
    tree = stage / "tree"
    if not prepared_path.is_file() or not (tree / "omni/web_assets/ui/v2/index.html").is_file():
        raise invalid("switch_plan_not_prepared")
    try:
        prepared = json.loads(prepared_path.read_text(encoding="utf-8"))
    except (ValueError, OSError, UnicodeError):
        raise invalid("switch_plan_receipt_invalid") from None
    if (
        not isinstance(prepared, dict)
        or prepared.get("format") != PREPARED_FORMAT
        or prepared.get("wheel_sha256") != sha256
    ):
        raise invalid("switch_plan_receipt_invalid")
    receipt_path = paths.home / "installation.json"
    if not receipt_path.is_file():
        raise invalid("switch_plan_unsealed")
    try:
        receipt = validate_installation(json.loads(receipt_path.read_text(encoding="utf-8")))
    except (ValueError, UnicodeError, BusinessError) as exc:
        raise invalid("switch_plan_unsealed") from exc
    try:
        schema = _schema(paths.database)
    except BusinessError:
        raise invalid("switch_plan_database") from None
    if not schema or receipt["schema_version"] != schema:
        raise invalid("switch_plan_receipt_stale")
    lock = InstanceLock(paths)
    try:
        lock.acquire()
    except BusinessError as exc:
        if exc.code == "instance_busy":
            raise invalid("instance_running") from None
        raise
    finally:
        lock.close()
    target = migration_head()
    steps = [
        {"id": "backup", "required": True, "reason": "consistent snapshot before any switch"},
        {"id": "stop", "required": True, "reason": "drain and stop the current service"},
        {"id": "switch", "required": True, "reason": "point the runtime at the prepared tree"},
        {"id": "migrate", "required": schema != target, "reason": "apply schema migrations if pending"},
        {"id": "seal", "required": True, "reason": "write a fresh installation receipt"},
        {"id": "start", "required": True, "reason": "start the candidate with its own readiness proof"},
        {"id": "health", "required": True, "reason": "verify health before declaring success"},
    ]
    return {
        "format": FORMAT,
        "wheel_sha256": sha256,
        "tree": str(tree),
        "source_schema": schema,
        "target_schema": target,
        "steps": steps,
        "executable": False,
        "requires_owner_approval": True,
    }
