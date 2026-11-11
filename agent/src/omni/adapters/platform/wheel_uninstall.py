"""Remove one staged candidate or deactivate the runtime pointer; data and installation receipts stay."""

from __future__ import annotations

import json
import shutil
from pathlib import Path

from ...domain.contracts import BusinessError
from .paths import InstanceLock, InstancePaths
from .wheel_install import FORMAT as RUNTIME_FORMAT

FORMAT = "omni-uninstall-1"
POINTER_FILES = ("current.json", "previous.json", "launch.py")
PURGE_DIRS = ("data", "objects", "logs", "updates", "runtime", "cache")
PURGE_FILES = ("installation.json", "omni.cmd", "omni")


def invalid(code: str = "uninstall_invalid") -> BusinessError:
    return BusinessError(code, "Uninstall only a staged candidate inside this instance", status=422)


def _inside(path: Path, root: Path) -> bool:
    resolved, root_resolved = path.resolve(), root.resolve()
    return resolved == root_resolved or root_resolved in resolved.parents


def _remove_stage(stage: Path, updates: Path) -> None:
    if stage.is_symlink() or stage.is_junction() or not _inside(stage, updates):
        raise invalid("uninstall_unsafe_path")
    shutil.rmtree(stage)


def _active_sha(runtime: Path) -> str | None:
    path = runtime / "current.json"
    if not path.is_file() or path.is_symlink():
        return None
    try:
        record = json.loads(path.read_text(encoding="utf-8"))
    except (ValueError, OSError, UnicodeError):
        raise invalid("uninstall_pointer_invalid") from None
    if not isinstance(record, dict) or record.get("format") != RUNTIME_FORMAT:
        raise invalid("uninstall_pointer_invalid")
    return str(record.get("wheel_sha256", "")) or None


def uninstall_candidate(paths: InstancePaths, sha256: str = "", purge: bool = False) -> dict[str, object]:
    """With SHA remove that candidate; without SHA only deactivate; purge explicitly clears local state."""
    if purge and sha256:
        raise invalid("uninstall_purge_conflict")
    updates = paths.home / "updates"
    runtime = paths.home / "runtime"
    lock = InstanceLock(paths)
    try:
        lock.acquire()
    except BusinessError as exc:
        if exc.code == "instance_busy":
            raise invalid("uninstall_instance_running") from None
        raise
    try:
        if purge:
            if not paths.home.is_dir():
                raise invalid("uninstall_missing")
            if (paths.home / "restore-in-progress.json").exists():
                raise invalid("uninstall_restore_pending")
            backups = []
            if updates.is_dir() and not updates.is_symlink():
                for stage in sorted(updates.iterdir()):
                    record = stage / "backup.json"
                    if record.is_file():
                        try:
                            backups.append(str(json.loads(record.read_text(encoding="utf-8")).get("destination", "")))
                        except (ValueError, OSError, UnicodeError):
                            pass
            purged = []
            for name in PURGE_DIRS:
                target = paths.home / name
                if target.is_symlink() or not _inside(target, paths.home):
                    raise invalid("uninstall_unsafe_path")
                if target.is_dir():
                    shutil.rmtree(target)
                    purged.append(name)
            for name in PURGE_FILES:
                target = paths.home / name
                if target.is_symlink():
                    raise invalid("uninstall_unsafe_path")
                if target.exists():
                    target.unlink()
                    purged.append(name)
            if not purged:
                raise invalid("uninstall_missing")
            return {
                "format": FORMAT,
                "wheel_sha256": None,
                "deactivated": "runtime" in purged,
                "removed": "updates" in purged,
                "purged": purged,
                "data_kept": False,
                "backup_kept": next((item for item in backups if item), None),
            }
        active = _active_sha(runtime)
        stage = updates / sha256[:16] if sha256 else None
        if sha256 and (stage is None or not stage.is_dir()):
            raise invalid("uninstall_missing")
        deactivated = False
        if sha256 and active != sha256:
            raise invalid("uninstall_not_active")
        backup_kept = None
        if stage is not None:
            backup = stage / "backup.json"
            if backup.is_file():
                try:
                    backup_kept = str(json.loads(backup.read_text(encoding="utf-8")).get("destination", "")) or None
                except (ValueError, OSError, UnicodeError):
                    backup_kept = None
        if active and (not sha256 or active == sha256):
            for name in POINTER_FILES:
                (runtime / name).unlink(missing_ok=True)
            for name in ("omni.cmd", "omni"):
                (paths.home / name).unlink(missing_ok=True)
            deactivated = True
        removed = False
        if stage is not None:
            _remove_stage(stage, updates)
            removed = True
        if not deactivated and not removed:
            raise invalid("uninstall_missing")
    finally:
        lock.close()
    return {
        "format": FORMAT,
        "wheel_sha256": sha256 or active,
        "deactivated": deactivated,
        "removed": removed,
        "data_kept": True,
        "backup_kept": backup_kept,
    }
