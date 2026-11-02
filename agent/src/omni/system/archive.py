"""可恢复的项目归档事务；不自动挑选用户资料，不删除源数据库。"""

from __future__ import annotations

import hashlib
import json
import os
import uuid
from pathlib import Path, PurePosixPath

from .atomic import atomic_write_bytes, atomic_write_text
from .backup import snapshot_sqlite
from .filelock import FileLock


def _references(root: Path, names: set[str]) -> list[str]:
    """仅检查受管源码和当前文档；不会扫描私人数据、归档或其他Git工作树。"""
    references = []
    candidates = list(root.glob("*.md"))
    for folder in ("agent/src", "agent/scripts", "scripts", "web", "docs"):
        base = root / folder
        for path in base.rglob("*"):
            if (
                path.suffix in {".py", ".ps1", ".bat", ".js", ".html", ".css", ".md", ".toml"}
                and "node_modules" not in path.parts
            ):
                candidates.append(path)
    for path in candidates:
        if path.is_file() and not path.is_symlink() and not path.is_junction() and path.stat().st_size < 2_000_000:
            relative = path.relative_to(root).as_posix()
            if relative in names:
                continue
            text = path.read_text(encoding="utf-8", errors="replace").replace("\\", "/")
            if any(name in text for name in names):
                references.append(relative)
    return sorted(set(references))


def _validate_source(root: Path, relative: str, category: str) -> Path:
    path = _under(root, relative)
    parts = Path(relative).parts
    if not parts or any(part in {".git", ".venv"} for part in parts) or relative.startswith("temp/"):
        raise ValueError("归档不能包含版本库、运行环境或归档本身")
    if path.name.endswith(("-wal", "-shm")):
        raise ValueError("不能单独搬动SQLite的WAL/SHM")
    if (
        parts[0] in {"data", "config", "skills"}
        or path.name.startswith(".env")
        or path.suffix in {".db", ".sqlite", ".sqlite3"}
    ) and category != "private":
        raise ValueError("运行资料只能进入私密归档")
    if path.name in {"config.yaml", ".env", "web_secret"} or "browser_profile" in parts or "run" in parts:
        raise ValueError("活跃配置、凭据和运行身份不得自动迁移")
    return path


def _digest(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _under(root: Path, relative: str) -> Path:
    if (
        "\\" in relative
        or ":" in relative
        or PurePosixPath(relative).as_posix() != relative
        or ".." in PurePosixPath(relative).parts
    ):
        raise ValueError("归档路径必须是规范的仓库相对路径")
    path = root / relative
    if not path.resolve().is_relative_to(root) or path.resolve() == root:
        raise ValueError("归档路径越界")
    for part in (path, *path.parents):
        if part == root:
            break
        if part.is_symlink() or part.is_junction():
            raise ValueError("不能通过联接归档文件")
    return path


def _assert_idle(path: Path) -> None:
    if os.name != "nt" or not path.exists():
        return
    import ctypes
    from ctypes import wintypes

    kernel = ctypes.WinDLL("kernel32", use_last_error=True)
    kernel.CreateFileW.argtypes = [
        wintypes.LPCWSTR,
        wintypes.DWORD,
        wintypes.DWORD,
        ctypes.c_void_p,
        wintypes.DWORD,
        wintypes.DWORD,
        wintypes.HANDLE,
    ]
    kernel.CreateFileW.restype = wintypes.HANDLE
    kernel.CloseHandle.argtypes = [wintypes.HANDLE]
    kernel.CloseHandle.restype = wintypes.BOOL
    handle = kernel.CreateFileW(str(path), 0x80000000, 0, None, 3, 0x80, None)
    if handle == ctypes.c_void_p(-1).value:
        raise PermissionError("文件被占用或权限不足，未归档：" + path.name)
    kernel.CloseHandle(handle)


def _write(path: Path, state: dict) -> None:
    atomic_write_text(path, json.dumps(state, ensure_ascii=False, indent=2) + "\n")


def plan(root: Path, names: list[str], *, category: str, reason: str) -> Path:
    root = root.resolve()
    if category not in {"archive", "private", "build"} or not reason.strip():
        raise ValueError("必须声明归档区域与理由")
    identity = uuid.uuid4().hex
    folder = _under(root, f"temp/{category}/{identity}")
    entries = []
    seen = set()
    for name in names:
        source = _under(root, name)
        if source.is_dir():
            if (source / ".git").exists():
                raise ValueError("Git工作树须通过Git移动，不作为普通目录归档")
            files = sorted(path for path in source.rglob("*") if path.is_file())
        else:
            files = [source]
        for path in files:
            relative = path.relative_to(root).as_posix()
            _validate_source(root, relative, category)
            if relative in seen or not path.is_file():
                continue
            kind = "database" if path.suffix in {".db", ".sqlite", ".sqlite3"} else "file"
            entries.append(
                {
                    "source": relative,
                    "target": (folder / "files" / relative).relative_to(root).as_posix(),
                    "sha256": _digest(path),
                    "kind": kind,
                    "status": "planned",
                }
            )
            seen.add(relative)
    if not entries:
        raise ValueError("没有可归档的文件")
    folder.mkdir(parents=True)
    manifest = folder / "manifest.json"
    references = _references(root, seen)
    _write(
        manifest,
        {
            "schema": 1,
            "root": str(root),
            "category": category,
            "reason": reason,
            "references": references,
            "entries": entries,
        },
    )
    return manifest


def execute(manifest: Path, *, restore: bool = False) -> dict:
    manifest = manifest.resolve()
    with FileLock(manifest.with_suffix(".lock")):
        state = json.loads(manifest.read_text(encoding="utf-8"))
        if state.get("schema") != 1:
            raise ValueError("不支持的归档清单")
        root = Path(state["root"]).resolve()
        expected = _under(root, manifest.relative_to(root).as_posix())
        if expected != manifest or not manifest.is_relative_to(root / "temp"):
            raise ValueError("归档清单位置无效")
        category = manifest.relative_to(root).parts[1]
        if category not in {"archive", "private", "build"}:
            raise ValueError("归档区域无效")
        if not restore:
            names = {entry["source"] for entry in state["entries"]}
            references = _references(root, names)
            if references:
                raise ValueError("仍有引用，先更新引用再归档：" + ", ".join(references[:10]))
        # 先校验整批，避免可预见的冲突导致半批移动。
        for entry in state["entries"]:
            _validate_source(root, entry["source"], category)
            source, target = (_under(root, entry[key]) for key in ("source", "target"))
            if not target.is_relative_to(manifest.parent / "files"):
                raise ValueError("目标不属于本归档")
            available = target if target.exists() else source
            snapshot_matches = (
                entry["kind"] == "database" and target.is_file() and entry.get("snapshot_sha256") == _digest(target)
            )
            if (not available.is_file() or _digest(available) != entry["sha256"]) and not snapshot_matches:
                raise ValueError("文件已变化，拒绝覆盖或归档")
            valid_source = {entry["sha256"]}
            if entry["kind"] == "database" and entry.get("snapshot_sha256"):
                valid_source.add(entry["snapshot_sha256"])
            if restore and source.exists() and _digest(source) not in valid_source:
                raise FileExistsError("原位置已有不同内容，拒绝覆盖")
            if entry["kind"] != "database":
                _assert_idle(available)
            elif (
                restore
                and not source.exists()
                and any(source.with_name(source.name + suffix).exists() for suffix in ("-wal", "-shm"))
            ):
                raise ValueError("原位置仍有SQLite侧文件，须先关闭原连接并核查")
        for entry in state["entries"]:
            source, target = (_under(root, entry[key]) for key in ("source", "target"))
            if restore:
                if not source.exists():
                    source.parent.mkdir(parents=True, exist_ok=True)
                    if entry["kind"] == "database":
                        if (
                            source.with_name(source.name + "-wal").exists()
                            or source.with_name(source.name + "-shm").exists()
                        ):
                            raise ValueError("原位置仍有SQLite侧文件，须先关闭原连接并核查")
                        atomic_write_bytes(source, target.read_bytes())
                    else:
                        target.rename(source)
                entry["status"] = "restored"
            elif not target.exists():
                target.parent.mkdir(parents=True, exist_ok=True)
                if entry["kind"] == "database":
                    partial = target.with_suffix(target.suffix + ".partial")
                    _under(root, partial.relative_to(root).as_posix())
                    if partial.exists() and not entry.get("snapshot_sha256"):
                        partial.unlink()  # 本归档事务未完成的派生快照，可从原库重新生成。
                    if partial.exists() and _digest(partial) != entry.get("snapshot_sha256"):
                        raise ValueError("暂存快照已变化，拒绝继续")
                    if not (partial.is_file() and entry.get("snapshot_sha256") == _digest(partial)):
                        snapshot_sqlite(source, partial)
                        entry["snapshot_sha256"] = _digest(partial)
                        _write(manifest, state)
                    partial.replace(target)
                else:
                    source.rename(target)
                entry["status"] = "archived"
            _write(manifest, state)
        return state
