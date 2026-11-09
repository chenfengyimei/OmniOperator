"""Inspect loaded launchd descriptions via the fixed Swift helper; never parses launchctl print as authority."""

from __future__ import annotations

import secrets
from collections.abc import Awaitable, Callable
from pathlib import Path

from ...application.ports import Record
from ...domain.contracts import BusinessError
from ...domain.service_deployment import manager_identity
from .deployment_files import _decode
from .macos_service_state import disabled_override as disabled_override
from .macos_service_state import loaded_definition

VERSION = "omni-macos-service-query-2"
MAX_OUTPUT = 65536
NATIVE_ERRORS = frozenset(
    {
        "service_native_unverified",
        "service_native_unavailable",
        "service_native_changed",
        "service_native_record_limit",
        "service_native_record_invalid",
        "service_native_session_unverified",
        "service_native_path_unverified",
    }
)


class MacOSServiceInspection:
    def __init__(
        self, command: Callable[[list[str]], Awaitable[tuple[int, bytes]]], helper: Callable[[], Awaitable[Path | None]]
    ) -> None:
        self.command, self.helper = command, helper

    async def probe(self, plan: dict) -> Record:
        request, manager = plan["request"], manager_identity(plan)
        if request["target"] not in {"macos-user", "macos-system"}:
            raise BusinessError("service_native_target_invalid", "Inspect an exact macOS service domain")
        result: Record = {
            "loaded": None,
            "loaded_definition_matches": None,
            "state": "unknown",
            "pid": None,
            "error_code": "service_loaded_definition_unverified",
        }
        try:
            executable = await self.helper()
        except BusinessError as error:
            if error.code != "desktop_macos_helper_invalid":
                raise
            result["error_code"] = "service_macos_helper_invalid"
            return result
        if executable is None:
            result["error_code"] = "service_macos_helper_missing"
            return result
        nonce = secrets.token_hex(16)
        code, output = await self.command(
            [
                str(executable),
                "--system-service-query" if request["target"] == "macos-system" else "--service-query",
                manager["name"],
                request["principal"]["id"],
                nonce,
            ]
        )
        if code != 0 or len(output) > MAX_OUTPUT:
            return result
        response = _decode(output)
        if (
            set(response) != {"version", "domain", "label", "uid", "nonce", "complete", "loaded", "job", "error_code"}
            or response["version"] != VERSION
            or response["domain"] != manager["domain"]
            or response["nonce"] != nonce
            or response["label"] != manager["name"]
            or response["uid"] != request["principal"]["id"]
        ):
            raise BusinessError("service_native_identity_invalid", "Native service query identity is invalid")
        if response["complete"] is not True or type(response["loaded"]) is not bool or response["error_code"] != "":
            if isinstance(response["error_code"], str) and response["error_code"] in NATIVE_ERRORS:
                result["error_code"] = response["error_code"]
            return result
        if response["loaded"] is False:
            if response["job"] is not None:
                return result
            return {**result, "loaded": False, "state": "stopped", "error_code": ""}
        matched = loaded_definition(plan, response["job"])
        job = response["job"]
        pid = job.get("PID") if isinstance(job, dict) else None
        if type(pid) is not int or not 0 <= pid <= 2_147_483_647:
            pid = None
        state = (
            "running"
            if pid is not None and pid > 0
            else "stopped"
            if pid == 0 or (isinstance(job, dict) and "PID" not in job)
            else "unknown"
        )
        return {
            **result,
            "loaded": True,
            "loaded_definition_matches": matched,
            "state": state if matched is True else "unknown",
            "pid": pid or None,
            "error_code": "" if matched is True else "service_loaded_definition_unverified",
        }
