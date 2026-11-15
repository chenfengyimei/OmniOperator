"""Pure bounded launchd definition and override comparison shared by owner and administrator ports."""

from __future__ import annotations

import json
import plistlib
import re
from collections.abc import Mapping
from typing import cast

from ...domain.service_deployment import manager_identity
from .service_templates import render

MAX_OUTPUT = 65536


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
    if set(expected) - set(job_value):
        return None
    # JSON's typed spelling distinguishes booleans from integers.
    actual = {key: job_value[key] for key in expected}
    if json.dumps(actual, sort_keys=True, allow_nan=False) != json.dumps(expected, sort_keys=True, allow_nan=False):
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
            match = re.fullmatch(r'\s*("(?:[^"\\\x00-\x1f]|\\["\\])+")\s*=>\s*(true|false)\s*', line)
            if match is None:
                return None
            name = json.loads(match[1])
            if name in values or len(name) > 256 or len(values) >= 4096:
                return None
            values[name] = match[2] == "true"
        # No override means the plist's own Disabled=false/default applies.
        return values.get(label, False)
    except (ValueError, UnicodeError):
        return None
