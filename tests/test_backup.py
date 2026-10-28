"""备份/恢复单测（D05 前置：迁移前必须有一条可验证的退路）。"""
from __future__ import annotations

import json
import sqlite3

import pytest

from omni.config import RuntimePaths
from omni.system.backup import (
    BackupError,
    MANIFEST_NAME,
    backup_runtime,
    read_manifest,
    restore_into,
    sha256_file,
    snapshot_sqlite,
    verify_database,
)


def _rows(path):
    conn = sqlite3.connect(str(path))
    try:
        return [row[0] for row in conn.execute("SELECT body FROM notes ORDER BY id")]
    finally:
        conn.close()


def _db_with_row(path, text="已提交的一行"):
    conn = sqlite3.connect(str(path))
    conn.execute("PRAGMA journal_mode=WAL")
    conn.execute("CREATE TABLE IF NOT EXISTS notes (id INTEGER PRIMARY KEY, body TEXT)")
    conn.execute("INSERT INTO notes(body) VALUES (?)", (text,))
    conn.commit()
    return conn


def test_snapshot_captures_committed_rows_only(tmp_path):
    """写连接还开着未提交事务时取快照：副本必须只有已提交数据，且完整可用。"""
    src = tmp_path / "memory.db"
    writer = _db_with_row(src)
    try:
        writer.execute("BEGIN")
        writer.execute("INSERT INTO notes(body) VALUES ('未提交的一行')")
        entry = snapshot_sqlite(src, tmp_path / "backup" / "memory.db")
    finally:
        writer.rollback()
        writer.close()

    copy = sqlite3.connect(str(tmp_path / "backup" / "memory.db"))
    try:
        bodies = [row[0] for row in copy.execute("SELECT body FROM notes ORDER BY id")]
    finally:
        copy.close()
    assert bodies == ["已提交的一行"]  # 未提交的事务没有进副本
    assert entry.sha256 == sha256_file(tmp_path / "backup" / "memory.db")


def test_snapshot_missing_database_is_an_error(tmp_path):
    with pytest.raises(BackupError, match="不存在"):
        snapshot_sqlite(tmp_path / "nope.db", tmp_path / "out.db")


def test_snapshot_corrupt_database_fails_without_leaving_half_file(tmp_path):
    bad = tmp_path / "state.db"
    bad.write_bytes(b"this is definitely not a sqlite file" * 10)
    with pytest.raises(BackupError):
        snapshot_sqlite(bad, tmp_path / "out.db")
    assert not (tmp_path / "out.db").exists(), "失败时不能留下看似成功的副本"


def test_backup_runtime_covers_databases_config_resident_and_skills(tmp_path):
    data = tmp_path / "data"
    data.mkdir()
    paths = RuntimePaths.create(data, config_path=tmp_path / "config.yaml", skills_root=tmp_path / "skills")
    paths.config.write_text("persona:\n  name: 小鱼\n", encoding="utf-8")
    (data / "MEMORY.md").write_text("- 常驻一条\n", encoding="utf-8")
    paths.skills.mkdir(parents=True)
    (paths.skills / "demo").mkdir()
    (paths.skills / "demo" / "SKILL.md").write_text("# demo\n", encoding="utf-8")
    _db_with_row(paths.data / "memory.db").close()

    report = backup_runtime(paths, tmp_path / "out")

    rels = {e.rel for e in report.entries}
    assert "memory.db" in rels and "config.yaml" in rels and "MEMORY.md" in rels
    assert "skills" in rels
    manifest = read_manifest(tmp_path / "out")
    assert json.loads((tmp_path / "out" / MANIFEST_NAME).read_text(encoding="utf-8"))["version"] == 1
    assert len(manifest["entries"]) == len(report.entries)
    assert any("state.db" in item for item in report.skipped)


def test_backup_runtime_marks_missing_items_instead_of_pretending(tmp_path):
    paths = RuntimePaths.create(
        tmp_path / "empty", config_path=tmp_path / "x.yaml", skills_root=tmp_path / "skills"
    )
    report = backup_runtime(paths, tmp_path / "out")
    assert report.entries == []
    assert any("state.db" in item for item in report.skipped)
    assert any("config.yaml" in item for item in report.skipped)


def test_restore_goes_to_new_dir_and_refuses_overwrite(tmp_path):
    data = tmp_path / "data"
    data.mkdir()
    paths = RuntimePaths.create(data, config_path=tmp_path / "config.yaml")
    paths.config.write_text("persona:\n  name: 小鱼\n", encoding="utf-8")
    _db_with_row(paths.data / "memory.db").close()
    backup_runtime(paths, tmp_path / "out")

    target = tmp_path / "restored"
    entries = restore_into(tmp_path / "out", target)
    assert {e.rel for e in entries} >= {"memory.db", "config.yaml"}
    verify_database(target / "memory.db")
    assert sha256_file(target / "memory.db") == sha256_file(tmp_path / "out" / "memory.db")
    # 原库没有被恢复过程改动：按内容比对（WAL 存在时字节会不同，不能拿 sha 当同一性）
    assert _rows(paths.data / "memory.db") == _rows(target / "memory.db") == ["已提交的一行"]

    with pytest.raises(BackupError, match="拒绝覆盖"):
        restore_into(tmp_path / "out", target)


def test_restore_rejects_tampered_backup(tmp_path):
    data = tmp_path / "data"
    data.mkdir()
    paths = RuntimePaths.create(
        data, config_path=tmp_path / "config.yaml", skills_root=tmp_path / "skills"
    )
    _db_with_row(paths.data / "memory.db").close()
    backup_runtime(paths, tmp_path / "out")

    # 在副本里改一行：库仍然合法（integrity_check 通过），只有校验和能发现被换过
    copy = tmp_path / "out" / "memory.db"
    conn = sqlite3.connect(str(copy))
    conn.execute("UPDATE notes SET body = '被篡改'")
    conn.commit()
    conn.close()
    with pytest.raises(BackupError):
        restore_into(tmp_path / "out", tmp_path / "restored")
    assert not (tmp_path / "restored" / "memory.db").exists()


def test_manifest_path_traversal_is_rejected(tmp_path):
    backup = tmp_path / "out"
    backup.mkdir()
    (backup / MANIFEST_NAME).write_text(
        json.dumps(
            {
                "version": 1,
                "created_at": "2026-10-27T00:00:00+08:00",
                "entries": [{"rel": "../escape.txt", "kind": "file", "sha256": "x"}],
            }
        ),
        encoding="utf-8",
    )
    with pytest.raises(BackupError, match="非法路径"):
        restore_into(backup, tmp_path / "restored")


def test_verify_database_reports_corruption(tmp_path):
    bad = tmp_path / "bad.db"
    bad.write_bytes(b"not sqlite at all")
    with pytest.raises(BackupError):
        verify_database(bad)
