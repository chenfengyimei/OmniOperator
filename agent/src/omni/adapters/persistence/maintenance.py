"""Exclusive offline snapshots and restore. Live SQLite files are never copied directly."""

from __future__ import annotations

import datetime as dt
import hashlib
import json
import shutil
import sqlite3
from pathlib import Path

from ...domain.contracts import BusinessError
from ..platform.paths import InstanceLock, InstancePaths


def fingerprint(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        while block := stream.read(1_048_576):
            digest.update(block)
    return digest.hexdigest()


def safe_child(root: Path, relative: str) -> Path:
    fragment = Path(relative)
    if fragment.is_absolute() or ".." in fragment.parts or any(":" in part for part in fragment.parts):
        raise BusinessError("unsafe_manifest", "Snapshot contains an unsafe path")
    candidate = root / fragment
    if not candidate.resolve().is_relative_to(root.resolve()):
        raise BusinessError("unsafe_manifest", "Snapshot path escapes its root")
    for path in (candidate, *candidate.parents):
        if not path.is_relative_to(root):
            break
        if path.is_symlink() or path.is_junction():
            raise BusinessError("unsafe_manifest", "Snapshot paths cannot contain links")
    return candidate


def inspect_database(path: Path) -> dict:
    if not path.is_file() or path.is_symlink():
        raise BusinessError("database_missing", "V2 database does not exist")
    with sqlite3.connect(path.resolve().as_uri() + "?mode=ro", uri=True) as connection:
        tables = {row[0] for row in connection.execute("SELECT name FROM sqlite_master WHERE type='table'")}
        if not {"instances", "alembic_version"}.issubset(tables):
            raise BusinessError("foreign_database", "Database is not a V2 instance")
        return {
            "integrity": connection.execute("PRAGMA integrity_check").fetchone()[0],
            "foreign_key_errors": len(connection.execute("PRAGMA foreign_key_check").fetchall()),
            "schema": connection.execute("SELECT version_num FROM alembic_version").fetchone()[0],
            "tables": len(tables),
            "agents": connection.execute("SELECT count(*) FROM agents").fetchone()[0],
        }


def backup(paths: InstancePaths, destination: Path) -> dict:
    destination = destination.absolute()
    if destination.resolve().is_relative_to(paths.home.resolve()) or paths.home.resolve().is_relative_to(
        destination.resolve()
    ):
        raise BusinessError("unsafe_destination", "Backup must be outside the instance home")
    lock = InstanceLock(paths)
    lock.acquire()
    try:
        if destination.exists():
            raise BusinessError(
                "destination_exists", "Use a new backup directory; partial snapshots are never overwritten"
            )
        inspect_database(paths.database)
        destination.mkdir(parents=True, mode=0o700)
        target = destination / "data/omni.db"
        target.parent.mkdir()
        with (
            sqlite3.connect(paths.database.resolve().as_uri() + "?mode=ro", uri=True) as source,
            sqlite3.connect(target) as output,
        ):
            source.backup(output)
        files = []
        for path in sorted(paths.objects.rglob("*")):
            if path.is_symlink() or path.is_junction():
                raise BusinessError("unsafe_object", "Object store contains a link")
            if path.is_file():
                relative = path.relative_to(paths.home).as_posix()
                out = safe_child(destination, relative)
                out.parent.mkdir(parents=True, exist_ok=True)
                shutil.copyfile(path, out)
                files.append({"path": relative, "sha256": fingerprint(out), "bytes": out.stat().st_size})
        files.insert(0, {"path": "data/omni.db", "sha256": fingerprint(target), "bytes": target.stat().st_size})
        manifest = {
            "format": "omni-v2-backup-1",
            "complete": True,
            "created_at": dt.datetime.now(dt.timezone.utc).isoformat(),
            "database": inspect_database(target),
            "files": files,
            "excluded": ["credentials", "owner_token", "browser_profiles", "cache", "diagnostic_logs"],
        }
        if manifest["database"]["integrity"] != "ok" or manifest["database"]["foreign_key_errors"]:
            raise BusinessError("backup_integrity_failed", "Snapshot failed integrity validation")
        temporary = destination / "manifest.partial"
        temporary.write_text(json.dumps(manifest, ensure_ascii=False, indent=2), encoding="utf-8")
        temporary.replace(destination / "manifest.json")
        return manifest
    finally:
        lock.close()


def restore(source: Path, destination: Path) -> dict:
    source, destination = source.resolve(), destination.absolute()
    if source.is_relative_to(destination.resolve()) or destination.resolve().is_relative_to(source):
        raise BusinessError("unsafe_destination", "Restore must be outside the backup directory")
    manifest_path = safe_child(source, "manifest.json")
    try:
        manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    except (OSError, ValueError) as exc:
        raise BusinessError("incomplete_backup", "A complete validated backup manifest is required") from exc
    if manifest.get("format") != "omni-v2-backup-1" or manifest.get("complete") is not True:
        raise BusinessError("incomplete_backup", "Backup format or completion marker is invalid")
    if len(manifest.get("files", [])) > 100_000:
        raise BusinessError("unsafe_manifest", "Backup manifest is too large")
    names = set()
    for entry in manifest["files"]:
        path = safe_child(source, entry["path"])
        safe_child(destination, entry["path"])
        if (
            entry["path"] in names
            or not path.is_file()
            or path.stat().st_size != entry["bytes"]
            or fingerprint(path) != entry["sha256"]
        ):
            raise BusinessError("backup_fingerprint_changed", "A backup file changed or is duplicated")
        names.add(entry["path"])
    if "data/omni.db" not in names:
        raise BusinessError("incomplete_backup", "Backup database is missing")
    manifest_hash = fingerprint(manifest_path)
    report_path = destination / "restore-report.json"
    if destination.exists():
        if report_path.is_file() and not report_path.is_symlink():
            previous = json.loads(report_path.read_text())
            if previous["manifest_sha256"] == manifest_hash and all(
                fingerprint(safe_child(destination, item["path"])) == item["sha256"] for item in previous["files"]
            ):
                return {**previous, "already_restored": True}
        raise BusinessError("destination_exists", "Restore requires an empty new directory")
    destination.mkdir(parents=True, mode=0o700)
    for entry in manifest["files"]:
        target = safe_child(destination, entry["path"])
        target.parent.mkdir(parents=True, exist_ok=True)
        shutil.copyfile(safe_child(source, entry["path"]), target)
    database = destination / "data/omni.db"
    with sqlite3.connect(database) as connection:
        connection.execute("UPDATE auth_sessions SET revoked_at = ?", (dt.datetime.now(dt.timezone.utc).isoformat(),))
    report = {
        "manifest_sha256": manifest_hash,
        "database": inspect_database(database),
        "credentials_restored": False,
        "files": [
            {"path": entry["path"], "sha256": fingerprint(destination / entry["path"])} for entry in manifest["files"]
        ],
    }
    if report["database"]["integrity"] != "ok" or report["database"]["foreign_key_errors"]:
        raise BusinessError("restore_integrity_failed", "Restored database failed validation")
    report_path.write_text(json.dumps(report, indent=2), encoding="utf-8")
    return report
