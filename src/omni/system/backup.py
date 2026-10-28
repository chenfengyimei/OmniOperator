"""离线备份与恢复（D25 的提前子项，D05 迁移前必须先能用）。

三条纪律：

1. **SQLite 用 `Connection.backup()` 取一致快照**。直接复制正在使用的 db 文件会漏掉
   仍在 WAL 里的已提交事务——恢复后看起来"丢了一段记忆"，而备份当时并没有报错。
2. **备份完对副本跑 `integrity_check`**，并把 sha256 / 字节数 / 关键表行数写进清单。
   没有校验和的"备份成功"只是一句话。
3. **恢复只往新目录写**，先校验再交给用户切换；整个过程不启动、不探测、不终止任何真实服务。

边界（明确不做）：不自动切换正在运行的实例，不覆盖已存在的目标文件（除非显式要求），
不打包浏览器 profile / Cookie / .env——凭据按 D25 的显式选择处理，默认不出现在备份里。
"""
from __future__ import annotations

import argparse
import datetime as dt
import hashlib
import json
import shutil
import sqlite3
import sys
from dataclasses import asdict, dataclass, field
from pathlib import Path

from ..config import DATA_DIR, RuntimePaths

MANIFEST_NAME = "backup.json"
MANIFEST_VERSION = 1

# 备份清单里记录行数的表（缺失即跳过，不当作错误）
COUNTED_TABLES = {
    "state.db": ("jobs", "executions", "budgets", "migrations", "llm_calls", "llm_budget",
                 "artifacts"),
    "memory.db": ("sessions", "messages", "facts", "profile", "summaries"),
}


class BackupError(RuntimeError):
    """备份/恢复无法给出可信结果（缺失、损坏、已存在、校验失败）。"""


@dataclass(frozen=True)
class Entry:
    """清单条目：一个被备份的产物。"""

    rel: str          # 备份目录内的相对路径，也是恢复时的目标相对路径
    kind: str         # database | file | tree
    source: str       # 源路径（绝对路径，仅用于排查）
    size: int
    sha256: str
    rows: dict[str, int] = field(default_factory=dict)
    files: int = 0    # kind=tree 时的文件数


@dataclass(frozen=True)
class BackupReport:
    dest: Path
    created_at: str
    entries: list[Entry]
    skipped: list[str]

    def to_dict(self) -> dict:
        return {
            "version": MANIFEST_VERSION,
            "created_at": self.created_at,
            "dest": str(self.dest),
            "entries": [asdict(e) for e in self.entries],
            "skipped": list(self.skipped),
        }

    def database(self, name: str) -> Entry | None:
        return next((e for e in self.entries if e.rel == name), None)


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with Path(path).open("rb") as handle:
        for chunk in iter(lambda: handle.read(1 << 20), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _now() -> str:
    return dt.datetime.now().astimezone().isoformat(timespec="seconds")


def _count_tables(db_file: Path, name: str) -> dict[str, int]:
    counts: dict[str, int] = {}
    wanted = COUNTED_TABLES.get(name, ())
    if not wanted:
        return counts
    conn = sqlite3.connect(str(db_file))
    try:
        existing = {
            str(row[0])
            for row in conn.execute("SELECT name FROM sqlite_master WHERE type='table'")
        }
        for table in wanted:
            if table not in existing:
                continue
            counts[table] = int(conn.execute(f"SELECT COUNT(*) FROM {table}").fetchone()[0])
    except sqlite3.DatabaseError:
        pass
    finally:
        conn.close()
    return counts


def snapshot_sqlite(src: Path, dest: Path) -> Entry:
    """一致快照 + 校验。src 不存在或不可读时抛 BackupError，不产出"半份备份"。"""
    src = Path(src)
    dest = Path(dest)
    if not src.is_file():
        raise BackupError(f"数据库不存在：{src}")
    dest.parent.mkdir(parents=True, exist_ok=True)
    if dest.exists():
        dest.unlink()  # 只覆盖本次自己的目标文件
    try:
        source = sqlite3.connect(str(src))
        try:
            target = sqlite3.connect(str(dest))
            try:
                source.backup(target)
            finally:
                target.close()
        finally:
            source.close()
    except sqlite3.DatabaseError as exc:
        dest.unlink(missing_ok=True)
        raise BackupError(f"快照失败（{src.name}）：{exc}") from exc

    check = sqlite3.connect(str(dest))
    try:
        row = check.execute("PRAGMA integrity_check").fetchone()
    except sqlite3.DatabaseError as exc:
        dest.unlink(missing_ok=True)
        raise BackupError(f"副本无法校验（{src.name}）：{exc}") from exc
    finally:
        check.close()
    if not row or str(row[0]) != "ok":
        detail = row[0] if row else "无返回"
        dest.unlink(missing_ok=True)
        raise BackupError(f"副本完整性校验未通过（{src.name}）：{detail}")

    return Entry(
        rel=dest.name,
        kind="database",
        source=str(src),
        size=dest.stat().st_size,
        sha256=sha256_file(dest),
        rows=_count_tables(dest, dest.name),
    )


def _copy_entry(src: Path, dest: Path, rel: str) -> Entry:
    dest.parent.mkdir(parents=True, exist_ok=True)
    shutil.copy2(src, dest)
    return Entry(
        rel=rel,
        kind="file",
        source=str(src),
        size=dest.stat().st_size,
        sha256=sha256_file(dest),
    )


def _copy_tree(src: Path, dest: Path, rel: str) -> Entry:
    shutil.copytree(src, dest, dirs_exist_ok=True)
    files = [p for p in dest.rglob("*") if p.is_file()]
    # 目录级摘要：把所有文件的 rel+sha 串起来再取一次 sha256（可复算）
    joined = "\n".join(
        f"{p.relative_to(dest).as_posix()}:{sha256_file(p)}" for p in sorted(files)
    )
    return Entry(
        rel=rel,
        kind="tree",
        source=str(src),
        size=sum(p.stat().st_size for p in files),
        sha256=hashlib.sha256(joined.encode("utf-8")).hexdigest(),
        files=len(files),
    )


def backup_runtime(
    paths: RuntimePaths,
    dest_dir: Path,
    *,
    include_skills: bool = True,
) -> BackupReport:
    """备份运行态到 dest_dir：两个数据库 + 配置 + 常驻笔记 + 技能库。

    缺失项进 `skipped`，不让"没这个文件"变成备份失败——但也绝不假装备份了。
    """
    dest_dir = Path(dest_dir)
    dest_dir.mkdir(parents=True, exist_ok=True)
    entries: list[Entry] = []
    skipped: list[str] = []

    for name in ("state.db", "memory.db"):
        src = paths.data / name
        if src.is_file():
            entry = snapshot_sqlite(src, dest_dir / name)
            entries.append(entry)
        else:
            skipped.append(f"{name}（不存在）")

    for rel, src in (("config.yaml", paths.config), ("MEMORY.md", paths.data / "MEMORY.md")):
        if src.is_file():
            entries.append(_copy_entry(src, dest_dir / rel, rel))
        else:
            skipped.append(f"{rel}（不存在）")

    if include_skills:
        if paths.skills.is_dir():
            entries.append(_copy_tree(paths.skills, dest_dir / "skills", "skills"))
        else:
            skipped.append("skills/（不存在）")

    report = BackupReport(dest=dest_dir, created_at=_now(), entries=entries, skipped=skipped)
    (dest_dir / MANIFEST_NAME).write_text(
        json.dumps(report.to_dict(), ensure_ascii=False, indent=2), encoding="utf-8"
    )
    return report


def read_manifest(backup_dir: Path) -> dict:
    path = Path(backup_dir) / MANIFEST_NAME
    if not path.is_file():
        raise BackupError(f"不是备份目录（缺 {MANIFEST_NAME}）：{backup_dir}")
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise BackupError(f"备份清单不可读：{exc}") from exc
    if not isinstance(data, dict) or data.get("version") != MANIFEST_VERSION:
        raise BackupError("备份清单版本不受支持（可能是别的版本写的）")
    return data


def verify_database(path: Path) -> str:
    """对任意 db 文件跑 integrity_check；返回 'ok' 或抛 BackupError。"""
    path = Path(path)
    if not path.is_file():
        raise BackupError(f"数据库不存在：{path}")
    conn = sqlite3.connect(str(path))
    try:
        row = conn.execute("PRAGMA integrity_check").fetchone()
    except sqlite3.DatabaseError as exc:
        raise BackupError(f"无法校验 {path.name}：{exc}") from exc
    finally:
        conn.close()
    detail = str(row[0]) if row else "无返回"
    if detail != "ok":
        raise BackupError(f"{path.name} 完整性校验未通过：{detail}")
    return detail


def restore_into(backup_dir: Path, target_dir: Path, *, overwrite: bool = False) -> list[Entry]:
    """把备份恢复到一个**新目录**（默认不覆盖已有文件）。

    恢复前后各校验一次数据库副本：还原失败时宁可停在这里，也不要让用户拿半个库去启动。
    """
    manifest = read_manifest(backup_dir)
    backup_dir = Path(backup_dir)
    target_dir = Path(target_dir)
    restored: list[Entry] = []
    for raw in manifest.get("entries") or []:
        rel = str(raw.get("rel") or "")
        if not rel or rel.startswith("..") or Path(rel).is_absolute():
            raise BackupError(f"清单里有非法路径：{rel!r}")
        src = backup_dir / rel
        dest = target_dir / rel
        if not src.exists():
            raise BackupError(f"备份不完整，缺少 {rel}")
        if dest.exists() and not overwrite:
            raise BackupError(f"目标已存在，拒绝覆盖：{dest}")
        if raw.get("kind") == "database":
            verify_database(src)
            dest.parent.mkdir(parents=True, exist_ok=True)
            try:
                snapshot_sqlite(src, dest)  # 再走一次快照：复制 + 校验
                verify_database(dest)
                digest = sha256_file(dest)
                if digest != raw.get("sha256"):
                    raise BackupError(f"恢复后校验和不一致：{rel}")
            except BackupError:
                # 不留一份"看起来成功了"的坏副本：恢复失败必须是干净的失败
                dest.unlink(missing_ok=True)
                raise
            restored.append(
                Entry(
                    rel=rel,
                    kind="database",
                    source=str(src),
                    size=dest.stat().st_size,
                    sha256=digest,
                    rows=_count_tables(dest, dest.name),
                )
            )
        elif raw.get("kind") == "tree":
            partial = dest.with_name(dest.name + ".partial")
            shutil.rmtree(partial, ignore_errors=True)
            try:
                entry = _copy_tree(src, partial, rel)
            except OSError:
                shutil.rmtree(partial, ignore_errors=True)
                raise
            dest.unlink(missing_ok=True)
            shutil.rmtree(dest, ignore_errors=True)
            partial.replace(dest)
            restored.append(entry)
        else:
            partial = dest.with_name(dest.name + ".partial")
            partial.unlink(missing_ok=True)
            try:
                entry = _copy_entry(src, partial, rel)
            except OSError:
                partial.unlink(missing_ok=True)
                raise
            partial.replace(dest)
            restored.append(entry)
    return restored


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="omni-backup", description="离线备份/恢复运行态")
    parser.add_argument("--to", help="备份输出目录")
    parser.add_argument("--restore", help="要恢复的备份目录")
    parser.add_argument("--into", help="恢复到哪个新目录（必须为空或不存在）")
    parser.add_argument("--data-dir", default=str(DATA_DIR), help="运行数据根（默认 data/）")
    parser.add_argument("--config", default=None, help="配置源路径")
    parser.add_argument("--skills", default=None, help="技能库路径")
    parser.add_argument("--overwrite", action="store_true", help="恢复时允许覆盖目标文件")
    parser.add_argument("--no-skills", action="store_true", help="备份时不包含技能库")
    args = parser.parse_args(argv)

    paths = RuntimePaths.create(Path(args.data_dir), config_path=args.config, skills_root=args.skills)
    try:
        if args.restore:
            if not args.into:
                parser.error("--restore 需要同时给 --into")
            entries = restore_into(Path(args.restore), Path(args.into), overwrite=args.overwrite)
            print(f"已恢复到 {args.into}：{len(entries)} 项（数据库已校验）")
            for e in entries:
                print(f"  {e.rel}  {e.size} B  {e.sha256[:12]}")
            return 0
        if not args.to:
            parser.error("需要 --to（备份）或 --restore + --into（恢复）")
        report = backup_runtime(paths, Path(args.to), include_skills=not args.no_skills)
    except BackupError as exc:
        print(f"失败：{exc}", file=sys.stderr)
        return 2
    print(f"已备份到 {report.dest}：{len(report.entries)} 项")
    for e in report.entries:
        suffix = f" rows={e.rows}" if e.rows else (f" files={e.files}" if e.kind == "tree" else "")
        print(f"  {e.rel}  {e.size} B  {e.sha256[:12]}{suffix}")
    for item in report.skipped:
        print(f"  跳过 {item}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
