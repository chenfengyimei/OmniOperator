"""归档验证内容变化、恢复与SQLite一致快照，不接触真实运行目录。"""

from pathlib import Path

import pytest

from omni.system.archive import execute, plan


def test_archive_roundtrip_and_idempotence(tmp_path):
    source = tmp_path / "old.txt"
    source.write_text("旧版本", encoding="utf-8")
    manifest = plan(tmp_path, ["old.txt"], category="archive", reason="已退役")
    execute(manifest)
    assert not source.exists()
    execute(manifest)
    execute(manifest, restore=True)
    assert source.read_text(encoding="utf-8") == "旧版本"
    execute(manifest, restore=True)


def test_changed_source_and_restore_conflict_are_rejected(tmp_path):
    source = tmp_path / "old.txt"
    source.write_text("first")
    manifest = plan(tmp_path, ["old.txt"], category="private", reason="保存历史")
    source.write_text("changed")
    with pytest.raises(ValueError):
        execute(manifest)
    assert source.read_text() == "changed"


def test_private_data_cannot_be_archived_publicly_or_escape_root(tmp_path):
    source = tmp_path / "data/log.txt"
    source.parent.mkdir()
    source.write_text("private")
    with pytest.raises(ValueError):
        plan(tmp_path, ["data/log.txt"], category="archive", reason="测试")
    with pytest.raises(ValueError):
        plan(tmp_path, ["../outside"], category="private", reason="测试")


def test_sqlite_archive_uses_committed_wal_and_preserves_source(tmp_path):
    import sqlite3

    source = tmp_path / "history.db"
    connection = sqlite3.connect(source)
    connection.execute("PRAGMA journal_mode=WAL")
    connection.execute("CREATE TABLE evidence(value TEXT)")
    connection.execute("INSERT INTO evidence VALUES ('committed')")
    connection.commit()
    try:
        manifest = plan(tmp_path, ["history.db"], category="private", reason="历史一致快照")
        result = execute(manifest)
        snapshot = tmp_path / result["entries"][0]["target"]
        with sqlite3.connect(snapshot) as restored:
            assert restored.execute("SELECT value FROM evidence").fetchone()[0] == "committed"
        assert source.exists()
    finally:
        connection.close()


def test_new_reference_between_plan_and_apply_blocks_archive(tmp_path):
    (tmp_path / "old.txt").write_text("retired")
    manifest = plan(tmp_path, ["old.txt"], category="archive", reason="旧文件")
    (tmp_path / "README.md").write_text("see old.txt")
    with pytest.raises(ValueError, match="仍有引用"):
        execute(manifest)
    assert (tmp_path / "old.txt").is_file()


def test_interruption_after_move_resumes_without_losing_restore(tmp_path, monkeypatch):
    import omni.system.archive as archive

    (tmp_path / "old.txt").write_text("recoverable")
    manifest = plan(tmp_path, ["old.txt"], category="archive", reason="旧文件")
    original = archive._write
    monkeypatch.setattr(archive, "_write", lambda *_: (_ for _ in ()).throw(OSError("interrupted")))
    with pytest.raises(OSError):
        execute(manifest)
    monkeypatch.setattr(archive, "_write", original)
    execute(manifest)
    execute(manifest, restore=True)
    assert (tmp_path / "old.txt").read_text() == "recoverable"


def test_snapshot_can_restore_missing_closed_database_and_repeat(tmp_path):
    import sqlite3

    source = tmp_path / "old.db"
    with sqlite3.connect(source) as connection:
        connection.execute("CREATE TABLE notes(text TEXT)")
        connection.execute("INSERT INTO notes VALUES ('keep')")
    connection.close()
    manifest = plan(tmp_path, ["old.db"], category="private", reason="一致快照")
    execute(manifest)
    source.unlink()
    execute(manifest, restore=True)
    execute(manifest, restore=True)
    with sqlite3.connect(source) as connection:
        assert connection.execute("SELECT text FROM notes").fetchall() == [("keep",)]


def test_open_file_is_not_moved_and_other_batch_entries_remain(tmp_path):
    first, second = tmp_path / "first.txt", tmp_path / "second.txt"
    first.write_text("first")
    second.write_text("second")
    manifest = plan(tmp_path, ["first.txt", "second.txt"], category="archive", reason="已退役")
    with second.open("rb"), pytest.raises(PermissionError, match="占用"):
        execute(manifest)
    assert first.exists() and second.exists()
