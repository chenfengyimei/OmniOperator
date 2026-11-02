"""Pure validation for versioned business configuration; no secret values allowed."""

from __future__ import annotations

import re
from typing import TypedDict, cast
from urllib.parse import urlsplit

from .contracts import BusinessError

KEYS = {
    "persona",
    "model",
    "workspace",
    "daily_budget_micro",
    "call_reservation_micro",
    "max_steps",
    "credential_ref",
    "model_base_url",
    "disabled_tools",
}


class Settings(TypedDict):
    persona: str
    model: str
    workspace: str
    daily_budget_micro: int
    call_reservation_micro: int
    max_steps: int
    credential_ref: str
    model_base_url: str
    disabled_tools: list[str]


def settings_value(value: object) -> Settings:
    if not isinstance(value, dict):
        raise BusinessError("invalid_settings", "Settings must be an object", status=422)
    validate_settings(value)
    return cast(Settings, value)


def validate_settings(value: dict) -> None:
    if set(value) != KEYS:
        raise BusinessError("invalid_settings", "Settings must match the complete V2 configuration schema", status=422)
    for name, limit in (
        ("persona", 50_000),
        ("model", 200),
        ("workspace", 4000),
        ("credential_ref", 200),
        ("model_base_url", 2000),
    ):
        if not isinstance(value[name], str) or len(value[name]) > limit or "\0" in value[name]:
            raise BusinessError("invalid_settings", f"Invalid {name}", status=422)
    for name in ("daily_budget_micro", "call_reservation_micro", "max_steps"):
        if type(value[name]) is not int or value[name] < 0:
            raise BusinessError("invalid_settings", f"Invalid {name}", status=422)
    if not 1 <= value["max_steps"] <= 32:
        raise BusinessError("invalid_settings", "max_steps must be between 1 and 32", status=422)
    if value["call_reservation_micro"] == 0:
        raise BusinessError("invalid_settings", "Model calls require a positive reservation", status=422)
    if value["credential_ref"] and not re.fullmatch(r"[a-zA-Z0-9_.:/-]{1,200}", value["credential_ref"]):
        raise BusinessError("invalid_settings", "Invalid credential reference", status=422)
    if value["model_base_url"]:
        target = urlsplit(value["model_base_url"])
        if (
            target.scheme not in {"https", "http"}
            or not target.hostname
            or target.username
            or target.password
            or target.query
            or target.fragment
        ):
            raise BusinessError("invalid_settings", "Model endpoint must be a plain HTTP(S) URL", status=422)
        if target.scheme == "http" and target.hostname not in {"127.0.0.1", "localhost", "::1"}:
            raise BusinessError("invalid_settings", "Remote model endpoints require HTTPS", status=422)
    tools = value["disabled_tools"]
    if (
        not isinstance(tools, list)
        or len(tools) > 200
        or not all(isinstance(name, str) and re.fullmatch(r"[A-Za-z0-9_-]{1,100}", name) for name in tools)
    ):
        raise BusinessError("invalid_settings", "Invalid disabled tool list", status=422)
