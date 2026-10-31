"""原子写文件（A09 的同源问题）。

以前各处自己写 `.tmp`（固定名字）再 `replace`：
- 两个进程同时写同一个目标 → 互相覆盖对方的临时文件；
- 写到一半崩溃 → 留下半截临时文件，甚至可能已经替换成功一半；
- 没有 fsync → 断电后可能拿到"文件存在但内容为空"。

这里做四件事：**同目录唯一临时名**（保证 `os.replace` 是同分区原子重命名）、
写入后 `flush + fsync`、失败时删除临时文件且**绝不动原文件**、
POSIX 上再同步父目录（掉电后 rename 的目录项也落盘；Windows 无此概念，跳过）。
"""
from __future__ import annotations

import contextlib
import os
import tempfile
from pathlib import Path


def _sync_parent_dir(path: Path) -> None:
    """rename 后同步父目录项；O_DIRECTORY 不存在的平台（Windows）无操作。"""
    flags = getattr(os, "O_RDONLY", 0) | getattr(os, "O_DIRECTORY", 0)
    if not getattr(os, "O_DIRECTORY", 0):
        return
    with contextlib.suppress(OSError):
        fd = os.open(path, flags)
        try:
            os.fsync(fd)
        finally:
            os.close(fd)


def atomic_write_bytes(path: Path, data: bytes) -> None:
    """原子替换整个文件；失败时原文件保持原样。"""
    target = Path(path)
    target.parent.mkdir(parents=True, exist_ok=True)
    fd, tmp_name = tempfile.mkstemp(
        prefix=f".{target.name}.", suffix=".tmp", dir=str(target.parent)
    )
    try:
        with os.fdopen(fd, "wb") as stream:
            stream.write(data)
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(tmp_name, target)
        _sync_parent_dir(target.parent)
    except BaseException:
        with contextlib.suppress(OSError):
            os.unlink(tmp_name)
        raise


def atomic_write_text(path: Path, text: str, *, encoding: str = "utf-8") -> None:
    atomic_write_bytes(path, text.encode(encoding))
