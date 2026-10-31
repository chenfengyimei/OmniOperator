"""所有者记忆一次性迁移：profile 表与 MEMORY.md → owner 人物条目。

数据层统一（本批）把"关于用户"的常驻注入记忆并入人物系统后，历史数据在
启动时搬迁一次：

- `profile` 表的档案行 → owner 人物的 archive 条目（source="import"）；
- `data/MEMORY.md` 的常驻条目 → owner 人物的 resident 条目（source="import"）。

安全设计：
- **幂等**：完成标记存在即跳过；半途崩溃重启时按"owner 已有 import 来源条目"
  跳过对应种类，不产生重复导入；
- **先备份后动手**：memory.db（SQLite backup API，WAL 安全）与 MEMORY.md
  原样复制进 data/backups/pre-owner-merge-<ts>/，失败不迁移；
- **迁移绝不阻断启动**：任何一步失败都只记录并跳过，旧数据保持原样；
- **旧表/旧文件只冻结不删除**：facts 墓碑、摘要失效等旧机制引用的表保持
  可读，回滚 = 还原备份后回退代码。
"""
from __future__ import annotations

import datetime as dt
import shutil
import sqlite3
import sys
from pathlib import Path

from .resident import ENTRY_DELIMITER
from .store import MemoryStore

MIGRATION_KEY = "profile+resident"


def _read_resident_file(path: Path) -> tuple[list[str], str | None]:
    """读旧常驻文件：返回 (条目, 错误说明)。文件不存在=合法空；读失败不迁移该部分。"""
    try:
        raw = path.read_bytes()
    except FileNotFoundError:
        return [], None
    except OSError as exc:
        return [], f"读取失败（{exc}）"
    try:
        text = raw.decode("utf-8")
    except UnicodeError:
        return [], "不是 UTF-8 文本"
    # Windows 手工编辑过的文件分隔符是 \r\n§\r\n：读侧归一化后再切分（写侧保持 \n）
    return [
        e.strip() for e in text.replace("\r\n", "\n").split(ENTRY_DELIMITER) if e.strip()
    ], None


def _backup_memory_db(db_path: Path, backup_dir: Path) -> str | None:
    try:
        backup_dir.mkdir(parents=True, exist_ok=True)
        target_path = backup_dir / db_path.name
        src = sqlite3.connect(f"file:{db_path}?mode=ro", uri=True)
        try:
            dst = sqlite3.connect(target_path)
            try:
                with dst:
                    src.backup(dst)
            finally:
                dst.close()
        finally:
            src.close()
        return str(target_path)
    except sqlite3.Error as exc:
        print(f"[所有者记忆迁移] memory.db 备份失败，本次不迁移：{exc}", file=sys.stderr, flush=True)
        return None


def migrate_owner_memory(
    store: MemoryStore,
    people,  # PersonStore（避免循环 import 用鸭子类型）
    resident_path: Path,
    *,
    backup_root: Path | None = None,
    owner_name: str = "所有者",
    memory_db_path: Path | None = None,
) -> dict:
    """执行一次性迁移；返回统计（skipped/imported/owner_id/backup）。"""
    if people.migration_done(MIGRATION_KEY):
        return {"skipped": True, "key": MIGRATION_KEY}

    profile_rows = store.profile_lines()
    resident_entries, file_error = _read_resident_file(Path(resident_path))
    if file_error:
        print(
            f"[所有者记忆迁移] 旧常驻文件{file_error}，该部分跳过（文件保持原样）",
            file=sys.stderr,
            flush=True,
        )

    owner_id = people.ensure_owner(owner_name)
    existing = {
        kind: {e["content"] for e in people.entries(owner_id) if e["source"] == "import"}
        for kind in ("archive", "resident")
    }
    # 半途崩溃重启：已有 import 条目的种类不再重复导入
    pending_profile = [c for _, c in profile_rows if c not in existing["archive"]]
    pending_resident = [c for c in resident_entries if c not in existing["resident"]]

    result: dict = {
        "skipped": False,
        "key": MIGRATION_KEY,
        "owner_id": owner_id,
        "imported_profile": 0,
        "imported_resident": 0,
        "backup": None,
        "file_error": file_error,
    }
    if not pending_profile and not pending_resident:
        people.mark_migration(MIGRATION_KEY)
        return result

    db_path = Path(memory_db_path) if memory_db_path else None
    backup_dir = None
    if backup_root is not None and (pending_profile or pending_resident):
        stamp = dt.datetime.now().strftime("%Y%m%d-%H%M%S")
        backup_dir = Path(backup_root) / f"pre-owner-merge-{stamp}"
        db_source = db_path if db_path is not None else None
        if db_source is not None:
            backed = _backup_memory_db(db_source, backup_dir)
            if backed is None:
                result["error"] = "备份失败，本次不迁移（旧数据保持原样，下轮启动重试）"
                return result
            result["backup"] = backed
            if Path(resident_path).exists():
                try:
                    shutil.copy2(resident_path, backup_dir / resident_path.name)
                except OSError as exc:
                    print(
                        f"[所有者记忆迁移] MEMORY.md 备份失败，本次不迁移：{exc}",
                        file=sys.stderr,
                        flush=True,
                    )
                    result["error"] = "MEMORY.md 备份失败，本次不迁移"
                    return result

    for content in pending_profile:
        people._import_entry(owner_id, "archive", content, source="import")
        result["imported_profile"] += 1
    for content in pending_resident:
        people._import_entry(owner_id, "resident", content, source="import")
        result["imported_resident"] += 1
    people.mark_migration(MIGRATION_KEY)
    print(
        f"[所有者记忆迁移] 完成：档案行 {result['imported_profile']} 条、"
        f"常驻条目 {result['imported_resident']} 条 → owner 人物 #{owner_id}"
        f"{('，备份 ' + str(result['backup'])) if result['backup'] else ''}",
        flush=True,
    )
    return result
