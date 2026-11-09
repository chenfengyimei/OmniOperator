"""Offline object registry snapshots and bounded physical inventory validation."""

from __future__ import annotations

import hashlib
import re
import sqlite3
import uuid
from pathlib import Path

from ...domain.contracts import BusinessError
from ...domain.objects import MAX_OBJECT_BYTES, Blob, agent_identity
from ..object_store import checked_path
from .sqlite_access import sqlite_connection

MAX_INVENTORY = 100_000


def registry(database: Path) -> tuple[str, str, list[dict], set[str]]:
    if database.is_symlink() or database.is_junction() or not database.is_file():
        raise BusinessError("database_missing", "A regular V2 database is required")
    with sqlite_connection(database) as connection:
        connection.row_factory = sqlite3.Row
        connection.execute("BEGIN")
        schema = connection.execute("SELECT version_num FROM alembic_version").fetchone()[0]
        instance_row = connection.execute("SELECT id FROM instances WHERE slot=1").fetchone()
        instance = instance_row[0] if instance_row else ""
        rows = [
            dict(row) for row in connection.execute("SELECT * FROM objects ORDER BY id LIMIT ?", (MAX_INVENTORY + 1,))
        ]
        if len(rows) > MAX_INVENTORY:
            raise BusinessError("inventory_limit", "Object inventory exceeds the maintenance page limit")
        tables = {r[0] for r in connection.execute("SELECT name FROM sqlite_master WHERE type='table'")}
        refs = set()
        for table in tables:
            if table == "image_objects":
                # Non-owning metadata cascades with its object. Message/call
                # references, not verification alone, determine retention.
                continue
            quoted_table = '"' + table.replace('"', '""') + '"'
            for foreign_key in connection.execute(f"PRAGMA foreign_key_list({quoted_table})"):
                if foreign_key["table"] == "objects" and foreign_key["to"] == "id":
                    column = '"' + foreign_key["from"].replace('"', '""') + '"'
                    refs.update(
                        row[0]
                        for row in connection.execute(
                            f"SELECT DISTINCT {column} FROM {quoted_table} WHERE {column} IS NOT NULL"
                        )
                    )
    for row in rows:
        Blob(row["agent_id"], row["sha256"], row["bytes"])
    return instance, schema, rows, refs


def signature(root: Path, key: str) -> dict | None:
    path = checked_path(root, key)
    if not path.exists():
        return None
    info = path.stat()
    if info.st_size > MAX_OBJECT_BYTES:
        raise BusinessError("object_integrity_failed", "Object exceeds its physical size limit", status=503)
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        while block := stream.read(1_048_576):
            digest.update(block)
    after = path.stat()
    if (info.st_ino, info.st_size, info.st_mtime_ns) != (after.st_ino, after.st_size, after.st_mtime_ns):
        raise BusinessError("object_changed", "An object changed during inspection")
    return {
        "sha256": digest.hexdigest(),
        "bytes": info.st_size,
        "mtime_ns": info.st_mtime_ns,
        "inode": info.st_ino,
        "device": info.st_dev,
    }


def physical_keys(root: Path):
    if root.is_symlink() or root.is_junction():
        raise BusinessError("unsafe_object_path", "Object store cannot be a link")
    if not root.exists():
        return
    count = 0
    for agent in sorted(root.iterdir()):
        try:
            agent_identity(agent.name)
        except BusinessError:
            continue  # Unrelated files are never maintenance targets.
        if agent.is_symlink() or agent.is_junction():
            raise BusinessError("unsafe_object_path", "Agent object directory cannot be a link")
        if not agent.is_dir():
            continue
        for prefix in sorted(agent.iterdir()):
            if not re.fullmatch(r"[0-9a-f]{2}", prefix.name):
                continue
            if prefix.is_symlink() or prefix.is_junction():
                raise BusinessError("unsafe_object_path", "Object prefix cannot be a link")
            if not prefix.is_dir():
                continue
            for path in sorted(prefix.iterdir()):
                if re.fullmatch(r"[0-9a-f]{64}\.blob|\.[0-9a-f-]{36}\.partial", path.name):
                    if path.name.endswith(".blob") and path.name[:2] != prefix.name:
                        continue
                    if path.name.endswith(".partial"):
                        try:
                            if str(uuid.UUID(path.name[1:-8])) != path.name[1:-8]:
                                continue
                        except ValueError:
                            continue
                    count += 1
                    if count > MAX_INVENTORY:
                        raise BusinessError("inventory_limit", "Physical object inventory exceeds its limit")
                    yield path.relative_to(root).as_posix()


def verify_objects(database: Path, root: Path) -> list[dict]:
    _, _, rows, _ = registry(database)
    files = []
    for row in rows:
        blob = Blob(row["agent_id"], row["sha256"], row["bytes"])
        current = signature(root, blob.key)
        if current is None or current["sha256"] != blob.sha256 or current["bytes"] != blob.size:
            raise BusinessError("object_integrity_failed", "Registered object is missing or has changed", status=503)
        files.append({"path": "objects/" + blob.key, "sha256": blob.sha256, "bytes": blob.size})
    return files
