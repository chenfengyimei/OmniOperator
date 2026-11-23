"""Native user-service registration with fixed commands and scoped macOS unload; no elevation or data deletion."""

from __future__ import annotations

import ctypes
import hashlib
import os
import stat
import sys
import uuid
from collections.abc import Awaitable, Callable
from pathlib import Path
from typing import cast

from omni.adapters.object_store import sync_directory
from omni.adapters.platform.services.deployment_files import decode as _decode
from omni.adapters.platform.services.deployment_files import no_links as _no_links
from omni.adapters.platform.services.deployment_files import read_file as _read
from omni.adapters.platform.services.macos_service import MacOSServiceInspection, disabled_override
from omni.adapters.platform.services.service_query import ServiceQuery, action_pid
from omni.adapters.platform.services.service_templates import render
from omni.application.ports import Record
from omni.domain.contracts import BusinessError
from omni.domain.service_control import start_precondition
from omni.domain.service_deployment import manager_identity, validate_plan

Payload = dict[str, object]

SUPPORTED = {"windows-user": "win32", "linux-user": "linux", "macos-user": "darwin"}


def _unknown(code: str, *, available: bool = True) -> Record:
    return {
        "available": available,
        "installed": None,
        "definition_present": None,
        "definition_matches": None,
        "enabled": None,
        "state": "unknown",
        "error_code": code,
    }


def _owner_can_register(info: os.stat_result, uid: int) -> bool:
    return info.st_uid == uid and not info.st_mode & 0o022


class ServiceRegistration(ServiceQuery):
    """Reuse the owned, bounded manager-process transport for one offline command."""

    def __init__(self) -> None:
        super().__init__()
        self._changing = False

    def _supported(self, plan: Record) -> bool:
        target = cast(str, cast(Record, plan["request"])["target"])
        if SUPPORTED.get(target) != sys.platform:
            return False
        if target in {"linux-user", "macos-user"}:
            return str(getattr(os, "geteuid", lambda: None)()) == cast(
                str, cast(Record, cast(Record, plan["request"])["principal"])["id"]
            )
        return True  # The Windows helper rechecks the current token SID independently.

    async def inspect(self, plan: Record) -> Record:
        checked = cast(Record, validate_plan(plan))
        if not self._supported(checked):
            return _unknown("service_registration_target_unimplemented", available=False)
        if cast(str, cast(Record, checked["request"])["target"]) == "windows-user":
            return await self._windows(checked, "inspect")
        try:
            if cast(str, cast(Record, checked["request"])["target"]) == "macos-user":
                return await self._inspect_macos(checked)
            return await self._inspect_linux(checked)
        except (OSError, ValueError, BusinessError, UnicodeError):
            return _unknown("service_registration_unverified")

    async def start(self, plan: Record) -> Record:
        checked = cast(Record, validate_plan(plan))
        if not self._supported(checked) or self._changing:
            return {"effect": "none", "error_code": "service_start_target_unavailable"}
        self._changing = True
        try:
            before = await self.inspect(checked)
            if not start_precondition(before):
                return {"effect": "none", "error_code": "service_start_precondition"}
            target = cast(str, cast(Record, checked["request"])["target"])
            if target == "windows-user":
                return await self._windows(checked, "start")
            program = self._program(target)
            if program is None:
                return {"effect": "none", "error_code": "service_manager_missing"}
            manager = cast(dict[str, str], manager_identity(checked))
            if target == "linux-user":
                argv = [program, "--user", "start", manager["name"], "--no-block", "--no-ask-password"]
            elif before.get("loaded") is False:
                argv = [program, "bootstrap", manager["domain"], manager["installed_path"]]
            elif before.get("loaded") is True and before.get("loaded_definition_matches") is True:
                argv = [program, "kickstart", manager["domain"] + "/" + manager["name"]]
            else:
                return {"effect": "none", "error_code": "service_loaded_definition_unverified"}
            try:
                code, _ = await self._command(argv)
                return {
                    "effect": "applied" if code == 0 else "unknown",
                    "error_code": "" if code == 0 else "service_start_unverified",
                }
            except (BusinessError, OSError, ValueError):
                return {"effect": "unknown", "error_code": "service_start_interrupted"}
        finally:
            self._changing = False

    async def _inspect_macos(self, plan: Record) -> Record:
        manager = cast(dict[str, str], manager_identity(plan))
        native = await MacOSServiceInspection(self._command, self._native_helper).probe(plan)
        path = Path(manager["installed_path"])
        self._check_unit_path(path, plan)
        present = path.exists()
        same_file = present and await self._file(path) == render(cast(Payload, plan))[manager["artifact"]]
        program = self._program("macos-user")
        if program is None:
            return _unknown("service_manager_missing", available=False)
        code, output = await self._command([program, "print-disabled", manager["domain"]])
        disabled = disabled_override(output, manager["name"]) if code == 0 else None
        loaded = native["loaded"]
        matched = (
            same_file
            if loaded is False
            else (same_file and native["loaded_definition_matches"] is True)
            if loaded is True
            else None
        )
        return {
            "available": loaded is not None,
            "installed": True if present or loaded is True else False if loaded is False else None,
            "definition_present": present,
            "definition_matches": matched,
            "enabled": None if disabled is None else not disabled,
            "state": native["state"],
            "pid": native.get("pid"),
            "loaded": loaded,
            "loaded_definition_matches": native["loaded_definition_matches"],
            "activation": "next_login" if present and loaded is False else "loaded" if loaded is True else "unknown",
            "error_code": native["error_code"]
            if loaded is None
            else "service_enable_state_unverified"
            if disabled is None
            else ""
            if matched or not present
            else "service_definition_mismatch",
        }

    async def _inspect_linux(self, plan: Record) -> Record:
        from omni.adapters.platform.services.service_query import PROPERTIES

        manager = cast(dict[str, str], manager_identity(plan))
        program = self._program("linux-user")
        if program is None:
            return _unknown("service_manager_missing", available=False)
        code, output = await self._command(
            [program, "--user", "show", manager["name"], "--no-pager", "--property=" + ",".join(PROPERTIES)]
        )
        # systemctl can exit nonzero for a missing unit, but only a full,
        # identity-bound structured not-found report proves absence.
        report = await self._linux(output, manager, render(cast(Payload, plan))[manager["artifact"]])
        path = Path(manager["installed_path"])
        _no_links(path)
        present = path.exists()
        if present:
            self._check_unit_path(path, plan)
        report.update({"definition_present": present, "enabled": None})
        if code and report.get("installed") is not False:
            return _unknown("service_manager_query_failed")
        if report.get("installed") is True and report.get("definition_matches") is True:
            enabled_code, enabled = await self._command(
                [program, "--user", "is-enabled", manager["name"], "--no-pager"]
            )
            if enabled == b"enabled\n" and enabled_code == 0:
                report["enabled"] = True
            elif enabled in {b"disabled\n", b"static\n"} and enabled_code != 0:
                report["enabled"] = False
            else:
                report["error_code"] = "service_enable_state_unverified"
        return report

    @staticmethod
    def _check_unit_path(path: Path, plan: Record) -> None:
        _no_links(path)
        home = Path(cast(str, cast(Record, cast(Record, plan["request"])["principal"])["home"]))
        if path != Path(cast(str, manager_identity(plan)["installed_path"])) or not path.is_relative_to(home):
            raise BusinessError(
                "service_install_path_unsafe", "Service file is outside the fixed owner registration directory"
            )
        uid = int(cast(str, cast(Record, cast(Record, plan["request"])["principal"])["id"]))
        for candidate in (path, *path.parents):
            if candidate.exists():
                info = candidate.stat(follow_symlinks=False)
                if candidate == path and (not stat.S_ISREG(info.st_mode) or info.st_nlink != 1):
                    raise BusinessError("service_install_path_unsafe", "Service definition is not one regular file")
                if not _owner_can_register(info, uid):
                    raise BusinessError(
                        "service_install_path_unsafe", "Service registration path is writable by another principal"
                    )
            if candidate == home:
                break

    def _publish_unit(self, plan: Record) -> None:
        manager = cast(dict[str, str], manager_identity(plan))
        path = Path(manager["installed_path"])
        self._check_unit_path(path, plan)
        home = Path(cast(str, cast(Record, cast(Record, plan["request"])["principal"])["home"]))
        missing: list[Path] = []
        parent = path.parent
        while not parent.exists() and parent != home:
            missing.append(parent)
            parent = parent.parent
        for directory in reversed(missing):
            directory.mkdir(mode=0o700)
            sync_directory(directory.parent)
        self._check_unit_path(path, plan)
        data = render(cast(Payload, plan))[manager["artifact"]]
        temporary = path.with_name("." + uuid.uuid4().hex + ".omni-partial")
        descriptor = os.open(temporary, os.O_WRONLY | os.O_CREAT | os.O_EXCL | getattr(os, "O_NOFOLLOW", 0), 0o600)
        try:
            with os.fdopen(descriptor, "wb") as stream:
                stream.write(data)
                stream.flush()
                os.fsync(stream.fileno())
            self._check_unit_path(path, plan)
            # Same-directory hard-link publication refuses an existing destination.
            os.link(temporary, path, follow_symlinks=False)
            temporary.unlink()
            sync_directory(path.parent)
        finally:
            temporary.unlink(missing_ok=True)

    def _remove_unit(self, plan: Record) -> None:
        manager = cast(dict[str, str], manager_identity(plan))
        path = Path(manager["installed_path"])
        self._check_unit_path(path, plan)
        expected = render(cast(Payload, plan))[manager["artifact"]]
        if _read(path, 65536, single=True) != expected:
            raise BusinessError(
                "service_definition_mismatch", "Changed or foreign service definitions cannot be removed"
            )
        self._check_unit_path(path, plan)
        path.unlink()
        sync_directory(path.parent)

    async def change(self, plan: Record, action: str) -> Record:
        checked = cast(Record, validate_plan(plan))
        if action not in {"install", "uninstall"} or not self._supported(checked):
            return {"effect": "none", "error_code": "service_registration_target_unimplemented"}
        if self._changing or self._closing:
            return {"effect": "none", "error_code": "service_registration_busy"}
        self._changing = True
        try:
            if cast(str, cast(Record, checked["request"])["target"]) == "windows-user":
                return await self._windows(checked, action)
            if cast(str, cast(Record, checked["request"])["target"]) == "macos-user":
                return await self._change_macos(checked, action)
            return await self._change_linux(checked, action)
        finally:
            self._changing = False

    async def _change_macos(self, plan: Record, action: str) -> Record:
        from omni.domain.service_operations import registration_precondition

        program = self._program("macos-user")
        if program is None:
            return {"effect": "none", "error_code": "service_manager_missing"}
        before = await self.inspect(plan)
        if not registration_precondition(action, cast(Payload, before)):
            return {"effect": "none", "error_code": "service_registration_changed"}
        manager = cast(dict[str, str], manager_identity(plan))
        target = manager["domain"] + "/" + manager["name"]
        try:
            if action == "install":
                self._publish_unit(plan)
                code, _ = await self._command([program, "enable", target])
                return {
                    "effect": "applied" if code == 0 else "unknown",
                    "error_code": "" if code == 0 else "service_enable_unverified",
                }
            code, _ = await self._command([program, "disable", target])
            if code != 0:
                return {"effect": "unknown", "error_code": "service_disable_unverified"}
            current = await self.inspect(plan)
            if not registration_precondition(action, cast(Payload, current)) or current.get("enabled") is not False:
                return {"effect": "unknown", "error_code": "service_registration_changed"}
            if current.get("loaded") is True:
                if current.get("loaded_definition_matches") is not True:
                    return {"effect": "unknown", "error_code": "service_loaded_definition_unverified"}
                code, _ = await self._command([program, "bootout", target])
                if code != 0:
                    return {"effect": "unknown", "error_code": "service_unload_unverified"}
            native = await MacOSServiceInspection(self._command, self._native_helper).probe(plan)
            if native["loaded"] is not False:
                return {"effect": "unknown", "error_code": "service_unload_unverified"}
            self._remove_unit(plan)
            return {"effect": "applied", "error_code": ""}
        except (BusinessError, OSError, ValueError):
            return {"effect": "unknown", "error_code": "service_registration_interrupted"}

    async def _change_linux(self, plan: Record, action: str) -> Record:
        program = self._program("linux-user")
        if program is None:
            return {"effect": "none", "error_code": "service_manager_missing"}
        manager = cast(dict[str, str], manager_identity(plan))
        # Recheck after the durable dispatch record; never write over a foreign unit.
        from omni.domain.service_operations import registration_precondition

        if not registration_precondition(action, cast(Payload, await self.inspect(plan))):
            return {"effect": "none", "error_code": "service_registration_changed"}
        try:
            if action == "install":
                self._publish_unit(plan)
                commands = [
                    [program, "--user", "daemon-reload"],
                    [program, "--user", "enable", manager["name"], "--no-ask-password"],
                ]
            else:
                code, _ = await self._command([program, "--user", "disable", manager["name"], "--no-ask-password"])
                if code != 0:
                    return {"effect": "unknown", "error_code": "service_disable_unverified"}
                self._remove_unit(plan)
                commands = [[program, "--user", "daemon-reload"]]
            for argv in commands:
                code, _ = await self._command(argv)
                if code != 0:
                    return {"effect": "unknown", "error_code": "service_registration_command_failed"}
            return {"effect": "applied", "error_code": ""}
        except (BusinessError, OSError, ValueError):
            return {"effect": "unknown", "error_code": "service_registration_interrupted"}

    async def _windows(self, plan: Record, action: str) -> Record:
        return await windows_receipt(self._command, plan, action)


async def windows_receipt(
    command: Callable[[list[str]], Awaitable[tuple[int, bytes]]], plan: Record, action: str
) -> Record:
    """One bounded Task Scheduler COM receipt; shared by registration and read-only status."""
    if sys.platform != "win32":
        return _unknown("service_registration_target_unimplemented", available=False)
    directory = ctypes.create_unicode_buffer(32768)
    try:
        kernel = ctypes.WinDLL("kernel32", use_last_error=True)
        size = kernel.GetSystemDirectoryW(directory, len(directory))
        if not 0 < size < len(directory):
            raise OSError("System executable unavailable")
        program = Path(directory.value) / "WindowsPowerShell/v1.0/powershell.exe"
        script = Path(__file__).resolve().parents[2] / "workers/windows_services.ps1"
        _no_links(script)
        _read(script, 65536, single=True)
        manager = cast(dict[str, str], manager_identity(plan))
        definition = Path(cast(str, cast(Record, plan["request"])["bundle_dir"])) / manager["artifact"]
        expected = render(cast(Payload, plan))[manager["artifact"]]
        if _read(definition, 65536, single=True) != expected:
            raise BusinessError("service_definition_mismatch", "Service bundle changed before native dispatch")
        argv = [
            str(program),
            "-NoLogo",
            "-NoProfile",
            "-NonInteractive",
            "-Mta",
            "-File",
            str(script),
            "-Mode",
            action,
            "-TaskName",
            manager["name"],
            "-Definition",
            str(definition),
            "-ExpectedSHA256",
            hashlib.sha256(expected).hexdigest(),
            "-OwnerSID",
            cast(str, cast(Record, cast(Record, plan["request"])["principal"])["id"]),
            "-PlanSHA256",
            cast(str, plan["sha256"]),
        ]
        code, output = await command(argv)
        result = _decode(output)
        if (
            code != 0
            or result.get("format") != "omni-service-native-1"
            or result.get("plan_sha256") != plan["sha256"]
            or result.get("action") != action
        ):
            raise ValueError("Native receipt identity is invalid")
        allowed = {
            "format",
            "plan_sha256",
            "action",
            "available",
            "installed",
            "definition_present",
            "definition_matches",
            "enabled",
            "state",
            "engine_pid",
            "effect",
            "error_code",
        }
        if (
            set(result) != allowed
            or result["state"] not in {"stopped", "running", "unknown"}
            or result["effect"] not in {"none", "applied", "unknown"}
        ):
            raise ValueError("Native receipt fields are invalid")
        if (
            any(
                type(result[key]) not in {bool, type(None)}
                for key in ("installed", "definition_present", "definition_matches", "enabled")
            )
            or type(result["available"]) is not bool
        ):
            raise ValueError("Native receipt flags are invalid")
        receipt = cast(dict[str, object], result)
        engine_pid: int | None = None
        raw_engine = receipt["engine_pid"]
        if raw_engine is not None:
            if not isinstance(raw_engine, int) or isinstance(raw_engine, bool) or not 0 < raw_engine <= 4294967295:
                raise ValueError("Native engine pid is invalid")
            engine_pid = raw_engine
        # R3: start and reconcile must use the same action-PID proof as status,
        # so the receipt carries the verified action process, not just the engine.
        # The worker never supplies this field; an extra key is rejected above.
        verified = action_pid(engine_pid, expected, plan) if receipt["state"] == "running" else None
        if verified is not None and verified <= 0:
            raise ValueError("Native action pid is invalid")
        if (
            not isinstance(result["error_code"], str)
            or len(result["error_code"]) > 100
            or any(not (char.isascii() and (char.islower() or char == "_")) for char in result["error_code"])
        ):
            raise ValueError("Native error code is not metadata")
        return cast(Record, {**receipt, "pid": verified})
    except (BusinessError, OSError, ValueError, UnicodeError, AttributeError):
        if action == "inspect":
            return _unknown("service_registration_unverified")
        return {"effect": "unknown", "error_code": "service_registration_interrupted"}
