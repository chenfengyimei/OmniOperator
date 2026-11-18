"""Remove one staged candidate or deactivate the runtime pointer; data and installation receipts stay."""

from __future__ import annotations

import json
import os
import shutil
import sys
import time
from pathlib import Path
from typing import cast

from omni.adapters.persistence.maintenance import durable_json
from omni.adapters.platform.releases.dependency_bundle import SHA, no_links
from omni.adapters.platform.releases.wheel_install import FORMAT as RUNTIME_FORMAT
from omni.adapters.platform.runtime.paths import InstanceLock, InstancePaths
from omni.adapters.platform.runtime.permissions import private_path
from omni.adapters.platform.runtime.runtime_identity import read_private
from omni.application.ports import Record
from omni.domain.contracts import BusinessError

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
    for attempt in range(4):
        try:
            no_links(stage)
        except BusinessError:
            raise invalid("uninstall_unsafe_path") from None
        if not _inside(stage, updates) or stage.resolve() == updates.resolve():
            raise invalid("uninstall_unsafe_path")
        try:
            shutil.rmtree(stage)
            return
        except OSError as exc:
            if isinstance(exc, FileNotFoundError) and not stage.exists():
                return
            # Antivirus/file-indexer locks can disappear while a large venv is removed.
            # Retry only Windows sharing/access failures, retaining the final failure.
            if os.name != "nt" or getattr(exc, "winerror", None) not in {2, 3, 5, 32, 33} or attempt == 3:
                raise
            time.sleep((0.1, 0.25, 0.5)[attempt])


def _removal_receipt(runtime: Path, sha256: str) -> Record | None:
    path = runtime / "uninstall.json"
    no_links(path)
    if not path.exists():
        return None
    try:
        record = json.loads(read_private(path))
    except (ValueError, OSError, UnicodeError):
        raise invalid("uninstall_receipt_invalid") from None
    if not isinstance(record, dict):
        raise invalid("uninstall_receipt_invalid")
    receipt = cast(Record, record)
    if receipt.get("format") != FORMAT or receipt.get("phase") not in ("deactivated", "removed"):
        raise invalid("uninstall_receipt_invalid")
    return receipt if receipt.get("wheel_sha256") == sha256 else None


def _record_removal(runtime: Path, sha256: str, phase: str, backup_kept: str | None) -> None:
    path = runtime / "uninstall.json"
    no_links(path)
    durable_json(
        path,
        {"format": FORMAT, "wheel_sha256": sha256, "phase": phase, "data_kept": True, "backup_kept": backup_kept},
    )
    private_path(path)


def _active_sha(runtime: Path) -> str | None:
    path = runtime / "current.json"
    if path.is_symlink() or path.is_junction() or (path.exists() and not path.is_file()):
        raise invalid("uninstall_pointer_invalid")
    if not path.is_file():
        return None
    try:
        record = json.loads(path.read_text(encoding="utf-8"))
    except (ValueError, OSError, UnicodeError):
        raise invalid("uninstall_pointer_invalid") from None
    if not isinstance(record, dict) or cast(Record, record).get("format") != RUNTIME_FORMAT:
        raise invalid("uninstall_pointer_invalid")
    return str(cast(Record, record).get("wheel_sha256", "")) or None


def uninstall_candidate(paths: InstancePaths, sha256: str = "", purge: bool = False) -> dict[str, object]:
    """With SHA remove that candidate; without SHA only deactivate; purge explicitly clears local state."""
    if purge and sha256:
        raise invalid("uninstall_purge_conflict")
    if sha256 and SHA.fullmatch(sha256) is None:
        raise invalid()
    updates = paths.home / "updates"
    runtime = paths.home / "runtime"
    removal_root = paths.home if purge else updates / sha256[:16] if sha256 else None
    if removal_root is not None and any(
        path.resolve().is_relative_to(removal_root.resolve()) for path in (Path(sys.executable), Path(__file__))
    ):
        raise invalid("uninstall_external_manager_required")
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
            backups: list[str] = []
            if updates.is_dir() and not updates.is_symlink():
                for stage in sorted(updates.iterdir()):
                    record = stage / "backup.json"
                    if record.is_file():
                        try:
                            backups.append(str(json.loads(record.read_text(encoding="utf-8")).get("destination", "")))
                        except (ValueError, OSError, UnicodeError):
                            pass
            purged: list[str] = []
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
        receipt = _removal_receipt(runtime, sha256) if sha256 else None
        resuming = bool(sha256 and active is None and receipt is not None)
        if sha256 and (stage is None or not stage.is_dir()) and not resuming:
            raise invalid("uninstall_missing")
        deactivated = False
        if sha256 and active != sha256 and not resuming:
            raise invalid("uninstall_not_active")
        backup_kept = None
        if resuming and receipt is not None:
            value = receipt.get("backup_kept")
            backup_kept = value if isinstance(value, str) else None
        if stage is not None:
            backup = stage / "backup.json"
            if backup.is_file():
                try:
                    backup_kept = str(json.loads(backup.read_text(encoding="utf-8")).get("destination", "")) or None
                except (ValueError, OSError, UnicodeError):
                    backup_kept = None
        if sha256 and active == sha256:
            # Persist the exact removal authority before deactivating the pointer.
            # If deletion fails, another command can resume only this candidate.
            _record_removal(runtime, sha256, "deactivated", backup_kept)
        if (active and (not sha256 or active == sha256)) or resuming:
            for name in POINTER_FILES:
                (runtime / name).unlink(missing_ok=True)
            for name in ("omni.cmd", "omni"):
                (paths.home / name).unlink(missing_ok=True)
            deactivated = True
        removed = False
        if stage is not None and stage.is_dir():
            try:
                _remove_stage(stage, updates)
            except OSError as exc:
                if os.name == "nt" and getattr(exc, "winerror", None) in {2, 3, 5, 32, 33}:
                    raise BusinessError(
                        "uninstall_cleanup_pending",
                        "Candidate deactivated; retry this uninstall with the external manager after file access settles",
                        retryable=True,
                        status=409,
                    ) from exc
                raise
            removed = True
        if sha256:
            _record_removal(runtime, sha256, "removed", backup_kept)
        if not deactivated and not removed and not resuming:
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
