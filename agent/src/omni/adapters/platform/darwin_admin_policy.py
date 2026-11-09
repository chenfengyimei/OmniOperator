"""Exact local-account Darwin administrator policy, protected root artifacts and shared audit bytes."""

from __future__ import annotations

import json
import os
import plistlib
import stat
import sys
from pathlib import Path

from ...domain.service_admin import (
    MACOS_AUDIT_DIRECTORY,
    MACOS_SOCKET_DIRECTORY,
    admin_scope,
    bound_operation,
    scoped_operations,
    verify_broker,
)
from ...domain.service_deployment import manager_identity
from .darwin_admin_commands import DarwinCommands
from .darwin_admin_files import protected, publish, read, sync
from .service_admin_policy import RootServicePolicy, error
from .service_templates import render


class DarwinPolicy(RootServicePolicy):
    def __init__(self, operation: dict, broker_sha256: str, commands: DarwinCommands, root: Path) -> None:
        self.approval = admin_scope(operation)
        verify_broker(self.approval, broker_sha256)
        self.operation_key = next(iter(scoped_operations(self.approval)))
        self.operation = bound_operation(self.approval, self.operation_key)
        if self.operation["deployment"]["request"]["target"] != "macos-system":
            raise error("service_admin_target_unsupported")
        self.plan = self.operation["deployment"]
        self.manager, self.broker_sha256 = manager_identity(self.plan), broker_sha256
        self._lock, self._principal = None, None
        self.commands, self.root = commands, root

    def authorize(self) -> None:
        if sys.platform != "darwin" or os.getuid() != 0 or os.geteuid() != 0:
            raise error("service_admin_root_required")
        actor = self.plan["request"]["principal"]
        program = Path("/usr/bin/dscl")
        protected(program)
        code, data = self.commands.run(
            [
                str(program),
                "-plist",
                "/Local/Default",
                "-read",
                "/Users/" + actor["name"],
                "UniqueID",
                "PrimaryGroupID",
                "NFSHomeDirectory",
            ]
        )
        if code != 0 or len(data) > 16384:
            raise error("service_admin_principal_changed")
        result = plistlib.loads(data)
        keys = {"dsAttrTypeStandard:" + key for key in ("UniqueID", "PrimaryGroupID", "NFSHomeDirectory")}
        if (
            not isinstance(result, dict)
            or set(result) != keys
            or any(
                not isinstance(value, list) or len(value) != 1 or not isinstance(value[0], str)
                for value in result.values()
            )
        ):
            raise error("service_admin_principal_changed")
        uid, gid, home = (
            result["dsAttrTypeStandard:" + key][0] for key in ("UniqueID", "PrimaryGroupID", "NFSHomeDirectory")
        )
        if not uid.isascii() or not uid.isdecimal() or not gid.isascii() or not gid.isdecimal():
            raise error("service_admin_principal_changed")
        identity = (int(uid), int(gid), actor["name"], home)
        if (
            not 0 < identity[0] <= 4_294_967_294
            or not 0 <= identity[1] <= 4_294_967_294
            or (uid != actor["id"] or home != actor["home"] or self._principal not in (None, identity))
        ):
            raise error("service_admin_principal_changed")
        self._principal = identity

    @property
    def audit(self) -> Path:
        return Path(MACOS_AUDIT_DIRECTORY) / self.operation["id"]

    def initialize(self) -> None:
        if sys.platform != "darwin":
            raise error("service_admin_root_required")
        self.authorize()
        for path, mode in (
            (Path(MACOS_SOCKET_DIRECTORY), 0o711),
            (Path(MACOS_AUDIT_DIRECTORY), 0o700),
        ):
            protected(path, missing=True)
            if not path.exists():
                path.mkdir(mode=mode)
                sync(path.parent)
            protected(path)
            if not path.is_dir() or (mode == 0o700 and path.stat().st_mode & 0o077):
                raise error()
        # The parent session owns one native key and instance lock; each exact
        # child keeps its own immutable dispatch/outcome evidence. Never reuse
        # the first operation's directory when selecting another child.
        for key in scoped_operations(self.approval):
            self.select(key)
            protected(self.audit, missing=True)
            if not self.audit.exists():
                self.audit.mkdir(mode=0o700)
                sync(self.audit.parent)
            protected(self.audit)
            if not self.audit.is_dir() or self.audit.stat().st_mode & 0o077:
                raise error()
        import fcntl

        path = Path(MACOS_SOCKET_DIRECTORY) / ("instance." + self.plan["request"]["instance_id"] + ".lock")
        protected(path, missing=True)
        descriptor = os.open(path, os.O_RDWR | os.O_CREAT | os.O_NOFOLLOW | os.O_CLOEXEC, 0o600)
        try:
            protected(path)
            info = os.fstat(descriptor)
            if not stat.S_ISREG(info.st_mode) or info.st_nlink != 1 or info.st_mode & 0o077:
                raise error()
            fcntl.flock(descriptor, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BaseException:
            os.close(descriptor)
            raise
        self._lock = descriptor

    def program(self) -> str:
        self.authorize()
        path = Path("/bin/launchctl")
        protected(path)
        if not stat.S_ISREG(path.stat().st_mode) or path.stat().st_mode & 0o6000:
            raise error("service_admin_manager_unsafe")
        return str(path)

    def probe_argv(self, nonce: str) -> list[str]:
        executable = Path(sys.executable).resolve()
        probe = self.root / "service-admin-probe.py"
        protected(executable)
        protected(probe)
        return [str(executable), "-I", "-S", str(probe), self.manager["name"], nonce]

    def definition(self) -> tuple[bool, bytes | None]:
        self.authorize()
        path = Path(self.manager["installed_path"])
        protected(path, missing=True)
        return (True, read(path)) if path.exists() else (False, None)

    def publish_definition(self) -> None:
        self.authorize()
        publish(Path(self.manager["installed_path"]), render(self.plan)[self.manager["artifact"]], 0o644)

    def remove_definition(self) -> None:
        self.authorize()
        path = Path(self.manager["installed_path"])
        if read(path) != render(self.plan)[self.manager["artifact"]]:
            raise error("service_definition_mismatch")
        path.unlink()
        sync(path.parent)

    def dispatched(self) -> bool:
        self.authorize()
        path = self.audit / "dispatch.json"
        protected(path, missing=True)
        if not path.exists():
            return False
        value = json.loads(read(path, 16384))
        if not isinstance(value, dict) or value.get("operation_sha256") != self.operation["sha256"]:
            raise error("service_admin_audit_changed")
        return True

    def record(self, phase: str, result: dict) -> None:
        publish(self.audit / (phase + ".json"), self.audit_bytes(phase, result))
