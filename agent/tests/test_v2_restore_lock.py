"""Restore owns its target, rejects unresolved SQLite sidecars and verifies complete recovery evidence."""

import asyncio
import json
import os
import subprocess
import sys
import uuid

import pytest

from omni.adapters.persistence import maintenance
from omni.adapters.persistence.maintenance import backup, fingerprint, restore, verify_snapshot
from omni.adapters.persistence.sqlite_access import sqlite_connection
from omni.adapters.platform.paths import InstanceLock, InstancePaths
from omni.composition.instance import open_instance
from omni.domain.contracts import BusinessError


def snapshot(tmp_path):
    async def seed():
        instance = await open_instance(tmp_path / "original", capabilities=())
        try:
            return instance.paths
        finally:
            assert (await instance.close())["complete"]

    paths = asyncio.run(seed())
    source = tmp_path / "snapshot"
    manifest = backup(paths, source)
    return source, manifest


def test_repeated_restore_refuses_a_live_target_and_never_clears_its_marker(tmp_path):
    source, _ = snapshot(tmp_path)
    target = tmp_path / "restored"
    first = restore(source, target)
    marker = target / "restore-in-progress.json"
    marker.write_text(json.dumps({"manifest_sha256": first["manifest_sha256"]}), encoding="utf-8")
    original = (target / "data/omni.db").read_bytes()
    lock = InstanceLock(InstancePaths.resolve(target))
    lock.acquire()
    try:
        with pytest.raises(BusinessError) as error:
            restore(source, target)
        assert error.value.code == "instance_busy"
        assert marker.exists() and (target / "data/omni.db").read_bytes() == original
    finally:
        lock.close()
    assert restore(source, target)["already_restored"]
    assert not marker.exists()


def test_new_restore_holds_target_lock_before_copy_and_releases_it_after_completion(tmp_path, monkeypatch):
    source, _ = snapshot(tmp_path)
    target = tmp_path / "restored"
    original = maintenance.shutil.copyfile
    observed = []

    def guarded_copy(input_path, output_path, *args, **kwargs):
        if output_path == target / "data/omni.db":
            contender = InstanceLock(InstancePaths.resolve(target))
            with pytest.raises(BusinessError) as error:
                contender.acquire()
            assert error.value.code == "instance_busy"
            observed.append(True)
        return original(input_path, output_path, *args, **kwargs)

    monkeypatch.setattr(maintenance.shutil, "copyfile", guarded_copy)
    assert restore(source, target)["execution_quarantined"] and observed == [True]
    lock = InstanceLock(InstancePaths.resolve(target))
    lock.acquire()
    lock.close()
    assert restore(source, target)["already_restored"]


def test_interrupted_copy_releases_target_lock_but_preserves_startup_fence(tmp_path, monkeypatch):
    source, _ = snapshot(tmp_path)
    target = tmp_path / "partial"

    def blocked(*args, **kwargs):
        raise PermissionError("isolated copy failure")

    monkeypatch.setattr(maintenance.shutil, "copyfile", blocked)
    with pytest.raises(PermissionError):
        restore(source, target)
    assert (target / "restore-in-progress.json").exists()
    lock = InstanceLock(InstancePaths.resolve(target))
    lock.acquire()
    lock.close()

    async def refuses_start():
        with pytest.raises(BusinessError) as error:
            await open_instance(target, capabilities=())
        assert error.value.code == "restore_incomplete"

    asyncio.run(refuses_start())


def test_uncheckpointed_commit_cannot_pass_main_file_fingerprint_check(tmp_path):
    source, _ = snapshot(tmp_path)
    target = tmp_path / "restored"
    report = restore(source, target)
    database = target / "data/omni.db"
    original = fingerprint(database)
    code = """
import os,sqlite3,sys
from pathlib import Path
from omni.adapters.platform.paths import InstanceLock,InstancePaths
lock=InstanceLock(InstancePaths.resolve(Path(sys.argv[1])))
lock.acquire()
connection=sqlite3.connect(str(Path(sys.argv[1])/'data/omni.db'))
assert connection.execute('PRAGMA journal_mode=WAL').fetchone()[0]=='wal'
connection.execute('PRAGMA wal_autocheckpoint=0')
connection.execute("UPDATE agents SET name='committed after restore in WAL'")
connection.commit()
os._exit(91)
"""
    process = subprocess.run(
        [sys.executable, "-c", code, str(target)], capture_output=True, text=True, timeout=30, check=False
    )
    assert process.returncode == 91, process.stderr
    assert fingerprint(database) == original
    assert report["files"][0]["sha256"] == original
    wal = database.with_name("omni.db-wal")
    assert wal.stat().st_size > 0
    wal_hash = fingerprint(wal)
    with pytest.raises(BusinessError) as error:
        restore(source, target)
    assert error.value.code == "restore_destination_changed"
    assert fingerprint(database) == original and fingerprint(wal) == wal_hash
    with sqlite_connection(database) as connection:
        assert connection.execute("SELECT name FROM agents").fetchone()[0] == "committed after restore in WAL"


def test_repeated_restore_requires_a_complete_report_file_set(tmp_path):
    source, _ = snapshot(tmp_path)
    target = tmp_path / "restored"
    report = restore(source, target)
    report["files"] = []
    (target / "restore-report.json").write_text(json.dumps(report), encoding="utf-8")
    marker = target / "restore-in-progress.json"
    marker.write_text(json.dumps({"manifest_sha256": report["manifest_sha256"]}), encoding="utf-8")
    with pytest.raises(BusinessError) as error:
        restore(source, target)
    assert error.value.code == "destination_exists" and marker.exists()


def test_snapshot_verification_checks_database_constraints_not_only_file_hashes(tmp_path):
    source, manifest = snapshot(tmp_path)
    assert verify_snapshot(source) == manifest
    database = source / "data/omni.db"
    with sqlite_connection(database, read_only=False) as connection:
        connection.execute("PRAGMA foreign_keys=OFF")
        connection.execute("UPDATE instances SET owner_id='11111111-1111-1111-1111-111111111111'")
    entry = next(item for item in manifest["files"] if item["path"] == "data/omni.db")
    entry.update(sha256=fingerprint(database), bytes=database.stat().st_size)
    (source / "manifest.json").write_text(json.dumps(manifest), encoding="utf-8")
    with pytest.raises(BusinessError) as error:
        verify_snapshot(source)
    assert error.value.code == "backup_integrity_failed"
    target = tmp_path / "invalid"
    with pytest.raises(BusinessError):
        restore(source, target)
    assert not target.exists()


def test_restore_rejects_aliased_target_lock_without_modifying_other_paths(tmp_path):
    source, _ = snapshot(tmp_path)
    target = tmp_path / "restored"
    restore(source, target)
    original = target / "run/instance.lock"
    alias = tmp_path / "unrelated-lock-alias"
    os.link(original, alias)
    content = alias.read_bytes()
    with pytest.raises(BusinessError) as error:
        restore(source, target)
    assert error.value.code == "unsafe_lock" and alias.read_bytes() == content


def test_snapshot_with_unmanifested_wal_cannot_be_reused_as_a_verified_backup(tmp_path):
    source, _ = snapshot(tmp_path)
    database = source / "data/omni.db"
    original = fingerprint(database)
    code = """
import os,sqlite3,sys
connection=sqlite3.connect(sys.argv[1])
assert connection.execute('PRAGMA journal_mode=WAL').fetchone()[0]=='wal'
connection.execute('PRAGMA wal_autocheckpoint=0')
connection.execute("UPDATE agents SET name='snapshot mutation lives in WAL'")
connection.commit()
os._exit(92)
"""
    process = subprocess.run(
        [sys.executable, "-c", code, str(database)], capture_output=True, text=True, timeout=30, check=False
    )
    assert process.returncode == 92, process.stderr
    assert fingerprint(database) == original
    with pytest.raises(BusinessError) as error:
        verify_snapshot(source)
    assert error.value.code == "backup_fingerprint_changed"
    target = tmp_path / "cannot-restore"
    with pytest.raises(BusinessError):
        restore(source, target)
    assert not target.exists()


def test_doctor_reports_pending_execution_retention_until_batch_is_complete(tmp_path):
    snapshot(tmp_path)
    database = tmp_path / "original/data/omni.db"
    assert maintenance.inspect_database(database)["execution_retention_attention"] == 0
    identity = str(uuid.uuid4())
    with sqlite_connection(database, read_only=False) as connection:
        instance = connection.execute("SELECT id FROM instances WHERE slot=1").fetchone()[0]
        schema = connection.execute("SELECT version_num FROM alembic_version").fetchone()[0]
        connection.execute(
            "INSERT INTO retention_batches(id,created_at,instance_id,plan_sha256,policy,cutoff,schema_version,backup_path,backup_sha256,state) VALUES(?,?,?,?,'execution-body-v1',?,?,?,?,'prepared')",
            (
                identity,
                "2026-11-04T00:00:00+00:00",
                instance,
                "a" * 64,
                "2026-10-05T00:00:00+00:00",
                schema,
                str(tmp_path / "snapshot"),
                "b" * 64,
            ),
        )
    assert maintenance.inspect_database(database)["execution_retention_attention"] == 1
    with sqlite_connection(database, read_only=False) as connection:
        connection.execute(
            "UPDATE retention_batches SET state='complete',completed_at=? WHERE id=?",
            ("2026-11-04T00:01:00+00:00", identity),
        )
    assert maintenance.inspect_database(database)["execution_retention_attention"] == 0


def test_existing_unrelated_directory_is_refused_without_creating_runtime_files(tmp_path):
    source, _ = snapshot(tmp_path)
    target = tmp_path / "personal-files"
    target.mkdir()
    note = target / "notes.txt"
    note.write_text("preserve unrelated user files", encoding="utf-8")
    with pytest.raises(BusinessError) as error:
        restore(source, target)
    assert error.value.code == "destination_exists"
    assert list(target.iterdir()) == [note]
    assert note.read_text(encoding="utf-8") == "preserve unrelated user files"
