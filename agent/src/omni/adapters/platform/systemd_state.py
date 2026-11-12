"""Shared bounded systemd decoding; no command execution or filesystem access."""

from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Any

from ...application.ports import Record

PROPERTIES = (
    "Id",
    "LoadState",
    "ActiveState",
    "SubState",
    "MainPID",
    "FragmentPath",
    "SourcePath",
    "DropInPaths",
    "NeedDaemonReload",
    "Transient",
)


def manager_report(
    *,
    available: bool = True,
    installed: bool | None = None,
    state: str = "unknown",
    matches: bool | None = None,
    pid: int | None = None,
    code: str = "",
    limitations: tuple[str, ...] = (),
) -> dict[str, Any]:
    return {
        "available": available,
        "installed": installed,
        "state": state,
        "definition_matches": matches,
        "pid": pid,
        "error_code": code,
        "limitations": list(limitations),
    }


@dataclass(frozen=True)
class SystemdState:
    report: dict[str, Any]
    definition_path: str | None = None


def systemd_state(output: bytes, identity: Record) -> SystemdState:
    if len(output) > 1_048_576:
        return SystemdState(manager_report(code="service_manager_output_invalid"))
    values: dict[str, str] = {}
    try:
        for line in output.decode("utf-8", errors="strict").splitlines():
            key, separator, value = line.partition("=")
            if not separator or key not in PROPERTIES or key in values:
                return SystemdState(manager_report(code="service_manager_output_invalid"))
            values[key] = value
    except UnicodeError:
        return SystemdState(manager_report(code="service_manager_output_invalid"))
    if set(values) != set(PROPERTIES) or values["Id"] != identity["name"]:
        return SystemdState(manager_report(code="service_manager_identity_unverified"))
    number = values["MainPID"]
    pid = int(number) if re.fullmatch(r"[1-9][0-9]{0,9}", number) and int(number) <= 2_147_483_647 else None
    if number != "0" and pid is None:
        return SystemdState(manager_report(code="service_manager_output_invalid"))
    state = (
        "running"
        if values["ActiveState"] == "active" and values["SubState"] == "running" and pid
        else "stopped"
        if values["ActiveState"] in {"inactive", "failed"} and pid is None
        else "unknown"
    )
    if (
        values["DropInPaths"]
        or values["SourcePath"]
        or values["Transient"] != "no"
        or values["NeedDaemonReload"] != "no"
    ):
        return SystemdState(
            manager_report(
                installed=True if values["LoadState"] == "loaded" else None,
                state=state,
                pid=pid,
                code="service_loaded_definition_unverified",
                limitations=(
                    "Drop-ins, generated/transient units or pending reload prevent exact definition verification.",
                ),
            )
        )
    if values["LoadState"] == "not-found":
        return SystemdState(manager_report(installed=False, state=state, pid=pid))
    if values["LoadState"] != "loaded":
        return SystemdState(manager_report(state=state, pid=pid, code="service_load_state_unverified"))
    if values["FragmentPath"] != identity["installed_path"]:
        return SystemdState(
            manager_report(installed=True, state=state, pid=pid, matches=False, code="service_definition_mismatch")
        )
    return SystemdState(manager_report(installed=True, state=state, pid=pid), values["FragmentPath"])
