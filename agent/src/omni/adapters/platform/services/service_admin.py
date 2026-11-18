"""Fixed Linux system-service registration/start broker; no owner code execution, business DB, stop or shell."""

from __future__ import annotations

import json
import os
import select
import socket
import stat
import struct
import sys
import time
from typing import Any, Protocol, cast

from omni.domain.contracts import BusinessError
from omni.domain.service_admin import MAX_FRAME, MAX_REQUESTS, decode, encode, response, validate_request
from omni.domain.service_control import start_precondition
from omni.domain.service_operations import registration_goal, registration_precondition
from omni.adapters.platform.services.service_admin_commands import RootCommands
from omni.adapters.platform.services.service_admin_policy import RootServicePolicy, error, root_path, sync
from omni.adapters.platform.services.service_templates import render
from omni.adapters.platform.services.systemd_state import PROPERTIES, systemd_state


def unknown(code: str) -> dict[str, Any]:
    return {
        "available": False,
        "installed": None,
        "definition_present": None,
        "definition_matches": None,
        "enabled": None,
        "state": "unknown",
        "pid": None,
        "error_code": code,
    }


class SystemServiceAdministrator:
    def __init__(self, policy: RootServicePolicy, commands: RootCommands) -> None:
        self.policy, self.commands = policy, commands

    def inspect(self) -> dict[str, Any]:
        try:
            program, manager = self.policy.program(), self.policy.manager
            name = str(manager["name"])
            code, output = self.commands.run(
                [
                    program,
                    "--system",
                    "show",
                    name,
                    "--no-pager",
                    "--property=" + ",".join(PROPERTIES),
                    "--no-ask-password",
                ]
            )
            parsed = systemd_state(output, manager)
            raw = parsed.report
            present, content = self.policy.definition()
            report = {
                key: raw[key] for key in ("available", "installed", "state", "definition_matches", "pid", "error_code")
            }
            report.update(definition_present=present, enabled=None)
            if code != 0 and raw["installed"] is not False:
                return unknown("service_admin_query_unverified")
            if parsed.definition_path is not None:
                matches = present and content == render(self.policy.plan)[str(manager["artifact"])]
                report.update(definition_matches=matches, error_code="" if matches else "service_definition_mismatch")
            enabled_code, enabled = self.commands.run(
                [program, "--system", "is-enabled", name, "--no-pager", "--no-ask-password"]
            )
            if enabled_code == 0 and enabled == b"enabled\n":
                report["enabled"] = True
            elif enabled_code != 0 and enabled in {b"disabled\n", b"not-found\n"}:
                report["enabled"] = False
            else:
                report["error_code"] = "service_admin_enable_unverified"
            return report
        except (BusinessError, OSError, ValueError) as exc:
            return unknown(exc.code if isinstance(exc, BusinessError) else "service_admin_query_unavailable")

    def change(self) -> dict[str, str]:
        if self.policy.operation["action"] == "start":
            return self._start()
        dispatched = False
        try:
            self.policy.authorize()
            if self.policy.dispatched():
                return {"effect": "unknown", "error_code": "service_admin_already_dispatched"}
            before = self.inspect()
            action = cast(str, self.policy.operation["action"])
            if not registration_precondition(action, before) or before["enabled"] is None:
                return {"effect": "none", "error_code": "service_registration_precondition"}
            program = self.policy.program()
            code, paths = self.commands.run(
                [program, "--system", "show", "--property=UnitPath", "--value", "--no-pager", "--no-ask-password"]
            )
            if code != 0:
                raise error("service_admin_unit_paths_unverified")
            self.policy.reject_dropins(paths)
            self.policy.record("dispatch", {"effect": "unknown"})
            dispatched = True
            program, name = self.policy.program(), str(self.policy.manager["name"])
            if action == "install":
                self.policy.publish_definition()
                code, _ = self.commands.run([program, "--system", "daemon-reload", "--no-ask-password"])
                if code != 0 or not registration_precondition("uninstall", self.inspect()):
                    raise error("service_admin_loaded_definition_unverified")
                commands = [[program, "--system", "enable", name, "--no-ask-password"]]
            else:
                code, _ = self.commands.run([program, "--system", "disable", name, "--no-ask-password"])
                if code != 0:
                    raise error("service_admin_disable_unverified")
                current = self.inspect()
                if not registration_precondition(action, current) or current["enabled"] is not False:
                    raise error("service_registration_changed")
                self.policy.remove_definition()
                commands = [[program, "--system", "daemon-reload", "--no-ask-password"]]
            for argv in commands:
                self.policy.authorize()
                code, _ = self.commands.run(argv)
                if code != 0:
                    raise error("service_admin_command_failed")
            observed = self.inspect()
            verified = registration_goal(action, observed) and (action != "uninstall" or observed["enabled"] is False)
            result = {
                "effect": "applied" if verified else "unknown",
                "error_code": "" if verified else "service_admin_outcome_unverified",
            }
            self.policy.record("outcome", {**result, "registration": observed})
            return result
        except (BusinessError, OSError, ValueError) as exc:
            result = {
                "effect": "unknown" if dispatched else "none",
                "error_code": exc.code if isinstance(exc, BusinessError) else "service_admin_operation_interrupted",
            }
            if dispatched:
                try:
                    self.policy.record("outcome", result)
                except (BusinessError, OSError, ValueError):
                    result["error_code"] = "service_admin_audit_unavailable"
            return result

    def _start(self) -> dict[str, str]:
        dispatched = False
        try:
            self.policy.authorize()
            if self.policy.dispatched():
                return {"effect": "unknown", "error_code": "service_admin_already_dispatched"}
            if not start_precondition(self.inspect()):
                return {"effect": "none", "error_code": "service_start_precondition"}
            program = self.policy.program()
            code, paths = self.commands.run(
                [program, "--system", "show", "--property=UnitPath", "--value", "--no-pager", "--no-ask-password"]
            )
            if code != 0:
                raise error("service_admin_unit_paths_unverified")
            self.policy.reject_dropins(paths)
            self.policy.authorize()
            if not start_precondition(self.inspect()):
                return {"effect": "none", "error_code": "service_start_precondition"}
            program = self.policy.program()
            self.policy.record("dispatch", {"effect": "unknown", "acceptance_only": True})
            dispatched = True
            # Submission must not replace conflicting jobs or wait for application readiness.
            # The owner verifies the reserved boot and manager PID through independent ports.
            code, _ = self.commands.run(
                [
                    program,
                    "--system",
                    "start",
                    str(self.policy.manager["name"]),
                    "--no-block",
                    "--job-mode=fail",
                    "--no-ask-password",
                ]
            )
            result = {
                "effect": "applied" if code == 0 else "unknown",
                "error_code": "" if code == 0 else "service_admin_start_unverified",
            }
            self.policy.record("outcome", {**result, "acceptance_only": True})
            return result
        except (BusinessError, OSError, ValueError) as exc:
            result = {
                "effect": "unknown" if dispatched else "none",
                "error_code": exc.code if isinstance(exc, BusinessError) else "service_admin_start_interrupted",
            }
            if dispatched:
                try:
                    self.policy.record("outcome", {**result, "acceptance_only": True})
                except (BusinessError, OSError, ValueError):
                    result["error_code"] = "service_admin_audit_unavailable"
            return result

    def close(self, timeout_s: float = 2) -> bool:
        return self.commands.close(timeout_s)


def peer_identity(connection: socket.socket) -> tuple[int, int, int]:
    if sys.platform == "darwin":
        from omni.adapters.platform.services.darwin_admin_peer import peer_identity as darwin_peer

        return darwin_peer(connection)
    if sys.platform != "linux":
        raise error("service_admin_peer_invalid")
    return struct.unpack("3i", connection.getsockopt(socket.SOL_SOCKET, socket.SO_PEERCRED, struct.calcsize("3i")))


def connected(connection: socket.socket) -> bool:
    if sys.platform != "linux" and sys.platform != "darwin":
        return False
    try:
        connection.recv(1, socket.MSG_PEEK | socket.MSG_DONTWAIT)
        # No pipelining is allowed while a manager command is in flight.
        return False
    except BlockingIOError:
        return True
    except OSError:
        return False


def receive(connection: socket.socket, timeout_s: float) -> bytes:
    result = bytearray()
    deadline = time.monotonic() + timeout_s
    while len(result) <= MAX_FRAME:
        remaining = deadline - time.monotonic()
        if remaining <= 0 or not select.select([connection], [], [], remaining)[0]:
            raise error("service_admin_idle_timeout")
        block = connection.recv(min(4096, MAX_FRAME + 1 - len(result)))
        if not block:
            raise error("service_admin_peer_disconnected")
        result.extend(block)
        if b"\n" in result:
            if not result.endswith(b"\n") or result.count(b"\n") != 1:
                raise error("service_admin_protocol_invalid")
            return bytes(result)
    raise error("service_admin_protocol_invalid")


class Administrator(Protocol):
    @property
    def policy(self) -> RootServicePolicy: ...

    def inspect(self) -> dict[str, Any]: ...
    def change(self) -> dict[str, Any]: ...
    def close(self, timeout_s: float = 2) -> bool: ...


def serve_connection(connection: socket.socket, administrator: Administrator, broker_sha256: str) -> None:
    operation = administrator.policy.approval
    for sequence in range(MAX_REQUESTS):
        value = validate_request(decode(receive(connection, 60)), operation, sequence, broker_sha256)
        command = value["command"]
        if command != "close":
            administrator.policy.select(cast(str, value["operation_key"]))
        if command == "inspect":
            report = administrator.inspect()
        elif command == "change":
            report = administrator.change()
        else:
            # One close frame owns the actual cleanup. Do not consume new frame
            # slots while the owner's bounded waits expire on this same reply.
            while not administrator.close(2):
                if not connected(connection):
                    return
                select.select([], [], [], 0.1)
            if command == "close":
                # Protocol4 acknowledges handover only after actual commands
                # are reclaimed AND the native instance lock is released.
                # No further commands are accepted on this connection.
                administrator.policy.close()
            report = {"settled" if command == "settle" else "closed": True, "cleanup_pending": False}
        connection.settimeout(2)
        connection.sendall(encode(response(value, report)))
        connection.settimeout(None)
        if command == "close" and report["closed"]:
            return


def serve(policy: RootServicePolicy) -> int:
    if sys.platform != "linux" and sys.platform != "darwin":
        raise error("service_admin_root_required")
    check_path, sync_path = root_path, sync
    administrator: Administrator | None = None
    darwin_commands = None
    if sys.platform == "darwin":
        from omni.adapters.platform.services.darwin_admin_files import protected
        from omni.adapters.platform.services.darwin_admin_files import sync as darwin_sync
        from omni.adapters.platform.services.darwin_admin_policy import DarwinPolicy
        from omni.adapters.platform.services.darwin_service_admin import DarwinAdministrator

        if not isinstance(policy, DarwinPolicy):
            raise error("service_admin_target_unsupported")
        check_path, sync_path = protected, darwin_sync
        darwin_commands = policy.commands
        administrator = DarwinAdministrator(policy, policy.commands)
    listener, connection = None, None
    identity = None
    try:
        policy.initialize()
        path = policy.endpoint
        check_path(path.parent)
        if path.exists() or path.is_symlink():
            raise error("service_admin_socket_exists")
        listener = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
        listener.bind(str(path))
        os.chown(path, policy.owner_uid, policy.owner_gid)
        os.chmod(path, 0o600)
        identity = path.lstat()
        sync_path(path.parent)
        listener.listen(1)
        listener.settimeout(60)
        print(
            json.dumps(
                {
                    "state": "awaiting_owner",
                    "socket": str(path),
                    "operation_sha256": policy.approval["sha256"],
                    "broker_sha256": policy.broker_sha256,
                }
            ),
            flush=True,
        )
        for _ in range(8):
            candidate, _ = listener.accept()
            pid, uid, _ = peer_identity(candidate)
            if pid > 0 and uid == policy.owner_uid:
                connection = candidate
                break
            candidate.close()
        if connection is None:
            raise error("service_admin_peer_invalid")
        if administrator is None:
            administrator = SystemServiceAdministrator(policy, RootCommands(lambda: connected(connection)))
        elif darwin_commands is not None:
            darwin_commands.connected = lambda: connected(connection)
        serve_connection(connection, administrator, policy.broker_sha256)
        return 0
    finally:
        if administrator is not None:
            # Keep actual tree ownership and the system lock while cleanup is
            # incomplete. No new commands or sessions are admitted in this state.
            warned = False
            while not administrator.close(2):
                if not warned:
                    print(json.dumps({"state": "cleanup_pending", "operation_id": policy.operation["id"]}), flush=True)
                    warned = True
                select.select([], [], [], 1)
        if connection is not None:
            connection.close()
        if listener is not None:
            listener.close()
        if identity is not None:
            current = policy.endpoint.lstat()
            if (current.st_dev, current.st_ino, current.st_uid) != (
                identity.st_dev,
                identity.st_ino,
                policy.owner_uid,
            ) or not stat.S_ISSOCK(current.st_mode):
                raise error("service_admin_socket_changed")
            policy.endpoint.unlink()
            sync_path(policy.endpoint.parent)
        policy.close()
