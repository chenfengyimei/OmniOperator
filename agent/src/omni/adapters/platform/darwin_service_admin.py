"""Darwin registration and single-start root backend; fixed launchctl operations, no GUI, database or force."""

from __future__ import annotations

import secrets
from typing import Any, cast

from ...domain.contracts import BusinessError
from ...domain.service_admin import decode
from ...domain.service_control import start_precondition
from ...domain.service_operations import registration_goal, registration_precondition
from .darwin_admin_commands import DarwinCommands
from .darwin_admin_policy import DarwinPolicy
from .macos_service_state import disabled_override, loaded_definition
from .service_admin import unknown
from .service_admin_policy import error
from .service_templates import render


class DarwinAdministrator:
    def __init__(self, policy: DarwinPolicy, commands: DarwinCommands) -> None:
        self.policy, self.commands = policy, commands

    def snapshot(self) -> tuple[dict[str, Any], bool | None]:
        program, manager = self.policy.program(), self.policy.manager
        name, artifact = str(manager["name"]), str(manager["artifact"])
        nonce = secrets.token_hex(16)
        code, raw = self.commands.run(self.policy.probe_argv(nonce))
        if code != 0 or len(raw) > 65536:
            return unknown("service_admin_native_unverified"), None
        native = decode(raw)
        if (
            set(native) != {"format", "label", "nonce", "complete", "loaded", "job"}
            or native["format"] != "omni-macos-root-query-1"
            or native["label"] != name
            or native["nonce"] != nonce
            or native["complete"] is not True
            or type(native["loaded"]) is not bool
        ):
            return unknown("service_admin_native_unverified"), None
        loaded, job = native["loaded"], native["job"]
        if (not loaded and job is not None) or (loaded and loaded_definition(self.policy.plan, job) is not True):
            return unknown("service_admin_native_unverified"), None
        present, content = self.policy.definition()
        matches = present and content == render(self.policy.plan)[artifact]
        code, raw = self.commands.run([program, "print-disabled", "system"])
        disabled = disabled_override(raw, name) if code == 0 else None
        if disabled is None:
            return unknown("service_admin_enable_unverified"), None
        if loaded and not isinstance(job, dict):
            return unknown("service_admin_native_unverified"), None
        job_row = cast(dict[str, object], job) if isinstance(job, dict) else None
        pid = cast(int, job_row.get("PID", 0)) if job_row is not None else 0
        return {
            "available": True,
            "installed": present or loaded,
            "definition_present": present,
            "definition_matches": matches,
            "enabled": not disabled,
            "state": "running" if pid > 0 else "stopped",
            "pid": pid or None,
            "error_code": "" if matches or not (present or loaded) else "service_definition_mismatch",
        }, loaded

    def inspect(self) -> dict[str, Any]:
        try:
            return self.snapshot()[0]
        except (BusinessError, OSError, ValueError):
            return unknown("service_admin_query_unverified")

    def change(self) -> dict[str, Any]:
        dispatched = False
        try:
            self.policy.authorize()
            if self.policy.dispatched():
                return {"effect": "unknown", "error_code": "service_admin_already_dispatched"}
            before, loaded = self.snapshot()
            action = self.policy.operation["action"]
            accepted = (
                start_precondition(before)
                if action == "start"
                else registration_precondition(action, cast(dict[str, object], before))
            )
            if not accepted or loaded is None:
                return {"effect": "none", "error_code": "service_registration_precondition"}
            program, manager = self.policy.program(), self.policy.manager
            self.policy.record("dispatch", {"effect": "unknown", "acceptance_only": action == "start"})
            dispatched = True
            target = "system/" + str(manager["name"])
            if action == "install":
                self.policy.publish_definition()
                code, _ = self.commands.run([program, "enable", target])
            elif action == "start":
                # RunAtLoad is allowed only after the owner's launch reservation
                # and instance-lock release. Never force/restart a loaded job.
                argv = (
                    [program, "kickstart", target]
                    if loaded
                    else [program, "bootstrap", "system", str(manager["installed_path"])]
                )
                code, _ = self.commands.run(argv)
            else:
                code, _ = self.commands.run([program, "disable", target])
                if code != 0:
                    raise error("service_admin_disable_unverified")
                current, current_loaded = self.snapshot()
                if (
                    not registration_precondition(action, cast(dict[str, object], current))
                    or current["enabled"] is not False
                ):
                    raise error("service_registration_changed")
                if current_loaded:
                    code, _ = self.commands.run([program, "bootout", target])
                    if code != 0:
                        raise error("service_admin_unload_unverified")
                current, current_loaded = self.snapshot()
                if (
                    current_loaded is not False
                    or current["state"] != "stopped"
                    or current["definition_matches"] is not True
                ):
                    raise error("service_admin_unload_unverified")
                self.policy.remove_definition()
                code = 0
            if code != 0:
                raise error("service_admin_command_failed")
            verified = action == "start" or registration_goal(action, self.inspect())
            result = {
                "effect": "applied" if verified else "unknown",
                "error_code": "" if verified else "service_admin_outcome_unverified",
            }
            self.policy.record("outcome", {**result, "acceptance_only": action == "start"})
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

    def close(self, timeout_s: float = 2) -> bool:
        return self.commands.close(timeout_s)
