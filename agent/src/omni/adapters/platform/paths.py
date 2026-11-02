"""Resolve writable instance paths independently from installed code and cwd."""

from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path

from platformdirs import user_data_path


@dataclass(frozen=True)
class InstancePaths:
    home: Path

    @classmethod
    def resolve(cls, home: Path | None = None) -> InstancePaths:
        chosen = home or os.environ.get("OMNI_HOME") or user_data_path("OmniOperator", appauthor=False) / "v2"
        return cls(Path(chosen).expanduser().resolve())

    @property
    def database(self) -> Path:
        return self.home / "data" / "omni.db"

    @property
    def logs(self) -> Path:
        return self.home / "logs"

    @property
    def objects(self) -> Path:
        return self.home / "objects"

    @property
    def control(self) -> Path:
        return self.home / "run"

    def initialize(self) -> None:
        if any((self.home / name).exists() for name in ("memory.db", "state.db", "data/memory.db", "data/state.db")):
            raise OSError("Choose a fresh V2 home; legacy data is not imported")
        for path in (self.home, self.database.parent, self.logs, self.objects, self.control):
            if path.is_symlink() or path.is_junction():
                raise OSError("Instance paths cannot contain links")
            path.mkdir(parents=True, exist_ok=True, mode=0o700)
            if os.name != "nt":
                path.chmod(0o700)
        from .permissions import private_path

        private_path(self.home, directory=True)


class InstanceLock:
    """An OS-owned lock. It is never stolen because a wall-clock TTL expired."""

    def __init__(self, paths: InstancePaths) -> None:
        self.path = paths.control / "instance.lock"
        self._file = None

    def acquire(self) -> None:
        from ...domain.contracts import BusinessError

        if self.path.is_symlink():
            raise BusinessError("unsafe_lock", "Instance lock is a link")
        self.path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
        stream = self.path.open("a+b")
        stream.seek(0)
        try:
            if os.name == "nt":
                import msvcrt

                msvcrt.locking(stream.fileno(), msvcrt.LK_NBLCK, 1)
            else:
                import fcntl

                fcntl.flock(stream.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
            if self.path.stat().st_size == 0:
                stream.write(b"0")
                stream.flush()
        except OSError as exc:
            stream.close()
            raise BusinessError("instance_busy", "This instance is already running") from exc
        self._file = stream

    def close(self) -> None:
        if self._file is not None:
            self._file.seek(0)
            if os.name == "nt":
                import msvcrt

                msvcrt.locking(self._file.fileno(), msvcrt.LK_UNLCK, 1)
            else:
                import fcntl

                fcntl.flock(self._file.fileno(), fcntl.LOCK_UN)
            self._file.close()
            self._file = None
