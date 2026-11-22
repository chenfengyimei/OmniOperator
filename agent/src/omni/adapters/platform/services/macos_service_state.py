"""Pure bounded launchd definition and override comparison shared by owner and administrator ports."""

from __future__ import annotations

import json
import plistlib
import re
from collections.abc import Mapping
from typing import cast

from omni.adapters.platform.services.service_templates import render
from omni.domain.service_deployment import manager_identity

MAX_OUTPUT = 65536
# launchd returns a normalized subset for a running job; these plist keys are
# covered by the on-disk definition comparison and may be omitted at runtime.
OMITTED_BY_LAUNCHD = frozenset(
    {"EnvironmentVariables", "ExitTimeOut", "KeepAlive", "RunAtLoad", "Umask", "WorkingDirectory"}
)
REQUIRED_LOADED = ("Label", "ProgramArguments", "StandardOutPath", "StandardErrorPath", "LimitLoadToSessionType")


def _bounded_job(value: object) -> bool:
    pending: list[tuple[object, int]] = [(value, 0)]
    count, text = 0, 0
    while pending:
        item, depth = pending.pop()
        count += 1
        if depth > 8 or count > 1024:
            return False
        if isinstance(item, str):
            size = len(item.encode("utf-8"))
            text += size
            if size > 4096 or text > 32768:
                return False
        elif isinstance(item, dict):
            mapping = cast(dict[object, object], item)
            if len(mapping) > 64 or any(not isinstance(key, str) for key in mapping):
                return False
            pending.extend((child, depth + 1) for pair in mapping.items() for child in pair)
        elif isinstance(item, list):
            items = cast(list[object], item)
            if len(items) > 64:
                return False
            pending.extend((child, depth + 1) for child in items)
        elif type(item) not in {type(None), bool, int, float}:
            return False
    return True


def loaded_definition(plan: Mapping[str, object], job: object) -> bool | None:
    """Compare all execution-relevant fields; unknown native fields cannot prove a match."""
    if not isinstance(job, dict) or not _bounded_job(cast(object, job)):
        return None
    job_value = cast(dict[str, object], job)
    artifact = str(manager_identity(plan)["artifact"])
    expected = cast(dict[str, object], plistlib.loads(render(plan)[artifact]))
    metadata = {"PID", "LastExitStatus", "OnDemand", "Program"}
    if set(job_value) - set(expected) - metadata:
        return None
    absent = set(expected) - set(job_value)
    if absent - OMITTED_BY_LAUNCHD:
        return None
    if any(key in expected and key not in job_value for key in REQUIRED_LOADED):
        return None
    # Compare every field launchd reports, including omit-nothing when present;
    # JSON's typed spelling distinguishes booleans from integers.
    actual = {key: job_value[key] for key in expected if key in job_value}
    wanted = {key: expected[key] for key in actual}
    if json.dumps(actual, sort_keys=True, allow_nan=False) != json.dumps(wanted, sort_keys=True, allow_nan=False):
        return False
    if "Program" in job_value and job_value["Program"] != cast(list[object], expected["ProgramArguments"])[0]:
        return False
    if "OnDemand" in job_value and job_value["OnDemand"] is not True:
        return False
    for key in ("PID", "LastExitStatus"):
        if key in job_value:
            number = job_value[key]
            if type(number) is not int or not -2_147_483_648 <= number <= 2_147_483_647:
                return None
    if "PID" in job_value:
        pid = job_value["PID"]
        if type(pid) is int and pid < 0:
            return None
    return True


def disabled_override(output: bytes, label: str) -> bool | None:
    """Accept only the entire known print-disabled grammar, never grep a label or error text."""
    try:
        text = output.decode("utf-8", errors="strict")
        lines = text.strip().splitlines()
        if (
            len(output) > MAX_OUTPUT
            or len(lines) < 2
            or lines[0].strip() != "disabled services = {"
            or lines[-1].strip() != "}"
        ):
            return None
        values: dict[str, bool] = {}
        for line in lines[1:-1]:
            match = re.fullmatch(r'\s*("(?:[^"\\\x00-\x1f]|\\["\\])+")\s*=>\s*(true|false|enabled|disabled)\s*', line)
            if match is None:
                return None
            name = json.loads(match[1])
            if name in values or len(name) > 256 or len(values) >= 4096:
                return None
            # macOS 27 prints the override state as enabled/disabled instead of
            # the older boolean spelling; both mean the same two states.
            values[name] = match[2] in {"true", "disabled"}
        # No override means the plist's own Disabled=false/default applies.
        return values.get(label, False)
    except (ValueError, UnicodeError):
        return None
