"""Execute the switch step: point the runtime at a prepared candidate; no stop, migrate or start."""

from __future__ import annotations

import datetime as dt
import json
import os
import sys
from pathlib import Path

from ...domain.contracts import BusinessError
from ...domain.installation import make_installation, validate_installation
from ..persistence.maintenance import durable_json, migrate_database, read_schema
from ..persistence.migration_config import head as migration_head
from ..primitives import SystemClock
from .paths import InstanceLock, InstancePaths
from .permissions import private_path
from .wheel_backup import FORMAT as BACKUP_FORMAT
from .wheel_prepare import FORMAT as PREPARED_FORMAT
from .wheel_rollback import FORMAT as ROLLBACK_FORMAT
from .wheel_smoke import FORMAT as SMOKE_FORMAT

FORMAT = "omni-runtime-1"
LAUNCHER = '''"""Run the active V2 candidate selected by the installation runtime pointer."""

import json
import sys
from pathlib import Path

runtime = Path(__file__).resolve().parent
home = runtime.parent
pointer = json.loads((runtime / "current.json").read_text(encoding="utf-8"))
sys.path.insert(0, str(Path(pointer["tree"])))
from omni.composition.cli import main

sys.argv[1:1] = ["--home", str(home)]
raise SystemExit(main())
'''


def invalid(code: str = "switch_invalid") -> BusinessError:
    return BusinessError(code, "Switch only a prepared, smoked, backed-up and drilled candidate", status=422)


def _receipt(stage: Path, name: str, fmt: str, sha256: str) -> dict[str, object]:
    path = stage / name
    if not path.is_file():
        raise invalid(f"switch_missing_{name.removesuffix('.json')}")
    try:
        record = json.loads(path.read_text(encoding="utf-8"))
    except (ValueError, OSError, UnicodeError):
        raise invalid(f"switch_{name.removesuffix('.json')}_invalid") from None
    if not isinstance(record, dict) or record.get("format") != fmt or record.get("wheel_sha256") != sha256:
        raise invalid(f"switch_{name.removesuffix('.json')}_invalid")
    return record


def _wrapper(home: Path) -> None:
    python = sys.executable
    (home / "omni.cmd").write_text(
        '@echo off\r\nsetlocal\r\n"' + python + '" "%~dp0runtime\\launch.py" %*\r\n',
        encoding="utf-8",
    )
    script = home / "omni"
    script.write_text(f'#!/bin/sh\nexec "{python}" "$(dirname "$0")/runtime/launch.py" "$@"\n', encoding="utf-8")
    if os.name != "nt":
        script.chmod(0o755)


def migrate_candidate(paths: InstancePaths, sha256: str) -> dict[str, object]:
    stage = paths.home / "updates" / sha256[:16]
    _receipt(stage, "prepared.json", PREPARED_FORMAT, sha256)
    _receipt(stage, "smoke.json", SMOKE_FORMAT, sha256)
    _receipt(stage, "backup.json", BACKUP_FORMAT, sha256)
    _receipt(stage, "rollback.json", ROLLBACK_FORMAT, sha256)
    receipt_path = paths.home / "installation.json"
    if not receipt_path.is_file():
        raise invalid("migrate_unsealed")
    try:
        receipt = validate_installation(json.loads(receipt_path.read_text(encoding="utf-8")))
    except (ValueError, UnicodeError, BusinessError) as exc:
        raise invalid("migrate_unsealed") from exc
    schema = read_schema(paths.database)
    if receipt["schema_version"] != schema:
        raise invalid("migrate_receipt_stale")
    target = migration_head()
    if schema == target:
        return {
            "format": "omni-schema-migration-1",
            "wheel_sha256": sha256,
            "written": False,
            "before": schema,
            "after": schema,
            "target": target,
            "migrated": False,
            "resealed": False,
        }
    lock = InstanceLock(paths)
    lock.acquire()
    try:
        result = migrate_database(paths.database)
        if result["after"] != target:
            raise invalid("migrate_incomplete")
        resealed = make_installation(SystemClock().now(), str(result["after"]), target)
        durable_json(receipt_path, resealed)
        private_path(receipt_path)
    finally:
        lock.close()
    record = {
        "format": "omni-schema-migration-1",
        "wheel_sha256": sha256,
        "written": True,
        "before": result["before"],
        "after": result["after"],
        "target": target,
        "migrated": bool(result["migrated"]),
        "resealed": True,
    }
    durable_json(stage / "migrate.json", record)
    private_path(stage / "migrate.json")
    return record


def switch_candidate(paths: InstancePaths, sha256: str) -> dict[str, object]:
    stage = paths.home / "updates" / sha256[:16]
    tree = stage / "tree"
    prepared = _receipt(stage, "prepared.json", PREPARED_FORMAT, sha256)
    smoke = _receipt(stage, "smoke.json", SMOKE_FORMAT, sha256)
    backup = _receipt(stage, "backup.json", BACKUP_FORMAT, sha256)
    rollback = _receipt(stage, "rollback.json", ROLLBACK_FORMAT, sha256)
    if smoke.get("verified") is not True or rollback.get("verified") is not True:
        raise invalid("switch_unverified")
    if not (tree / "omni" / "__init__.py").is_file() or not (tree / "omni/web_assets/ui/v2/index.html").is_file():
        raise invalid("switch_tree_invalid")
    if not Path(str(backup.get("destination", ""))).is_dir():
        raise invalid("switch_backup_invalid")
    receipt_path = paths.home / "installation.json"
    if not receipt_path.is_file():
        raise invalid("switch_unsealed")
    try:
        receipt = validate_installation(json.loads(receipt_path.read_text(encoding="utf-8")))
    except (ValueError, UnicodeError, BusinessError) as exc:
        raise invalid("switch_unsealed") from exc
    schema = read_schema(paths.database)
    if receipt["schema_version"] != schema:
        raise invalid("switch_receipt_stale")
    target = migration_head()
    if schema != target:
        raise BusinessError(
            "switch_migration_required", "Apply schema migrations before switching this candidate", status=422
        )
    lock = InstanceLock(paths)
    try:
        lock.acquire()
    except BusinessError as exc:
        if exc.code == "instance_busy":
            raise invalid("instance_running") from None
        raise
    finally:
        lock.close()
    runtime = paths.home / "runtime"
    runtime.mkdir(parents=True, exist_ok=True)
    current_path = runtime / "current.json"
    previous: str | None = None
    if current_path.is_file():
        try:
            old = json.loads(current_path.read_text(encoding="utf-8"))
        except (ValueError, OSError, UnicodeError):
            raise invalid("switch_pointer_invalid") from None
        if isinstance(old, dict) and old.get("format") == FORMAT:
            previous = str(old.get("wheel_sha256", "")) or None
            durable_json(runtime / "previous.json", old)
            private_path(runtime / "previous.json")
            if old.get("wheel_sha256") == sha256 and Path(str(old.get("tree", ""))) == tree:
                _wrapper(paths.home)
                return {
                    "format": FORMAT,
                    "written": False,
                    "wheel_sha256": sha256,
                    "tree": str(tree),
                    "previous": previous,
                    "launcher": str(runtime / "launch.py"),
                }
    record = {
        "format": FORMAT,
        "wheel_sha256": sha256,
        "tree": str(tree),
        "schema_version": schema,
        "python": sys.version.split()[0],
        "activated_at": dt.datetime.now(dt.timezone.utc).isoformat(timespec="microseconds"),
        "previous": previous,
        "prepared_files": prepared.get("files", 0),
    }
    durable_json(current_path, record)
    private_path(current_path)
    (runtime / "launch.py").write_text(LAUNCHER, encoding="utf-8")
    _wrapper(paths.home)
    return {"format": FORMAT, "written": True, **record, "launcher": str(runtime / "launch.py")}


def runtime_current(paths: InstancePaths) -> dict[str, object]:
    path = paths.home / "runtime" / "current.json"
    if not path.is_file():
        return {"format": FORMAT, "active": False, "wheel_sha256": None, "tree": None, "previous": None}
    try:
        record = json.loads(path.read_text(encoding="utf-8"))
    except (ValueError, OSError, UnicodeError):
        raise invalid("switch_pointer_invalid") from None
    if not isinstance(record, dict) or record.get("format") != FORMAT:
        raise invalid("switch_pointer_invalid")
    tree = Path(str(record.get("tree", "")))
    return {
        "format": FORMAT,
        "active": (tree / "omni" / "__init__.py").is_file(),
        "wheel_sha256": record.get("wheel_sha256"),
        "tree": str(tree),
        "schema_version": record.get("schema_version"),
        "previous": record.get("previous"),
        "launcher": str(paths.home / "runtime" / "launch.py"),
    }


def install_candidate(paths: InstancePaths, wheel: Path, sha256: str) -> dict[str, object]:
    from .wheel_artifact import inspect_wheel
    from .wheel_backup import backup_candidate
    from .wheel_prepare import prepare_wheel
    from .wheel_rollback import rollback_check
    from .wheel_smoke import smoke_candidate
    from .wheel_stage import stage_wheel

    inspected = inspect_wheel(wheel, sha256)
    staged = stage_wheel(paths.home, wheel, sha256)
    prepared = prepare_wheel(paths.home, sha256)
    smoke = smoke_candidate(paths, sha256)
    backup = backup_candidate(paths, sha256)
    rollback = rollback_check(paths, sha256)
    migration = migrate_candidate(paths, sha256)
    switched = switch_candidate(paths, sha256)
    return {
        "format": "omni-install-1",
        "wheel_sha256": sha256,
        "verified": inspected["verified"] is True,
        "staged": staged,
        "prepared": prepared,
        "smoke": smoke,
        "backup": backup,
        "rollback": rollback,
        "migrate": migration,
        "switch": switched,
        "requires_owner_approval": True,
    }
