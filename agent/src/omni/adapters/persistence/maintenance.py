"""Exclusive offline snapshots and restore. Live SQLite files are never copied directly."""

from __future__ import annotations

import datetime as dt
import hashlib
import json
import os
import shutil
import sqlite3
import uuid
from itertools import islice
from pathlib import Path
from typing import Any

from ...application.ports import Record
from ...domain.contracts import BusinessError
from ..object_store import publish_file, sync_directory
from ..platform.paths import InstanceLock, InstancePaths
from ..platform.permissions import private_path
from .history_sequence import allocate_history
from .object_inventory import registry, verify_objects
from .sqlite_access import sqlite_connection


def _rebuild_fulltext(connection: sqlite3.Connection, table: str) -> None:
    # Names come only from the fixed registry below, never from CLI input.
    connection.execute(f"INSERT INTO {table}({table}) VALUES('rebuild')")


def derived_indexes(paths: InstancePaths, *, rebuild: bool = False) -> dict:
    """Exclusive FTS/content consistency check; only explicit rebuild commits changes."""
    lock = InstanceLock(paths)
    lock.acquire()
    try:
        inspect_database(paths.database)
        with sqlite_connection(paths.database, read_only=False) as connection:
            available = {r[0] for r in connection.execute("SELECT name FROM sqlite_master WHERE type='table'")}
            required = {"memory_fts", "document_fts"} if "document_chunks" in available else {"memory_fts"}
            if not required <= available:
                raise BusinessError(
                    "derived_index_missing",
                    "A full-text schema is missing; restore a verified schema snapshot",
                    status=503,
                )
            tables = sorted(required)
            connection.execute("BEGIN IMMEDIATE" if rebuild else "BEGIN")
            try:
                for table in tables:
                    if rebuild:
                        _rebuild_fulltext(connection, table)
                    connection.execute(f"INSERT INTO {table}({table},rank) VALUES('integrity-check',1)")
                if rebuild:
                    now = dt.datetime.now(dt.timezone.utc).isoformat(timespec="microseconds")
                    for (agent_id,) in connection.execute("SELECT id FROM agents"):
                        connection.execute(
                            "INSERT INTO audit(sequence,id,agent_id,created_at,kind,data) VALUES(?,?,?,?,'indexes.rebuilt',?)",
                            (
                                allocate_history(connection, "audit") if "history_sequences" in available else None,
                                str(uuid.uuid4()),
                                agent_id,
                                now,
                                json.dumps({"indexes": tables}),
                            ),
                        )
                    connection.commit()
                else:
                    connection.rollback()
            except sqlite3.DatabaseError as exc:
                raise BusinessError(
                    "derived_index_invalid",
                    "Full-text indexes do not match their content; run the offline rebuild",
                    status=503,
                ) from exc
            return {
                "ok": True,
                "rebuilt": rebuild,
                "indexes": tables,
                "scope": "FTS-to-content consistency; original object integrity is checked separately",
                "source_rows_unchanged": True,
                "vectors_rebuilt": False,
            }
    finally:
        lock.close()


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


def read_schema(path: Path) -> str:
    if not path.is_file() or path.is_symlink():
        raise BusinessError("database_missing", "V2 database does not exist")
    try:
        with sqlite_connection(path) as connection:
            row = connection.execute("SELECT version_num FROM alembic_version").fetchone()
    except sqlite3.Error:
        raise BusinessError("database_unreadable", "V2 database schema cannot be read", status=503) from None
    return str(row[0]) if row else ""


def migrate_database(path: Path) -> Record:
    """Apply the packaged Alembic head with exclusive ownership; never runs on an owned instance."""
    from alembic import command

    from .migration_config import configuration, head

    before = read_schema(path)
    config = configuration()
    target = head(config)
    if before == target:
        return {
            "format": "omni-schema-migration-1",
            "before": before,
            "after": before,
            "target": target,
            "migrated": False,
        }
    try:
        from .sqlalchemy_access import database_engine

        engine = database_engine(path)
        try:
            with engine.connect() as connection:
                connection.info["omni_write"] = True
                config.attributes["connection"] = connection
                command.upgrade(config, "head")
                if connection.in_transaction():
                    connection.commit()
                after = str(connection.exec_driver_sql("SELECT version_num FROM alembic_version").scalar_one())
        finally:
            engine.dispose()
    except BusinessError:
        raise
    except Exception as exc:
        raise BusinessError("migration_failed", "Database migration failed and was rolled back", status=503) from exc
    if after != target:
        raise BusinessError("migration_incomplete", "Database migration did not reach the packaged head", status=503)
    return {
        "format": "omni-schema-migration-1",
        "before": before,
        "after": after,
        "target": target,
        "migrated": True,
    }


def inspect_database(path: Path, *, allow_restore_in_progress: bool = False) -> Record:
    if not allow_restore_in_progress and (path.parent.parent / "restore-in-progress.json").exists():
        raise BusinessError("restore_incomplete", "Restore completion has not been verified", status=503)
    if not path.is_file() or path.is_symlink():
        raise BusinessError("database_missing", "V2 database does not exist")
    with sqlite_connection(path) as connection:
        tables = {row[0] for row in connection.execute("SELECT name FROM sqlite_master WHERE type='table'")}
        if not {"instances", "alembic_version"}.issubset(tables):
            raise BusinessError("foreign_database", "Database is not a V2 instance")
        if "action_review_heads" in tables:
            from sqlalchemy import func, or_, select
            from sqlalchemy.dialects.sqlite import dialect

            from . import schema as s
            from .action_predicates import unresolved_action

            statement = (
                select(func.count())
                .select_from(s.actions)
                .where(
                    s.actions.c.writes.is_(True),
                    or_(s.actions.c.state == "unknown", s.actions.c.effect == "unknown"),
                    unresolved_action(),
                )
            )
            attention = connection.execute(
                str(statement.compile(dialect=dialect(), compile_kwargs={"literal_binds": True}))
            ).fetchone()[0]
        else:
            attention = connection.execute(
                "SELECT COUNT(*) FROM actions WHERE writes=1 AND (state='unknown' OR effect='unknown')"
            ).fetchone()[0]
        return {
            "integrity": connection.execute("PRAGMA integrity_check").fetchone()[0],
            "foreign_key_errors": len(connection.execute("PRAGMA foreign_key_check").fetchall()),
            "schema": connection.execute("SELECT version_num FROM alembic_version").fetchone()[0],
            "tables": len(tables),
            "agents": connection.execute("SELECT count(*) FROM agents").fetchone()[0],
            "schedule_attention": connection.execute(
                "SELECT count(*) FROM schedule_progress WHERE error_code!=''"
            ).fetchone()[0]
            if "schedule_progress" in tables
            else 0,
            "unknown_model_calls": connection.execute(
                "SELECT count(*) FROM model_calls WHERE state IN ('reserved','unknown')"
            ).fetchone()[0],
            "unknown_actions": connection.execute(
                "SELECT count(*) FROM actions WHERE state IN ('executing','unknown')"
            ).fetchone()[0],
            "active_terminals": connection.execute(
                "SELECT count(*) FROM terminal_sessions WHERE state IN ('starting','running','closing')"
            ).fetchone()[0]
            if "terminal_sessions" in tables
            else 0,
            "terminal_attention": connection.execute(
                "SELECT count(*) FROM terminal_sessions WHERE cleanup_pending=1 OR state='unknown'"
            ).fetchone()[0]
            if "terminal_sessions" in tables
            else 0,
            "unknown_terminal_operations": connection.execute(
                "SELECT count(*) FROM terminal_operations WHERE state IN ('prepared','unknown')"
            ).fetchone()[0]
            if "terminal_operations" in tables
            else 0,
            "action_review_attention": attention,
            "unknown_deliveries": connection.execute(
                "SELECT count(*) FROM outbox WHERE state IN ('sending','unknown')"
            ).fetchone()[0],
            "delivery_attention": connection.execute(
                "SELECT count(*) FROM outbox WHERE state='blocked' OR (state='unknown' AND (reviewed_epoch!=review_epoch OR resolution IS NULL))"
            ).fetchone()[0]
            if "delivery_attempts" in tables
            else connection.execute("SELECT count(*) FROM outbox WHERE state IN ('sending','unknown')").fetchone()[0],
            "pending_deliveries": connection.execute(
                "SELECT count(*) FROM outbox WHERE state IN ('awaiting_approval','approved','claimed','sending','retry_wait','blocked')"
            ).fetchone()[0]
            if "delivery_attempts" in tables
            else 0,
            "held_dispatch_resources": connection.execute(
                "SELECT count(*) FROM dispatch_resources WHERE state='held'"
            ).fetchone()[0]
            if "dispatch_resources" in tables
            else 0,
            "document_erasure_attention": connection.execute(
                "SELECT count(*) FROM document_erasure_jobs WHERE state != 'done'"
            ).fetchone()[0]
            if "document_erasure_jobs" in tables
            else 0,
            "document_index_building": connection.execute(
                "SELECT count(*) FROM document_index_builds WHERE state='building'"
            ).fetchone()[0]
            if "document_index_builds" in tables
            else 0,
            "execution_retention_attention": connection.execute(
                "SELECT count(*) FROM retention_batches WHERE state='prepared'"
            ).fetchone()[0]
            if "retention_batches" in tables
            else 0,
            "reference_release_attention": connection.execute(
                "SELECT count(*) FROM reference_release_batches WHERE state='prepared'"
            ).fetchone()[0]
            if "reference_release_batches" in tables
            else 0,
            "audit_retention_attention": connection.execute(
                "SELECT count(*) FROM audit_retention_batches WHERE state='prepared'"
            ).fetchone()[0]
            if "audit_retention_batches" in tables
            else 0,
            "event_retention_attention": connection.execute(
                "SELECT count(*) FROM event_retention_batches WHERE state='prepared'"
            ).fetchone()[0]
            if "event_retention_batches" in tables
            else 0,
            "maintenance_attention": connection.execute(
                "SELECT count(*) FROM maintenance_jobs WHERE state IN ('queued','claimed','running','cancelling')"
            ).fetchone()[0]
            if "maintenance_jobs" in tables
            else 0,
            "memory_organization_attention": connection.execute(
                "SELECT count(*) FROM memory_organization_batches WHERE state IN ('queued','ready')"
            ).fetchone()[0]
            if "memory_organization_batches" in tables
            else 0,
            "memory_index_building": connection.execute(
                "SELECT count(*) FROM memory_embeddings WHERE state='building'"
            ).fetchone()[0]
            if "memory_embeddings" in tables
            else 0,
            "memory_semantic_pending": connection.execute(
                "SELECT count(*) FROM memory_semantic_links WHERE state='pending'"
            ).fetchone()[0]
            if "memory_semantic_links" in tables
            else 0,
            "enabled_extensions": connection.execute("SELECT count(*) FROM extensions WHERE enabled=1").fetchone()[0]
            if "extension_revisions" in tables
            else 0,
            "extension_attention": connection.execute(
                "SELECT count(*) FROM extensions WHERE state IN ('drifted','missing') OR error_code!=''"
            ).fetchone()[0]
            if "extension_revisions" in tables
            else 0,
        }


def backup(paths: InstancePaths, destination: Path) -> Record:
    destination = destination.absolute()
    if destination.resolve().is_relative_to(paths.home.resolve()) or paths.home.resolve().is_relative_to(
        destination.resolve()
    ):
        raise BusinessError("unsafe_destination", "Backup must be outside the instance home")
    lock = InstanceLock(paths)
    lock.acquire()
    try:
        return create_snapshot(paths.database, paths.objects, destination)
    finally:
        lock.close()


def create_snapshot(database: Path, objects_root: Path | None, destination: Path) -> Record:
    """Caller holds exclusive instance ownership, including before schema upgrades."""
    if destination.exists() or destination.is_symlink() or destination.is_junction():
        raise BusinessError("destination_exists", "Use a new backup directory; partial snapshots are never overwritten")
    inspect_database(database)
    destination.mkdir(parents=True, mode=0o700)
    private_path(destination, directory=True)
    target = destination / "data/omni.db"
    target.parent.mkdir(mode=0o700)
    with (
        sqlite_connection(database) as source,
        sqlite_connection(target, read_only=False) as output,
    ):
        source.backup(output)
    _, _, rows, _ = registry(target)
    if rows and objects_root is None:
        raise BusinessError("object_store_required", "Schema backup requires the registered object store")
    files = verify_objects(target, objects_root) if objects_root is not None else []
    for entry in files:
        if objects_root is None:
            raise BusinessError("object_store_required", "Snapshot requires its object store")
        relative = entry["path"]
        out = safe_child(destination, relative)
        out.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
        shutil.copyfile(safe_child(objects_root, relative.removeprefix("objects/")), out)
        with out.open("r+b") as stream:
            os.fsync(stream.fileno())
        if fingerprint(out) != entry["sha256"] or out.stat().st_size != entry["bytes"]:
            raise BusinessError("object_changed", "An object changed during snapshot creation")
        sync_directory(out.parent)
    with target.open("r+b") as stream:
        os.fsync(stream.fileno())
    files.insert(0, {"path": "data/omni.db", "sha256": fingerprint(target), "bytes": target.stat().st_size})
    manifest = {
        "format": "omni-v2-backup-1",
        "complete": True,
        "created_at": dt.datetime.now(dt.timezone.utc).isoformat(),
        "database": inspect_database(target),
        "files": files,
        "excluded": [
            "unregistered_objects",
            "partial_objects",
            "credentials",
            "owner_token",
            "browser_profiles",
            "cache",
            "diagnostic_logs",
        ],
    }
    if manifest["database"]["integrity"] != "ok" or manifest["database"]["foreign_key_errors"]:
        raise BusinessError("backup_integrity_failed", "Snapshot failed integrity validation")
    durable_json(destination / "manifest.json", manifest)
    return manifest


def durable_json(path: Path, value: Record) -> None:
    if path.is_symlink() or path.is_junction():
        raise BusinessError("unsafe_manifest", "Maintenance records cannot be links")
    temporary = path.with_name(f".{uuid.uuid4()}.partial")
    try:
        with temporary.open("x", encoding="utf-8") as stream:
            json.dump(value, stream, ensure_ascii=False, indent=2)
            stream.flush()
            os.fsync(stream.fileno())
        # Journal replacement is explicit; object publication never overwrites.
        if path.exists():
            os.replace(temporary, path)
            sync_directory(path.parent)
        else:
            publish_file(temporary, path)
    finally:
        temporary.unlink(missing_ok=True)


def migration_snapshot(database: Path, objects_root: Path | None) -> Record:
    folder = database.parent / "schema-backups"
    if folder.is_symlink() or folder.is_junction():
        raise BusinessError("unsafe_backup_path", "Schema backup directory cannot be a link")
    return create_snapshot(database, objects_root, folder / str(uuid.uuid4()))


def object_report(paths: InstancePaths) -> dict:
    """Read-only, exclusive caller; no models or device actions."""
    try:
        files = verify_objects(paths.database, paths.objects)
        report = {"ok": True, "registered": len(files), "bytes": sum(item["bytes"] for item in files)}
    except (BusinessError, OSError) as exc:
        report = {"ok": False, "error_code": exc.code if isinstance(exc, BusinessError) else "object_io_failed"}
    directory = paths.control / "collection"
    if directory.is_symlink() or directory.is_junction():
        return {**report, "ok": False, "error_code": "unsafe_gc_journal"}
    pending = []
    scanned = 0
    for path in islice(directory.glob("*.json"), 301):
        scanned += 1
        try:
            if str(uuid.UUID(path.stem)) != path.stem:
                continue
        except ValueError:
            continue
        status_path = path.with_suffix(".status.json")
        if (
            status_path.is_symlink()
            or status_path.is_junction()
            or (status_path.exists() and status_path.stat().st_size > 8192)
        ):
            raise BusinessError("unsafe_gc_journal", "Collection status is not a bounded regular file")
        value = json.loads(status_path.read_text(encoding="utf-8")) if status_path.exists() else {}
        if value.get("complete") is not True:
            pending.append({"plan_id": path.stem, "phase": value.get("phase", "unknown")})
        if len(pending) >= 100:
            break
    return {
        **report,
        "pending_collection": pending,
        "collection_scan_limit_reached": scanned >= 301 or len(pending) >= 100,
    }


def _quarantine_restored_work(connection: sqlite3.Connection, manifest_hash: str) -> dict:
    """A snapshot cannot prove what happened externally after it was captured."""
    now = dt.datetime.now(dt.timezone.utc).isoformat(timespec="microseconds")
    tables = {r[0] for r in connection.execute("SELECT name FROM sqlite_master WHERE type='table'")}
    history_epoch = None
    if "history_identity" in tables:
        history_epoch = str(uuid.uuid4())
        connection.execute("UPDATE history_identity SET epoch=? WHERE slot=1", (history_epoch,))
    active = connection.execute(
        "SELECT id,agent_id,last_seq FROM runs WHERE state IN ('queued','running','waiting_approval','cancelling')"
    ).fetchall()
    for run_id, agent_id, seq in active:
        connection.execute(
            "UPDATE runs SET state='interrupted',error_code='backup_restored_requires_review',ended_at=?,last_seq=? WHERE id=?",
            (now, seq + 1, run_id),
        )
        connection.execute(
            "INSERT INTO run_events(sequence,agent_id,run_id,seq,created_at,kind,data) VALUES(?,?,?,?,?,'run.interrupted',?)",
            (
                allocate_history(connection, "events") if "history_sequences" in tables else None,
                agent_id,
                run_id,
                seq + 1,
                now,
                json.dumps({"error_code": "backup_restored_requires_review"}),
            ),
        )
    counts: dict[str, int | str] = {"interrupted_runs": len(active)}
    if "terminal_sessions" in tables:
        counts["unknown_terminals"] = connection.execute(
            "UPDATE terminal_sessions SET state='unknown',cleanup_pending=1,revision=revision+1,"
            "error_code='terminal_backup_restored',ended_at=NULL WHERE state!='exited'"
        ).rowcount
        connection.execute(
            "UPDATE terminal_operations SET state='unknown',error_code='terminal_backup_restored',ended_at=? "
            "WHERE state='prepared'",
            (now,),
        )
    if history_epoch is not None:
        counts["history_epoch"] = history_epoch
    counts["invalidated_action_review_heads"] = 0
    if "action_review_heads" in tables:
        heads = connection.execute("SELECT id,agent_id,action_id FROM action_review_heads").fetchall()
        counts["invalidated_action_review_heads"] = connection.execute(
            "UPDATE action_review_heads SET revision=revision+1,history_epoch=?,state='invalidated',"
            "resolution=NULL,last_review_id=NULL,updated_at=?,error_code='backup_restored_requires_review'",
            (history_epoch, now),
        ).rowcount
        for head_id, agent_id, action_id in heads:
            connection.execute(
                "INSERT INTO audit(sequence,id,agent_id,created_at,kind,operation_id,data) "
                "VALUES(?,?,?,?,'action_review.restored_invalidated',?,?)",
                (
                    allocate_history(connection, "audit"),
                    str(uuid.uuid4()),
                    agent_id,
                    now,
                    action_id,
                    json.dumps(
                        {
                            "schema": 1,
                            "head_id": head_id,
                            "manifest_sha256": manifest_hash,
                            "reason_code": "backup_restored_requires_review",
                            "original_outcome_preserved": True,
                        }
                    ),
                ),
            )
    counts["cancelled_audit_retention_batches"] = 0
    counts["cancelled_audit_retention_targets"] = 0
    if "audit_retention_batches" in tables:
        prepared = connection.execute(
            "SELECT id,agent_id FROM audit_retention_batches WHERE state='prepared'"
        ).fetchall()
        counts["cancelled_audit_retention_targets"] = connection.execute(
            "UPDATE audit_retention_targets SET state='cancelled',ended_at=? WHERE state='pending'", (now,)
        ).rowcount
        counts["cancelled_audit_retention_batches"] = connection.execute(
            "UPDATE audit_retention_batches SET state='cancelled',completed_at=? WHERE state='prepared'", (now,)
        ).rowcount
        for batch_id, agent_id in prepared:
            connection.execute(
                "INSERT INTO audit(sequence,id,agent_id,created_at,kind,operation_id,data) "
                "VALUES(?,?,?,?,'audit_retention.restored_cancelled',?,?)",
                (
                    allocate_history(connection, "audit"),
                    str(uuid.uuid4()),
                    agent_id,
                    now,
                    batch_id,
                    json.dumps(
                        {
                            "schema": 1,
                            "reason_code": "backup_restored_requires_review",
                            "manifest_sha256": manifest_hash,
                            "deleted_records_restored": False,
                        }
                    ),
                ),
            )
    counts["cancelled_event_retention_batches"] = 0
    counts["cancelled_event_retention_targets"] = 0
    if "event_retention_batches" in tables:
        prepared = connection.execute(
            "SELECT id,agent_id FROM event_retention_batches WHERE state='prepared'"
        ).fetchall()
        counts["cancelled_event_retention_targets"] = connection.execute(
            "UPDATE event_retention_targets SET state='cancelled',ended_at=? WHERE state='pending'", (now,)
        ).rowcount
        counts["cancelled_event_retention_batches"] = connection.execute(
            "UPDATE event_retention_batches SET state='cancelled',completed_at=? WHERE state='prepared'", (now,)
        ).rowcount
        for batch_id, agent_id in prepared:
            connection.execute(
                "INSERT INTO audit(sequence,id,agent_id,created_at,kind,operation_id,data) "
                "VALUES(?,?,?,?,'event_retention.restored_cancelled',?,?)",
                (
                    allocate_history(connection, "audit"),
                    str(uuid.uuid4()),
                    agent_id,
                    now,
                    batch_id,
                    json.dumps(
                        {
                            "schema": 1,
                            "reason_code": "backup_restored_requires_review",
                            "manifest_sha256": manifest_hash,
                            "deleted_records_restored": False,
                        }
                    ),
                ),
            )
    counts["cancelled_maintenance_jobs"] = 0
    counts["interrupted_maintenance_attempts"] = 0
    if "maintenance_jobs" in tables:
        pending = connection.execute(
            "SELECT id,agent_id FROM maintenance_jobs WHERE state IN ('queued','claimed','running','cancelling')"
        ).fetchall()
        counts["interrupted_maintenance_attempts"] = connection.execute(
            "UPDATE maintenance_attempts SET state='interrupted',ended_at=?,error_code='backup_restored_requires_review' "
            "WHERE state IN ('claimed','running')",
            (now,),
        ).rowcount
        counts["cancelled_maintenance_jobs"] = connection.execute(
            "UPDATE maintenance_jobs SET state='cancelled',cancel_requested_at=COALESCE(cancel_requested_at,?),"
            "finished_at=?,updated_at=?,revision=revision+1,error_code='backup_restored_requires_review' "
            "WHERE state IN ('queued','claimed','running','cancelling')",
            (now, now, now),
        ).rowcount
        for job_id, agent_id in pending:
            connection.execute(
                "INSERT INTO audit(sequence,id,agent_id,created_at,kind,operation_id,data) "
                "VALUES(?,?,?,?,'maintenance.restored_cancelled',?,?)",
                (
                    allocate_history(connection, "audit"),
                    str(uuid.uuid4()),
                    agent_id,
                    now,
                    job_id,
                    json.dumps(
                        {
                            "schema": 1,
                            "reason_code": "backup_restored_requires_review",
                            "manifest_sha256": manifest_hash,
                        }
                    ),
                ),
            )
    counts["cancelled_memory_organization_batches"] = 0
    counts["invalidated_memory_organization_batches"] = 0
    counts["invalidated_memory_organization_candidates"] = 0
    if "memory_organization_batches" in tables:
        cancelled_batches = invalidated_batches = 0
        affected = connection.execute(
            "SELECT id,agent_id,state FROM memory_organization_batches WHERE state='queued' OR "
            "(state IN ('ready','complete','cancelled') AND id IN "
            "(SELECT batch_id FROM memory_organization_candidates WHERE state IN ('pending','applied')))"
        ).fetchall()
        counts["invalidated_memory_organization_candidates"] = connection.execute(
            "UPDATE memory_organization_candidates SET state='invalidated',revision=revision+1,updated_at=?,"
            "error_code='backup_restored_requires_review' WHERE state IN ('pending','applied')",
            (now,),
        ).rowcount
        for batch_id, agent_id, state in affected:
            cancelled = state == "queued"
            target_state = "cancelled" if cancelled else "invalidated"
            changed = connection.execute(
                "UPDATE memory_organization_batches SET state=?,revision=revision+1,updated_at=?,"
                "error_code='backup_restored_requires_review' WHERE id=? AND agent_id=? AND state=?",
                (target_state, now, batch_id, agent_id, state),
            ).rowcount
            if changed != 1:
                raise BusinessError(
                    "restore_memory_organization_conflict", "Memory organization changed during restoration"
                )
            if cancelled:
                cancelled_batches += 1
            else:
                invalidated_batches += 1
            connection.execute(
                "INSERT INTO audit(sequence,id,agent_id,created_at,kind,operation_id,data) VALUES(?,?,?,?,?,?,?)",
                (
                    allocate_history(connection, "audit"),
                    str(uuid.uuid4()),
                    agent_id,
                    now,
                    "memory_organization.restored_" + target_state,
                    batch_id,
                    json.dumps(
                        {
                            "schema": 1,
                            "reason_code": "backup_restored_requires_review",
                            "manifest_sha256": manifest_hash,
                        }
                    ),
                ),
            )
        counts["cancelled_memory_organization_batches"] = cancelled_batches
        counts["invalidated_memory_organization_batches"] = invalidated_batches
    counts["disabled_skills"] = 0
    if "skills" in tables:
        reviewed = connection.execute("SELECT id,agent_id FROM skills WHERE state='enabled'").fetchall()
        counts["disabled_skills"] = connection.execute(
            "UPDATE skills SET state='disabled',revision=revision+1,authority_epoch=authority_epoch+1 WHERE state='enabled'"
        ).rowcount
        for identity, agent_id in reviewed:
            connection.execute(
                "INSERT INTO audit(sequence,id,agent_id,created_at,kind,operation_id,data) VALUES(?,?,?,?,'skill.restored_disabled',?,?)",
                (
                    allocate_history(connection, "audit"),
                    str(uuid.uuid4()),
                    agent_id,
                    now,
                    identity,
                    json.dumps({"reason_code": "backup_restored_requires_review", "manifest_sha256": manifest_hash}),
                ),
            )
    counts["disabled_extensions"] = 0
    counts["revoked_extension_grants"] = 0
    counts["cancelled_extension_actions"] = 0
    counts["cancelled_extension_approvals"] = 0
    if "extension_revisions" in tables:
        extensions = connection.execute("SELECT id,agent_id,authority_epoch FROM extensions").fetchall()
        counts["revoked_extension_grants"] = connection.execute(
            "UPDATE grants SET revoked_at=? WHERE revoked_at IS NULL", (now,)
        ).rowcount
        counts["disabled_extensions"] = connection.execute(
            "UPDATE extensions SET enabled=0,state='disabled',revision=revision+1,authority_epoch=authority_epoch+1,"
            "updated_at=?,error_code='backup_restored_requires_review'",
            (now,),
        ).rowcount
        # The common action quarantine below performs these state changes in
        # this same transaction. Captured tool bindings remain immutable proof.
        counts["cancelled_extension_actions"] = connection.execute(
            "SELECT COUNT(*) FROM actions a JOIN action_extension_bindings b ON b.action_id=a.id AND b.agent_id=a.agent_id "
            "WHERE a.state IN ('pending','approved')"
        ).fetchone()[0]
        counts["cancelled_extension_approvals"] = connection.execute(
            "SELECT COUNT(*) FROM approvals p JOIN action_extension_bindings b ON b.action_id=p.action_id AND b.agent_id=p.agent_id "
            "WHERE p.state IN ('pending','approved')"
        ).fetchone()[0]
        for identity, agent_id, authority_epoch in extensions:
            connection.execute(
                "INSERT INTO audit(sequence,id,agent_id,created_at,kind,operation_id,data) "
                "VALUES(?,?,?,?,'extension.restored_disabled',?,?)",
                (
                    allocate_history(connection, "audit"),
                    str(uuid.uuid4()),
                    agent_id,
                    now,
                    identity,
                    json.dumps(
                        {
                            "schema": 1,
                            "authority_epoch": authority_epoch + 1,
                            "reason_code": "backup_restored_requires_review",
                            "manifest_sha256": manifest_hash,
                        }
                    ),
                ),
            )
    if "credential_references" in tables:
        counts["quarantined_credential_operations"] = connection.execute(
            "UPDATE credential_operations SET state='unknown',revision=revision+1,ended_at=?,error_code='backup_restored_requires_review' WHERE state IN ('prepared','running')",
            (now,),
        ).rowcount
        counts["unverified_credentials"] = connection.execute(
            "UPDATE credential_references SET state='unknown',revision=revision+1,authority_epoch=authority_epoch+1,updated_at=?",
            (now,),
        ).rowcount
        for identity, owner_id, fingerprint, epoch in connection.execute(
            "SELECT id,owner_id,reference_sha256,authority_epoch FROM credential_references"
        ):
            connection.execute(
                "INSERT INTO instance_audit(id,created_at,owner_id,operation_id,kind,outcome,data) VALUES(?,?,?,?,'credential.restored','unknown',?)",
                (
                    str(uuid.uuid4()),
                    now,
                    owner_id,
                    str(uuid.uuid4()),
                    json.dumps(
                        {
                            "schema": 1,
                            "credential_id": identity,
                            "reference_sha256": fingerprint,
                            "authority_epoch": epoch,
                            "source": "offline_restore",
                            "manifest_sha256": manifest_hash,
                        }
                    ),
                ),
            )
    counts["revoked_owner_tokens"] = connection.execute(
        "UPDATE auth_sessions SET revoked_at=? WHERE revoked_at IS NULL", (now,)
    ).rowcount
    for name, statement in (
        (
            "quarantined_work",
            "UPDATE work_items SET state='unknown' WHERE kind='turn' AND state IN ('pending','claimed','running')",
        ),
        ("unknown_calls", "UPDATE model_calls SET state='unknown' WHERE state='reserved'"),
        ("unknown_actions", "UPDATE actions SET state='unknown',effect='unknown',ended_at=? WHERE state='executing'"),
        ("cancelled_intents", "UPDATE actions SET state='cancelled',ended_at=? WHERE state IN ('pending','approved')"),
        ("revoked_approvals", "UPDATE approvals SET state='cancelled' WHERE state IN ('pending','approved')"),
    ):
        counts[name] = connection.execute(statement, (now,) if "?" in statement else ()).rowcount
    if "delivery_attempts" in tables:
        counts["unknown_deliveries"] = connection.execute(
            "UPDATE outbox SET state='unknown',error_code='backup_restored_requires_review',revision=revision+1,outcome_revision=outcome_revision+1,approval_until=NULL WHERE state IN ('awaiting_approval','approved','claimed','sending','retry_wait')"
        ).rowcount
        connection.execute(
            "UPDATE delivery_attempts SET state='unknown',ended_at=?,error_code='backup_restored_requires_review' WHERE state IN ('claimed','sending')",
            (now,),
        )
        connection.execute(
            "UPDATE delivery_approvals SET state='revoked',revoked_at=?,revision=revision+1 WHERE state IN ('pending','approved')",
            (now,),
        )
        connection.execute("UPDATE outbox SET review_epoch=review_epoch+1,reviewed_epoch=0,revision=revision+1")
        connection.execute("UPDATE dispatch_resources SET state='released' WHERE state='held'")
    else:
        counts["unknown_deliveries"] = connection.execute(
            "UPDATE outbox SET state='unknown' WHERE state IN ('pending','sending')"
        ).rowcount
    counts["paused_schedules"] = 0
    if "schedule_progress" in tables:
        counts["paused_schedules"] = connection.execute(
            "UPDATE schedule_progress SET next_at=NULL,error_code='restored_schedule_paused',reviewed_sequence=0,review_epoch=review_epoch+1,revision=revision+1,updated_at=?",
            (now,),
        ).rowcount
        connection.execute(
            "UPDATE occurrences SET state='cancelled',error_code='backup_restored_requires_review',updated_at=? WHERE run_id IS NULL AND state IN ('pending','blocked')",
            (now,),
        )
        connection.execute("UPDATE leases SET state='released' WHERE state='held'")
    else:
        # Earlier V2 development schemas have no immutable definition ledger.
        counts["paused_schedules"] = connection.execute(
            "UPDATE schedules SET enabled=0,revision=revision+1 WHERE enabled=1"
        ).rowcount
    for (agent_id,) in connection.execute("SELECT id FROM agents").fetchall():
        connection.execute(
            "INSERT INTO audit(sequence,id,agent_id,created_at,kind,data) VALUES(?,?,?,?,'instance.restore_quarantined',?)",
            (
                allocate_history(connection, "audit") if "history_sequences" in tables else None,
                str(uuid.uuid4()),
                agent_id,
                now,
                json.dumps({"manifest_sha256": manifest_hash, "automatic_replay": False, "cost_holds_preserved": True}),
            ),
        )
    return counts


def _finish_restore_marker(destination: Path, manifest_hash: str) -> None:
    marker = safe_child(destination, "restore-in-progress.json")
    if marker.exists():
        if (
            marker.stat().st_size > 4096
            or json.loads(marker.read_text(encoding="utf-8")).get("manifest_sha256") != manifest_hash
        ):
            raise BusinessError("restore_marker_changed", "Restore marker does not match the verified snapshot")
        marker.unlink()
        sync_directory(destination)


def verify_snapshot(source: Path) -> Record:
    """Verify an immutable snapshot without opening a runtime or upgrading its schema."""
    source = source.absolute()
    manifest_path = safe_child(source, "manifest.json")
    try:
        if (
            not manifest_path.is_file()
            or manifest_path.stat().st_nlink != 1
            or manifest_path.stat().st_size > 64 * 1024 * 1024
        ):
            raise ValueError
        with manifest_path.open("rb") as stream:
            encoded = stream.read(64 * 1024 * 1024 + 1)
        if len(encoded) > 64 * 1024 * 1024:
            raise ValueError
        manifest = json.loads(encoded)
    except (OSError, ValueError, UnicodeError, RecursionError) as exc:
        raise BusinessError("incomplete_backup", "A complete validated backup manifest is required") from exc
    if (
        not isinstance(manifest, dict)
        or manifest.get("format") != "omni-v2-backup-1"
        or manifest.get("complete") is not True
        or not isinstance(manifest.get("files"), list)
    ):
        raise BusinessError("incomplete_backup", "Backup format or completion marker is invalid")
    # The registry permits 100000 objects; the database is one additional file.
    if len(manifest["files"]) > 100_001:
        raise BusinessError("unsafe_manifest", "Backup manifest is too large")
    names = set()
    for entry in manifest["files"]:
        if (
            not isinstance(entry, dict)
            or not isinstance(entry.get("path"), str)
            or len(entry["path"]) > 400
            or type(entry.get("bytes")) is not int
            or entry["bytes"] < 0
            or not isinstance(entry.get("sha256"), str)
            or len(entry["sha256"]) != 64
            or any(character not in "0123456789abcdef" for character in entry["sha256"])
        ):
            raise BusinessError("unsafe_manifest", "Snapshot entries must contain bounded paths")
        if entry["path"] != "data/omni.db" and not entry["path"].startswith("objects/"):
            raise BusinessError("unsafe_manifest", "Only the database and registered objects may be restored")
        path = safe_child(source, entry["path"])
        if (
            entry["path"] in names
            or not path.is_file()
            or path.stat().st_nlink != 1
            or path.stat().st_size != entry["bytes"]
            or fingerprint(path) != entry["sha256"]
        ):
            raise BusinessError("backup_fingerprint_changed", "A backup file changed or is duplicated")
        names.add(entry["path"])
    if "data/omni.db" not in names:
        raise BusinessError("incomplete_backup", "Backup database is missing")
    for name in ("omni.db-wal", "omni.db-shm", "omni.db-journal"):
        path = safe_child(source, "data/" + name)
        if path.exists() and (
            not path.is_file() or path.stat().st_nlink != 1 or (name != "omni.db-shm" and path.stat().st_size > 0)
        ):
            raise BusinessError("backup_fingerprint_changed", "Snapshot contains SQLite state outside its manifest")
    expected_objects = verify_objects(source / "data/omni.db", source / "objects")
    if names != {"data/omni.db", *(entry["path"] for entry in expected_objects)}:
        raise BusinessError("unsafe_manifest", "Snapshot object entries differ from the database registry")
    report = inspect_database(source / "data/omni.db")
    if report["integrity"] != "ok" or report["foreign_key_errors"]:
        raise BusinessError("backup_integrity_failed", "Snapshot database failed integrity validation")
    if fingerprint(manifest_path) != hashlib.sha256(encoded).hexdigest():
        raise BusinessError("backup_fingerprint_changed", "Backup manifest changed during verification")
    return manifest


def _restore_verified(
    source: Path, destination: Path, manifest: dict[str, Any], manifest_hash: str, existed: bool
) -> dict[str, Any]:
    """Caller holds the target instance lock through report and marker publication."""
    report_path = safe_child(destination, "restore-report.json")
    if existed:
        # A matching main file does not describe commits still in its WAL or a
        # hot rollback journal. Never checkpoint or discard these user files.
        for name in ("omni.db-wal", "omni.db-shm", "omni.db-journal"):
            path = safe_child(destination, "data/" + name)
            if path.exists() and (
                not path.is_file() or path.stat().st_nlink != 1 or (name != "omni.db-shm" and path.stat().st_size > 0)
            ):
                raise BusinessError("restore_destination_changed", "Restore target contains unverified SQLite state")
        previous = None
        try:
            if (
                report_path.is_file()
                and report_path.stat().st_nlink == 1
                and report_path.stat().st_size <= 64 * 1024 * 1024
            ):
                with report_path.open("rb") as stream:
                    encoded = stream.read(64 * 1024 * 1024 + 1)
                if len(encoded) <= 64 * 1024 * 1024:
                    previous = json.loads(encoded)
        except (OSError, ValueError, UnicodeError, RecursionError):
            pass
        if (
            isinstance(previous, dict)
            and previous.get("manifest_sha256") == manifest_hash
            and previous.get("execution_quarantined") is True
            and previous.get("audit_retention_quarantined") is True
            and previous.get("event_retention_quarantined") is True
            and previous.get("maintenance_quarantined") is True
            and previous.get("memory_organization_quarantined") is True
            and previous.get("extensions_quarantined") is True
            and previous.get("action_reviews_quarantined") is True
            and previous.get("terminals_quarantined") is True
            and previous.get("credentials_restored") is False
            and isinstance(previous.get("files"), list)
            and len(previous["files"]) == len(manifest["files"])
        ):
            expected = {entry["path"] for entry in manifest["files"]}
            actual = set()
            for entry in previous["files"]:
                if (
                    not isinstance(entry, dict)
                    or not isinstance(entry.get("path"), str)
                    or entry["path"] not in expected
                    or entry["path"] in actual
                    or not isinstance(entry.get("sha256"), str)
                    or len(entry["sha256"]) != 64
                ):
                    break
                path = safe_child(destination, entry["path"])
                if not path.is_file() or path.stat().st_nlink != 1 or fingerprint(path) != entry["sha256"]:
                    break
                actual.add(entry["path"])
            if actual == expected:
                verification = inspect_database(destination / "data/omni.db", allow_restore_in_progress=True)
                if verification["integrity"] == "ok" and not verification["foreign_key_errors"]:
                    with sqlite_connection(destination / "data/omni.db") as connection:
                        tables = {
                            row[0] for row in connection.execute("SELECT name FROM sqlite_master WHERE type='table'")
                        }
                        if "history_identity" in tables:
                            epoch = connection.execute("SELECT epoch FROM history_identity WHERE slot=1").fetchone()[0]
                            quarantine = previous.get("quarantine")
                            if not isinstance(quarantine, dict) or quarantine.get("history_epoch") != epoch:
                                raise BusinessError("restore_destination_changed", "Restored history identity changed")
                    verify_objects(destination / "data/omni.db", destination / "objects")
                    _finish_restore_marker(destination, manifest_hash)
                    return {**previous, "already_restored": True}
        raise BusinessError("destination_exists", "Restore requires an empty new directory")
    durable_json(destination / "restore-in-progress.json", {"manifest_sha256": manifest_hash})
    for entry in manifest["files"]:
        target = safe_child(destination, entry["path"])
        target.parent.mkdir(parents=True, exist_ok=True)
        shutil.copyfile(safe_child(source, entry["path"]), target)
        if fingerprint(target) != entry["sha256"] or target.stat().st_size != entry["bytes"]:
            raise BusinessError("backup_fingerprint_changed", "A snapshot file changed during restore")
        with target.open("r+b") as stream:
            os.fsync(stream.fileno())
        sync_directory(target.parent)
    database = destination / "data/omni.db"
    with sqlite_connection(database, read_only=False) as connection:
        connection.execute("BEGIN IMMEDIATE")
        quarantine = _quarantine_restored_work(connection, manifest_hash)
        connection.commit()
    with database.open("r+b") as stream:
        os.fsync(stream.fileno())
    report = {
        "manifest_sha256": manifest_hash,
        "database": inspect_database(database, allow_restore_in_progress=True),
        "credentials_restored": False,
        "execution_quarantined": True,
        "audit_retention_quarantined": True,
        "event_retention_quarantined": True,
        "maintenance_quarantined": True,
        "memory_organization_quarantined": True,
        "extensions_quarantined": True,
        "action_reviews_quarantined": True,
        "terminals_quarantined": True,
        "quarantine": quarantine,
        "files": [
            {"path": entry["path"], "sha256": fingerprint(destination / entry["path"])} for entry in manifest["files"]
        ],
    }
    if report["database"]["integrity"] != "ok" or report["database"]["foreign_key_errors"]:
        raise BusinessError("restore_integrity_failed", "Restored database failed validation")
    verify_objects(database, destination / "objects")
    durable_json(report_path, report)
    _finish_restore_marker(destination, manifest_hash)
    return report


restore_verified = _restore_verified


def restore(source: Path, destination: Path) -> Record:
    source, destination = source.resolve(), destination.absolute()
    if source.is_relative_to(destination.resolve()) or destination.resolve().is_relative_to(source):
        raise BusinessError("unsafe_destination", "Restore must be outside the backup directory")
    manifest = verify_snapshot(source)
    for entry in manifest["files"]:
        safe_child(destination, entry["path"])
    lock_path = safe_child(destination, "run/instance.lock")
    if lock_path.exists() and (not lock_path.is_file() or lock_path.stat().st_nlink != 1):
        raise BusinessError("unsafe_lock", "Restore target lock must be a single-link regular file")
    manifest_hash = fingerprint(source / "manifest.json")
    existed = destination.exists()
    if existed:
        report_path = safe_child(destination, "restore-report.json")
        if not report_path.is_file():
            raise BusinessError("destination_exists", "Restore requires an empty new directory")
    else:
        destination.mkdir(parents=True, mode=0o700)
        private_path(destination, directory=True)
    lock = InstanceLock(InstancePaths(destination))
    lock.acquire()
    try:
        return _restore_verified(source, destination, manifest, manifest_hash, existed)
    finally:
        lock.close()
