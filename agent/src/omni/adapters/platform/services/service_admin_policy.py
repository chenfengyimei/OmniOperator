"""Root-only fixed Linux paths and append-only dispatch evidence; never opens owner business storage."""

from __future__ import annotations

import datetime as dt
import json
import os
import stat
import sys
import uuid
from pathlib import Path
from typing import Any, cast

from omni.adapters.platform.services.service_templates import render
from omni.domain.contracts import BusinessError
from omni.domain.service_admin import (
    AUDIT_DIRECTORY,
    SOCKET_DIRECTORY,
    admin_scope,
    bound_operation,
    scoped_operations,
    socket_path,
    verify_broker,
)
from omni.domain.service_deployment import manager_identity


def error(code: str = "service_admin_path_unsafe") -> BusinessError:
    return BusinessError(code, "The scoped service administrator requires verified root-owned resources")


def root_path(path: Path, *, missing: bool = False) -> None:
    if not path.is_absolute() or ".." in path.parts:
        raise error()
    for current in (*reversed(path.parents), path):
        try:
            info = current.lstat()
        except FileNotFoundError:
            if missing and current == path:
                continue
            raise error() from None
        if stat.S_ISLNK(info.st_mode) or info.st_uid != 0 or info.st_mode & 0o022:
            raise error()
        if current != path and not stat.S_ISDIR(info.st_mode):
            raise error()


def root_read(path: Path, maximum: int = 65536) -> bytes:
    if sys.platform != "linux":
        raise error("service_admin_root_required")
    root_path(path)
    descriptor = os.open(path, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK | os.O_CLOEXEC)
    with os.fdopen(descriptor, "rb") as stream:
        before = os.fstat(stream.fileno())
        if (
            not stat.S_ISREG(before.st_mode)
            or before.st_nlink != 1
            or before.st_uid != 0
            or before.st_mode & 0o022
            or before.st_size > maximum
        ):
            raise error()
        data = stream.read(maximum + 1)
        after = os.fstat(stream.fileno())
    current = path.lstat()
    fields = lambda info: (info.st_dev, info.st_ino, info.st_size, info.st_mtime_ns, info.st_ctime_ns, info.st_nlink)
    if len(data) != before.st_size or fields(before) != fields(after) or fields(after) != fields(current):
        raise error("service_admin_file_changed")
    return data


def sync(path: Path) -> None:
    if sys.platform != "linux":
        raise error("service_admin_root_required")
    descriptor = os.open(path, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW | os.O_CLOEXEC)
    try:
        os.fsync(descriptor)
    finally:
        os.close(descriptor)


def publish(path: Path, data: bytes, mode: int = 0o600) -> None:
    if sys.platform != "linux":
        raise error("service_admin_root_required")
    root_path(path.parent)
    if path.exists() or path.is_symlink():
        raise error("service_admin_destination_exists")
    temporary = path.with_name("." + uuid.uuid4().hex + ".omni-admin.partial")
    descriptor = os.open(temporary, os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW | os.O_CLOEXEC, mode)
    try:
        with os.fdopen(descriptor, "wb") as stream:
            os.fchmod(stream.fileno(), mode)
            stream.write(data)
            stream.flush()
            os.fsync(stream.fileno())
        root_path(path, missing=True)
        os.link(temporary, path, follow_symlinks=False)
        temporary.unlink()
        sync(path.parent)
    finally:
        # This exact new name was created by this invocation; never enumerate a directory for cleanup.
        temporary.unlink(missing_ok=True)


class RootServicePolicy:
    def __init__(self, operation: object, broker_sha256: str) -> None:
        self.approval = admin_scope(operation)
        verify_broker(self.approval, broker_sha256)
        self.operation_key = next(iter(scoped_operations(self.approval)))
        self.operation = cast(dict[str, Any], bound_operation(self.approval, self.operation_key))
        self.broker_sha256 = broker_sha256
        self.plan = self.operation["deployment"]
        if self.plan["request"]["target"] != "linux-system":
            raise error("service_admin_target_unsupported")
        self.manager = manager_identity(self.plan)
        self._lock: int | None = None
        self._principal: tuple[int, int, str, str] | None = None

    def authorize(self) -> None:
        if (
            sys.platform != "linux"
            or getattr(os, "getuid", lambda: -1)() != 0
            or getattr(os, "geteuid", lambda: -1)() != 0
        ):
            raise error("service_admin_root_required")
        actor = self.plan["request"]["principal"]
        # Only local accounts are supported. Do not execute NSS modules or wait
        # on LDAP/network identity lookups inside the privileged transaction.
        matches = []
        for line in root_read(Path("/etc/passwd"), 1024 * 1024).decode("utf-8").splitlines():
            fields = line.split(":")
            if len(fields) == 7 and (fields[0] == actor["name"] or fields[2] == actor["id"]):
                if not fields[2].isdigit() or not fields[3].isdigit():
                    raise error("service_admin_principal_changed")
                matches.append((int(fields[2]), int(fields[3]), fields[0], fields[5]))
        if len(matches) != 1:
            raise error("service_admin_principal_changed")
        identity = matches[0]
        if (
            identity[0] == 0
            or str(identity[0]) != actor["id"]
            or identity[2] != actor["name"]
            or identity[3] != actor["home"]
            or (self._principal is not None and self._principal != identity)
        ):
            raise error("service_admin_principal_changed")
        self._principal = identity

    @property
    def owner_uid(self) -> int:
        return int(self.plan["request"]["principal"]["id"])

    @property
    def owner_gid(self) -> int:
        self.authorize()
        assert self._principal is not None
        return int(self._principal[1])

    @property
    def endpoint(self) -> Path:
        return Path(socket_path(self.approval))

    def select(self, key: str) -> None:
        operation = cast(dict[str, Any], bound_operation(self.approval, key))
        self.operation_key, self.operation = key, operation
        self.plan = operation["deployment"]
        self.manager = manager_identity(self.plan)
        self.authorize()

    @property
    def audit(self) -> Path:
        return Path(AUDIT_DIRECTORY) / self.operation["id"]

    def initialize(self) -> None:
        if sys.platform != "linux":
            raise error("service_admin_root_required")
        self.authorize()
        for path, mode in ((Path(SOCKET_DIRECTORY), 0o711), (Path(AUDIT_DIRECTORY), 0o700)):
            root_path(path, missing=True)
            if not path.exists():
                path.mkdir(mode=mode)
                sync(path.parent)
            root_path(path)
            if not path.is_dir() or (mode == 0o700 and path.stat().st_mode & 0o077):
                raise error()
        for key in scoped_operations(self.approval):
            self.select(key)
            if not self.audit.exists():
                self.audit.mkdir(mode=0o700)
                sync(self.audit.parent)
            root_path(self.audit)
            if self.audit.stat().st_mode & 0o077:
                raise error()
        import fcntl

        lock_path = Path(SOCKET_DIRECTORY) / ("instance." + self.plan["request"]["instance_id"] + ".lock")
        root_path(lock_path, missing=True)
        descriptor = os.open(lock_path, os.O_RDWR | os.O_CREAT | os.O_NOFOLLOW | os.O_CLOEXEC, 0o600)
        try:
            info = os.fstat(descriptor)
            if not stat.S_ISREG(info.st_mode) or info.st_nlink != 1 or info.st_uid != 0 or info.st_mode & 0o077:
                raise error()
            fcntl.flock(descriptor, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BaseException:
            os.close(descriptor)
            raise
        self._lock = descriptor

    def program(self) -> str:
        if sys.platform != "linux":
            raise error("service_admin_root_required")
        self.authorize()
        candidate = Path("/usr/bin/systemctl")
        root_path(candidate)
        info = candidate.stat()
        if not stat.S_ISREG(info.st_mode) or info.st_mode & 0o6000:
            raise error("service_admin_manager_unsafe")
        try:
            capability = os.getxattr(candidate, "security.capability")
        except OSError as exc:
            import errno

            if exc.errno != errno.ENODATA:
                raise error("service_admin_manager_unsafe") from None
        else:
            if capability:
                raise error("service_admin_manager_unsafe")
        return str(candidate)

    def definition(self) -> tuple[bool, bytes | None]:
        self.authorize()
        path = Path(str(self.manager["installed_path"]))
        root_path(path, missing=True)
        return (True, root_read(path)) if path.exists() else (False, None)

    def publish_definition(self) -> None:
        self.authorize()
        publish(
            Path(str(self.manager["installed_path"])),
            render(self.plan)[str(self.manager["artifact"])],
            0o644,
        )

    def reject_dropins(self, output: bytes) -> None:
        self.authorize()
        if len(output) > 16384:
            raise error("service_admin_unit_paths_unverified")
        paths = output.decode("utf-8", errors="strict").strip().split()
        if not paths or len(paths) > 32 or "/etc/systemd/system" not in paths or len(set(paths)) != len(paths):
            raise error("service_admin_unit_paths_unverified")
        stem = str(self.manager["name"]).removesuffix(".service")
        names = ["service.d", stem + ".service.d"]
        names.extend(stem[: index + 1] + ".service.d" for index, char in enumerate(stem) if char == "-")
        for text in paths:
            directory = Path(text)
            if not directory.is_absolute() or ".." in directory.parts or any(char in text for char in "\\\x00\n\r"):
                raise error("service_admin_unit_paths_unverified")
            # Unit search directories may use root-owned usr-merge links. Their
            # canonical ancestors must still all be protected root directories.
            for candidate in (*reversed(directory.parents), directory):
                if candidate.is_symlink() and candidate.lstat().st_uid != 0:
                    raise error()
            resolved = directory.resolve(strict=False)
            existing = resolved
            while not existing.exists():
                existing = existing.parent
            root_path(existing)
            for name in names:
                target = directory / name
                if target.exists() or target.is_symlink():
                    raise error("service_admin_dropins_present")

    def remove_definition(self) -> None:
        self.authorize()
        path = Path(str(self.manager["installed_path"]))
        if root_read(path) != render(self.plan)[str(self.manager["artifact"])]:
            raise error("service_definition_mismatch")
        path.unlink()
        sync(path.parent)

    def dispatched(self) -> bool:
        self.authorize()
        path = self.audit / "dispatch.json"
        root_path(path, missing=True)
        if not path.exists():
            return False
        record = json.loads(root_read(path, 16384))
        if (
            not isinstance(record, dict)
            or cast(dict[str, object], record).get("operation_sha256") != self.operation["sha256"]
        ):
            raise error("service_admin_audit_changed")
        return True

    def record(self, phase: str, result: dict[str, Any]) -> None:
        publish(self.audit / (phase + ".json"), self.audit_bytes(phase, result))

    def audit_bytes(self, phase: str, result: dict[str, Any]) -> bytes:
        self.authorize()
        if phase not in {"dispatch", "outcome"}:
            raise error()
        value = {
            "format": "omni-service-admin-audit-4",
            "approval_id": self.approval["id"],
            "approval_sha256": self.approval["sha256"],
            "administrator_role": self.approval.get("role"),
            "exchange_sha256": cast(dict[str, object], self.approval.get("exchange", {})).get("sha256"),
            "operation_key": self.operation_key,
            "operation_id": self.operation["id"],
            "operation_sha256": self.operation["sha256"],
            "instance_id": self.plan["request"]["instance_id"],
            "owner_uid": self.owner_uid,
            "root_uid": 0,
            "broker_sha256": self.broker_sha256,
            "action": self.operation["action"],
            "boot_id": self.operation.get("boot_id"),
            "phase": phase,
            "recorded_at": dt.datetime.now(dt.timezone.utc).isoformat(),
            "result": result,
        }
        data = json.dumps(value, sort_keys=True, ensure_ascii=False, allow_nan=False).encode("utf-8")
        if len(data) > 16384:
            raise error("service_admin_audit_limit")
        return data

    def close(self) -> None:
        if self._lock is not None:
            descriptor, self._lock = self._lock, None
            os.close(descriptor)
