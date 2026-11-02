"""停写后的升级恢复快照；保留本机原值，清单验证先于任何原位恢复。"""

from __future__ import annotations

import shutil
import sqlite3
import time
from contextlib import closing
from pathlib import Path

from .common import UpdateError, digest, no_links, read_json, write_json


def sqlite_copy(source: Path, target: Path) -> None:
    no_links(source)
    no_links(target)
    if source.resolve() == target.resolve() or target.exists():
        raise UpdateError("snapshot_failed", "数据库快照目标已存在或与源相同")
    target.parent.mkdir(parents=True, exist_ok=True)
    deadline = time.monotonic() + 30

    def progress(*_):
        if time.monotonic() > deadline:
            raise UpdateError("snapshot_timeout", "数据库快照超时")

    with (
        closing(sqlite3.connect(source.resolve().as_uri() + "?mode=ro", uri=True)) as src,
        closing(sqlite3.connect(target)) as dst,
    ):
        src.backup(dst, pages=256, progress=progress, sleep=0.01)
        if dst.execute("PRAGMA integrity_check").fetchone() != ("ok",):
            raise UpdateError("snapshot_failed", "数据库快照完整性校验失败")


def capture(target: Path, sources: list[dict]) -> dict:
    if target.exists():
        raise UpdateError("snapshot_exists", "恢复点已存在，拒绝覆盖")
    target.mkdir(parents=True)
    entries, roots = [], []
    for index, item in enumerate(sources):
        path = no_links(Path(item["path"]))
        if path == Path(path.anchor):
            raise UpdateError("unsafe_path", "不能快照磁盘根目录")
        roots.append({"path": str(path), "kind": item["kind"], "existed": path.exists()})
        files = [path] if path.is_file() else sorted(path.rglob("*")) if path.is_dir() else []
        for file in files:
            no_links(file)
            if not file.is_file():
                continue
            rel = str(index) + "/" + (file.relative_to(path).as_posix() if path.is_dir() else "file")
            copy = target / rel
            copy.parent.mkdir(parents=True, exist_ok=True)
            if item["kind"] == "database":
                sqlite_copy(file, copy)
            else:
                shutil.copy2(file, copy)
            entries.append({"source": str(file), "copy": rel, "sha256": digest(copy), "root": index})
    manifest = {"schema": 1, "roots": roots, "entries": entries}
    write_json(target / "snapshot.json", manifest)
    return manifest


def restore(target: Path, expected_sources: list[dict]) -> None:
    data = read_json(target / "snapshot.json")
    if not data or [(row["path"], row["kind"]) for row in data["roots"]] != [
        (str(no_links(Path(row["path"]))), row["kind"]) for row in expected_sources
    ]:
        raise UpdateError("invalid_snapshot", "恢复点与本次升级目录不匹配")
    for row in data["entries"]:
        path = no_links(target / row["copy"])
        source = no_links(Path(row["source"]))
        root = Path(data["roots"][row["root"]]["path"])
        if (
            not path.is_relative_to(target.resolve())
            or not (source == root or source.is_relative_to(root))
            or digest(path) != row["sha256"]
        ):
            raise UpdateError("invalid_snapshot", "恢复快照校验失败，未覆盖任何数据")
    for root in data["roots"]:
        path = no_links(Path(root["path"]))
        if root["kind"] == "tree" and path.exists():
            # 只清理冻结清单明确声明的业务树，不使用 shell 或推导出的宽泛父目录。
            for child in path.rglob("*"):
                no_links(child)
            shutil.rmtree(path)
        elif path.exists():
            path.unlink()
        if root["kind"] == "database":
            for suffix in ("-wal", "-shm"):
                no_links(Path(str(path) + suffix)).unlink(missing_ok=True)
    for row in data["entries"]:
        path = Path(row["source"])
        path.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(target / row["copy"], path)
