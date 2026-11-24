"""可随恢复执行器冻结的标准库协议、原子状态和路径约束；不导入运行中应用。"""

from __future__ import annotations

import errno
import hashlib
import json
import os
import re
import sqlite3
import subprocess
import tempfile
import time
from collections.abc import Callable
from pathlib import Path, PurePosixPath
from typing import Any, TypeVar

REPOSITORY = "https://gitee.com/chenfengloveyuri/OmniOperator"
SHA = re.compile(r"^[0-9a-f]{40}$")
OP_ID = re.compile(r"^[0-9a-f]{32}$")
TERMINAL = {"succeeded", "failed", "rolled_back", "cancelled"}
MANAGED_ROOTS = {"agent", "scripts", "docs", "web"}
MANAGED_FILES = {
    "pyproject.toml",
    "uv.lock",
    "start.bat",
    "README.md",
    "README.en.md",
    "CHANGELOG.md",
    "LICENSE",
    "CONTRIBUTING.md",
    "SECURITY.md",
    "CODE_OF_CONDUCT.md",
    "AGENTS.md",
    "Jenkinsfile",
    ".gitignore",
    ".editorconfig",
    ".gitattributes",
    ".python-version",
}
T = TypeVar("T")


class UpdateError(RuntimeError):
    def __init__(self, code: str, message: str):
        self.code = code
        super().__init__(message)


def error_message(error: Exception) -> str:
    """恢复后仍展示失败原因；不把任意异常中的配置、命令输出或凭据返回浏览器。"""
    if isinstance(error, UpdateError):
        return str(error)
    if isinstance(error, PermissionError):
        return "文件被占用或目录权限不足，请关闭相关程序并检查权限"
    if isinstance(error, OSError):
        return "磁盘空间不足，请先清理空间" if error.errno == errno.ENOSPC else "文件系统操作失败，请检查磁盘和目录权限"
    if isinstance(error, sqlite3.Error):
        return "数据库快照或迁移校验失败，原始恢复点已保留"
    return "更新步骤未能完成，请查看本地恢复记录"


def digest(path: Path) -> str:
    result = hashlib.sha256()
    with path.open("rb") as stream:
        for block in iter(lambda: stream.read(1024 * 1024), b""):
            result.update(block)
    return result.hexdigest()


def retry_file(action: Callable[[], T]) -> T:
    """仅重试 Windows 短暂文件共享冲突，不重做业务动作。"""
    deadline = time.monotonic() + 1
    while True:
        try:
            return action()
        except PermissionError as exc:
            sharing = getattr(exc, "winerror", None) in {5, 32, 33} or (
                os.name == "nt" and exc.errno in {errno.EACCES, errno.EBUSY}
            )
            if not sharing or time.monotonic() >= deadline:
                raise
            time.sleep(0.01)


def remove_file(path: Path) -> None:
    retry_file(lambda: no_links(path).unlink(missing_ok=True))


def read_json(path: Path, default: Any = None) -> Any:
    try:
        return json.loads(retry_file(lambda: path.read_text(encoding="utf-8")))
    except FileNotFoundError:
        return default
    except (ValueError, OSError) as exc:
        raise UpdateError("state_invalid", "更新状态无法读取，请先恢复未完成的事务") from exc


def write_json(path: Path, value: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, name = tempfile.mkstemp(prefix=".update-", dir=path.parent)
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as stream:
            json.dump(value, stream, ensure_ascii=False, indent=2)
            stream.flush()
            os.fsync(stream.fileno())
        retry_file(lambda: os.replace(name, path))
    finally:
        if os.path.exists(name):
            os.unlink(name)


def no_links(path: Path) -> Path:
    path = path.absolute()
    for item in (path, *path.parents):
        if item.is_symlink() or item.is_junction():
            raise UpdateError("unsafe_path", "更新路径不能经过符号链接或 junction")
    return path.resolve()


def relative_name(value: str) -> str:
    if not isinstance(value, str) or not value or "\\" in value or ":" in value:
        raise UpdateError("unsafe_archive", "无效的归档路径")
    parts = PurePosixPath(value).parts
    reserved = {"con", "prn", "aux", "nul", *(f"com{i}" for i in range(1, 10)), *(f"lpt{i}" for i in range(1, 10))}
    if value.startswith("/") or any(
        p in {".", ".."}
        or p.endswith((" ", "."))
        or p.split(".")[0].lower() in reserved
        or any(ord(c) < 32 or c in '<>"|?*' for c in p)
        for p in parts
    ):
        raise UpdateError("unsafe_archive", "归档包含不安全的路径")
    if "/".join(parts) != value:
        raise UpdateError("unsafe_archive", "归档路径必须规范化")
    return value


def managed(name: str) -> bool:
    relative_name(name)
    parts = PurePosixPath(name).parts
    if name.casefold() == "docs/temp" or name.casefold().startswith("docs/temp/"):
        return False
    return (parts[0] in MANAGED_ROOTS or name in MANAGED_FILES) and not any(
        part in {".venv", "node_modules", "__pycache__", ".pytest_cache", ".tmp"} for part in parts
    )


def run(argv: list[str], *, cwd: Path, timeout: float = 60, env: dict | None = None, on_spawn=None) -> str:
    try:
        from ..process_identity import inspect_process, terminate_verified_tree

        if Path(argv[0]).name.lower() in {"git", "git.exe"}:
            env = (env if env is not None else os.environ).copy()
            env.update(GIT_TERMINAL_PROMPT="0", GCM_INTERACTIVE="never")
        child = subprocess.Popen(
            argv,
            cwd=cwd,
            env=env,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            stdin=subprocess.DEVNULL,
            creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
        )
        identity = inspect_process(child.pid)
        if on_spawn:
            try:
                on_spawn(identity)
            except Exception as exc:
                try:
                    if identity:
                        terminate_verified_tree(identity)
                    else:
                        child.kill()
                    child.communicate(timeout=5)
                except Exception as cleanup:
                    raise UpdateError("command_residual", "检查点写入失败且子进程未确认回收") from cleanup
                raise UpdateError("checkpoint_failed", "命令检查点无法保存，已停止执行") from exc
        try:
            stdout, _stderr = child.communicate(timeout=timeout)
        except subprocess.TimeoutExpired as exc:
            try:
                if identity is None:
                    child.kill()
                    child.wait(timeout=5)
                    raise UpdateError("command_residual", "超时命令的进程树无法完整核验，保留维护状态")
                terminate_verified_tree(identity)
                child.communicate(timeout=5)
            except UpdateError:
                raise
            except Exception as cleanup:
                raise UpdateError("command_residual", "更新命令存在未确认的子进程，保留维护状态") from cleanup
            raise UpdateError("command_timeout", "更新命令超时，已确认回收进程树") from exc
    except OSError as exc:
        raise UpdateError("command_failed", "更新所需命令不可用或执行超时") from exc
    if child.returncode:
        # 子进程 stderr 可能包含包源凭据，不进入接口或持久状态。
        raise UpdateError("command_failed", f"{Path(argv[0]).name} 执行失败，请检查本机依赖与目录权限")
    return stdout.decode("utf-8", errors="replace").strip()


def git(root: Path, *args: str) -> str:
    return run(["git", "-c", "core.hooksPath=", "-C", str(root), *args], cwd=root)
