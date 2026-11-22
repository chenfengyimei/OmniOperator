"""Owner-only managed persona growth cache; validates bytes against the ledger expectation."""

from __future__ import annotations

import datetime as dt
import errno
import os
import stat
import threading
import uuid
from pathlib import Path

from ..domain.contracts import BusinessError
from ..domain.persona_growth import MAX_GROWTH_BYTES
from .object_store import checked_path, publish_file, sync_directory

FILE_NAME = "growth.md"
TEMP_PREFIX = "growth.md.tmp-"


def _unsafe(message: str) -> BusinessError:
    return BusinessError("persona_growth_unsafe", message, status=409)


def _unreadable(message: str) -> BusinessError:
    return BusinessError("persona_growth_unreadable", message, retryable=True, status=503)


def _agent_root(root: Path, agent: str) -> Path:
    try:
        canonical = str(uuid.UUID(agent))
    except ValueError as exc:
        raise BusinessError("persona_growth_invalid_agent", "Agent identity must be a UUID", status=422) from exc
    if canonical != agent:
        raise BusinessError("persona_growth_invalid_agent", "Agent identity must be canonical", status=422)
    if root.is_symlink() or root.is_junction():
        raise _unsafe("Growth root cannot be a link")
    path = root / canonical
    if path.is_symlink() or path.is_junction():
        raise _unsafe("Growth directory cannot be a link")
    if path.exists() and not path.is_dir():
        raise _unsafe("Growth directory must be a directory")
    return path


class PersonaGrowthFiles:
    """Publish and verify each agent's `growth.md` under one managed root.

    The database ledger stays authoritative: :meth:`read` only returns bytes equal
    to the caller's expected rendering, so a cached file can never resurrect a
    retracted or erased sentence. Agents never share a cache file.
    """

    def __init__(self, root: Path) -> None:
        self.root = root
        self._mutex = threading.Lock()

    def _target(self, directory: Path) -> Path:
        try:
            return checked_path(directory, FILE_NAME)
        except BusinessError as exc:
            raise _unsafe(str(exc.message)) from exc

    def _clean_temps(self, directory: Path) -> None:
        for child in directory.iterdir():
            if not child.name.startswith(TEMP_PREFIX):
                continue
            if child.is_symlink() or child.is_junction():
                raise _unsafe("Growth temporary file cannot be a link")
            if not child.is_file() or child.stat().st_nlink != 1:
                raise _unsafe("Growth temporary file must be a private regular file")
            child.unlink(missing_ok=True)

    @staticmethod
    def _stamp(path: Path) -> str:
        return dt.datetime.fromtimestamp(path.stat().st_mtime, tz=dt.timezone.utc).isoformat()

    def read(self, agent: str, expected: bytes) -> tuple[bytes, str] | None:
        """Return the cached bytes and build time only when they match ``expected``."""
        with self._mutex:
            directory = _agent_root(self.root, agent)
            if not directory.exists():
                return None
            target = self._target(directory)
            flags = os.O_RDONLY | getattr(os, "O_NOFOLLOW", 0) | getattr(os, "O_BINARY", 0)
            try:
                descriptor = os.open(target, flags)
            except FileNotFoundError:
                return None
            except OSError as exc:
                if exc.errno in {errno.ELOOP, errno.EMLINK}:
                    raise _unsafe("Growth cache cannot be a link") from exc
                raise _unreadable("Growth cache could not be opened") from exc
            try:
                with os.fdopen(descriptor, "rb") as stream:
                    info = os.fstat(stream.fileno())
                    if (
                        not stat.S_ISREG(info.st_mode)
                        or info.st_nlink != 1
                        or info.st_size > MAX_GROWTH_BYTES
                        or info.st_size != len(expected)
                    ):
                        return None
                    content = stream.read(MAX_GROWTH_BYTES + 1)
            except OSError as exc:
                raise _unreadable("Growth cache could not be read") from exc
            if content != expected:
                return None
            return content, dt.datetime.fromtimestamp(info.st_mtime, tz=dt.timezone.utc).isoformat()

    def publish(self, agent: str, content: bytes) -> str:
        """Atomically replace this agent's cache; returns the new build time."""
        if len(content) > MAX_GROWTH_BYTES:
            raise BusinessError("persona_growth_too_large", "Growth cache exceeds 1 MiB", status=413)
        with self._mutex:
            directory = _agent_root(self.root, agent)
            directory.mkdir(parents=True, exist_ok=True, mode=0o700)
            directory = _agent_root(self.root, agent)
            self._clean_temps(directory)
            target = self._target(directory)
            temporary = directory / f"{TEMP_PREFIX}{uuid.uuid4()}"
            flags = os.O_WRONLY | os.O_CREAT | os.O_EXCL | getattr(os, "O_NOFOLLOW", 0) | getattr(os, "O_BINARY", 0)
            descriptor = os.open(temporary, flags, 0o600)
            try:
                with os.fdopen(descriptor, "wb") as stream:
                    stream.write(content)
                    stream.flush()
                    os.fsync(stream.fileno())
                publish_file(temporary, target, replace=True)
            except BaseException:
                temporary.unlink(missing_ok=True)
                raise
            return self._stamp(target)

    def invalidate(self, agent: str) -> bool:
        """Remove this agent's cache and temporary files; True only if a cache existed."""
        with self._mutex:
            directory = _agent_root(self.root, agent)
            if not directory.exists():
                return False
            self._clean_temps(directory)
            target = self._target(directory)
            existed = target.exists()
            target.unlink(missing_ok=True)
            if os.name != "nt":
                sync_directory(directory)
            if target.exists() or target.is_symlink():
                raise BusinessError(
                    "persona_growth_cleanup_failed", "Growth cache is still present", retryable=True, status=503
                )
            return existed


__all__ = ["FILE_NAME", "TEMP_PREFIX", "PersonaGrowthFiles"]
