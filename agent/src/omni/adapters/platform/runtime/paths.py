"""Resolve writable instance paths independently from installed code and cwd."""

from __future__ import annotations

import os
import stat
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
        if (self.home / "restore-in-progress.json").exists() or (self.home / "restore-in-progress.json").is_symlink():
            from omni.domain.contracts import BusinessError

            raise BusinessError(
                "restore_incomplete",
                "Restore did not finish; restore the verified backup into a fresh home",
                status=503,
            )
        if any((self.home / name).exists() for name in ("memory.db", "state.db", "data/memory.db", "data/state.db")):
            raise OSError("Choose a fresh V2 home; legacy data is not imported")
        for path in (self.home, self.database.parent, self.logs, self.objects, self.control):
            if path.is_symlink() or path.is_junction():
                raise OSError("Instance paths cannot contain links")
            path.mkdir(parents=True, exist_ok=True, mode=0o700)
            if os.name != "nt":
                path.chmod(0o700)
        from omni.adapters.platform.runtime.permissions import private_path

        # Children can predate the parent's protected Windows DACL. Set their
        # explicit private boundary too; inheritance alone preserves old ACEs.
        for path in (self.home, self.database.parent, self.logs, self.objects, self.control):
            private_path(path, directory=True)


class PrivateFileLock:
    """An owned private file descriptor; no historical PID or implicit release through cancellation."""

    def __init__(self, path: Path) -> None:
        self.path = path
        self._descriptor: int | None = None

    def _admit(self, release: tuple[str, str] | None) -> None:
        pass

    def acquire(self, *, release: tuple[str, str] | None = None) -> None:
        from omni.domain.contracts import BusinessError

        if self._descriptor is not None:
            raise BusinessError("instance_busy", "Instance lock is already held")
        if any(path.is_symlink() or path.is_junction() for path in (self.path, *self.path.parents)):
            raise BusinessError("unsafe_lock", "Instance lock is a link")
        self.path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
        descriptor = os.open(
            self.path, os.O_RDWR | os.O_CREAT | getattr(os, "O_NOFOLLOW", 0) | getattr(os, "O_NONBLOCK", 0), 0o600
        )
        try:
            info = os.fstat(descriptor)
            if not stat.S_ISREG(info.st_mode) or info.st_nlink != 1:
                raise BusinessError("unsafe_lock", "Instance lock must be one regular file")
            if os.name == "nt":
                import msvcrt

                msvcrt.locking(descriptor, msvcrt.LK_NBLCK, 1)
            else:
                import fcntl

                fcntl.flock(descriptor, fcntl.LOCK_EX | fcntl.LOCK_NB)
            from omni.adapters.platform.runtime.permissions import private_path

            if any(path.is_symlink() or path.is_junction() for path in (self.path, *self.path.parents)):
                raise BusinessError("unsafe_lock", "Instance lock path changed")
            current = self.path.stat(follow_symlinks=False)
            if (current.st_dev, current.st_ino) != (info.st_dev, info.st_ino):
                raise BusinessError("unsafe_lock", "Instance lock identity changed")
            private_path(self.path)
            self._admit(release)
            if os.fstat(descriptor).st_size == 0:
                os.write(descriptor, b"0")
                os.fsync(descriptor)
        except OSError as exc:
            os.close(descriptor)

            raise BusinessError("instance_busy", "This instance is already running") from exc
        except BaseException:
            os.close(descriptor)
            raise
        self._descriptor = descriptor

    def close(self) -> None:
        descriptor, self._descriptor = self._descriptor, None
        if descriptor is None:
            return
        try:
            os.lseek(descriptor, 0, os.SEEK_SET)
            if os.name == "nt":
                import msvcrt

                msvcrt.locking(descriptor, msvcrt.LK_UNLCK, 1)
            else:
                import fcntl

                fcntl.flock(descriptor, fcntl.LOCK_UN)
        finally:
            os.close(descriptor)


class InstanceLock(PrivateFileLock):
    """The instance excludes startup into staged or retired release data."""

    def __init__(self, paths: InstancePaths) -> None:
        super().__init__(paths.control / "instance.lock")

    def _admit(self, release: tuple[str, str] | None) -> None:
        from omni.domain.contracts import BusinessError
        from omni.domain.release_fence import validate_fence
        from omni.adapters.platform.deployment_files import decode, read_file
        from omni.adapters.platform.runtime.permissions import assert_private

        fence_path = self.path.parent / "release-fence.json"
        if fence_path.exists() or fence_path.is_symlink() or fence_path.is_junction():
            try:
                assert_private(fence_path)
                marker = validate_fence(decode(read_file(fence_path, 4096, single=True)))
            except OSError as exc:
                raise BusinessError(
                    "release_fence_unavailable", "The release startup fence cannot be verified"
                ) from exc
            if release != (marker["release_id"], marker["plan_sha256"]):
                raise BusinessError("release_fenced", "This data directory is staged or retired by a reviewed release")
