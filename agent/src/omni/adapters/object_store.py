"""Private, immutable content-addressed files; never opens the application database."""

from __future__ import annotations

import asyncio
import contextvars
import hashlib
import os
import stat
import threading
import uuid
from collections.abc import Callable
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from typing import TypeVar

from ..domain.contracts import BusinessError
from ..domain.objects import MAX_OBJECT_BYTES, Blob

T = TypeVar("T")


def checked_path(root: Path, relative: str) -> Path:
    path = root / relative
    if (
        not path.absolute().is_relative_to(root.absolute())
        or ".." in Path(relative).parts
        or Path(relative).is_absolute()
    ):
        raise BusinessError("unsafe_object_path", "Object path escapes its store", status=422)
    for current in (root, *path.relative_to(root).parents[::-1]):
        candidate = current if current == root else root / current
        if candidate.is_symlink() or candidate.is_junction():
            raise BusinessError("unsafe_object_path", "Object paths cannot contain links", status=409)
    if path.is_symlink() or path.is_junction():
        raise BusinessError("unsafe_object_path", "Object cannot be a link", status=409)
    if path.exists() and (not path.is_file() or path.stat().st_nlink != 1):
        raise BusinessError("unsafe_object_path", "Object must be a private regular file", status=409)
    return path


def sync_directory(path: Path) -> None:
    if os.name != "nt":
        descriptor = os.open(path, os.O_RDONLY | getattr(os, "O_DIRECTORY", 0))
        try:
            os.fsync(descriptor)
        finally:
            os.close(descriptor)


def publish_file(source: Path, target: Path, *, replace: bool = False) -> None:
    if os.name == "nt":
        import ctypes
        from ctypes import wintypes

        move = ctypes.WinDLL("kernel32", use_last_error=True).MoveFileExW
        move.argtypes = [wintypes.LPCWSTR, wintypes.LPCWSTR, wintypes.DWORD]
        move.restype = wintypes.BOOL
        if not move(
            str(source), str(target), 0x8 | (0x1 if replace else 0)
        ):  # WRITE_THROUGH, explicit replacement only
            raise OSError(ctypes.get_last_error(), "Object publication failed")
    else:
        # The instance lock and local mutex own publication; no other publisher
        # can introduce a destination after the existence check.
        if replace:
            os.replace(source, target)
        else:
            os.rename(source, target)
        sync_directory(target.parent)


class FileObjects:
    def __init__(self, root: Path) -> None:
        self.root = root.absolute()
        self._mutex = threading.Lock()

    def read(self, blob: Blob) -> bytes:
        path = checked_path(self.root, blob.key)
        try:
            flags = os.O_RDONLY | getattr(os, "O_NOFOLLOW", 0) | getattr(os, "O_BINARY", 0)
            descriptor = os.open(path, flags)
            with os.fdopen(descriptor, "rb") as stream:
                info = os.fstat(stream.fileno())
                if not stat.S_ISREG(info.st_mode) or info.st_nlink != 1 or info.st_size != blob.size:
                    raise BusinessError("object_integrity_failed", "Object size or ownership changed", status=503)
                content = stream.read(MAX_OBJECT_BYTES + 1)
        except FileNotFoundError as exc:
            raise BusinessError("object_missing", "Registered object is missing from storage", status=503) from exc
        if len(content) != blob.size or hashlib.sha256(content).hexdigest() != blob.sha256:
            raise BusinessError("object_integrity_failed", "Object fingerprint changed", status=503)
        return content

    def put(self, agent_id: str, content: bytes) -> Blob:
        blob = Blob(agent_id, hashlib.sha256(content).hexdigest(), len(content))
        with self._mutex:
            target = checked_path(self.root, blob.key)
            if target.exists():
                self.read(blob)
                return blob
            for directory in (self.root, self.root / agent_id, target.parent):
                checked_path(self.root, directory.relative_to(self.root).as_posix() + "/.check")
                directory.mkdir(parents=True, exist_ok=True, mode=0o700)
                sync_directory(directory.parent)
            temporary = checked_path(self.root, f"{agent_id}/{blob.sha256[:2]}/.{uuid.uuid4()}.partial")
            try:
                descriptor = os.open(
                    temporary, os.O_WRONLY | os.O_CREAT | os.O_EXCL | getattr(os, "O_BINARY", 0), 0o600
                )
                with os.fdopen(descriptor, "wb") as stream:
                    stream.write(content)
                    stream.flush()
                    os.fsync(stream.fileno())
                checked_path(self.root, blob.key)
                publish_file(temporary, target)
                # Publishing succeeds only after a second fingerprint check.
                self.read(blob)
                return blob
            finally:
                temporary.unlink(missing_ok=True)


class ObjectFiles:
    """Two I/O workers; the application service bounds queued operations."""

    def __init__(self, root: Path) -> None:
        self.files = FileObjects(root)
        self._executor = ThreadPoolExecutor(max_workers=2, thread_name_prefix="omni-object-io")

    async def _run(self, operation: Callable[[], T]) -> T:
        return await asyncio.get_running_loop().run_in_executor(
            self._executor, contextvars.copy_context().run, operation
        )

    async def put(self, agent_id: str, content: bytes) -> Blob:
        return await self._run(lambda: self.files.put(agent_id, content))

    async def read(self, blob: Blob) -> bytes:
        return await self._run(lambda: self.files.read(blob))

    async def close(self) -> None:
        self._executor.shutdown(wait=False)
