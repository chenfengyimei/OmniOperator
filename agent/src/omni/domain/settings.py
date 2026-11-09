"""Editable Agent settings and immutable resolved run configuration; no I/O."""

from __future__ import annotations

import re
import uuid
from typing import TypedDict, cast

from .configuration import ModelOptions, config_value
from .contracts import BusinessError

KEYS = {
    "persona",
    "workspace",
    "daily_budget_micro",
    "call_reservation_micro",
    "max_steps",
    "disabled_tools",
    "model_profile_id",
}
MODEL_KEYS = frozenset(ModelOptions.model_fields)


class AgentSettings(TypedDict):
    persona: str
    workspace: str
    daily_budget_micro: int
    call_reservation_micro: int
    max_steps: int
    disabled_tools: list[str]
    model_profile_id: str


class Settings(AgentSettings):
    model: str
    base_url: str
    credential_ref: str
    context_tokens: int
    output_tokens: int
    summary_tokens: int
    margin_tokens: int
    counter_mode: str
    counter_ref: str
    model_revision_id: str
    model_permission_epoch: int


def agent_settings_value(value: object) -> AgentSettings:
    if not isinstance(value, dict):
        raise BusinessError("invalid_settings", "Settings must be an object", status=422)
    validate_settings(value)
    return cast(AgentSettings, value)


def validate_settings(value: dict) -> None:
    if set(value) != KEYS:
        raise BusinessError(
            "invalid_settings", "Settings must match the complete Agent configuration schema", status=422
        )
    for name, limit in (("persona", 50000), ("workspace", 4000)):
        if not isinstance(value[name], str) or len(value[name]) > limit or "\0" in value[name]:
            raise BusinessError("invalid_settings", f"Invalid {name}", status=422)
    for name in ("daily_budget_micro", "call_reservation_micro", "max_steps"):
        if type(value[name]) is not int or not 0 <= value[name] <= 1_000_000_000_000:
            raise BusinessError("invalid_settings", f"Invalid {name}", status=422)
    if not 1 <= value["max_steps"] <= 32 or value["call_reservation_micro"] == 0:
        raise BusinessError("invalid_settings", "Positive reservation and 1 to 32 steps are required", status=422)
    try:
        uuid.UUID(value["model_profile_id"])
    except (ValueError, TypeError, AttributeError) as exc:
        raise BusinessError("invalid_settings", "Select a model configuration UUID", status=422) from exc
    tools = value["disabled_tools"]
    if (
        not isinstance(tools, list)
        or len(tools) > 200
        or len(set(str(x) for x in tools)) != len(tools)
        or not all(isinstance(n, str) and re.fullmatch(r"[A-Za-z0-9_-]{1,100}", n) for n in tools)
    ):
        raise BusinessError("invalid_settings", "Invalid disabled tool list", status=422)


def settings_value(value: object) -> Settings:
    if not isinstance(value, dict) or set(value) != KEYS | MODEL_KEYS | {"model_revision_id", "model_permission_epoch"}:
        raise BusinessError("invalid_runtime_configuration", "Run configuration is unresolved or invalid")
    validate_settings({k: value[k] for k in KEYS})
    config_value("model", {k: value[k] for k in MODEL_KEYS})
    if type(value["model_permission_epoch"]) is not int or value["model_permission_epoch"] < 1:
        raise BusinessError("invalid_runtime_configuration", "Invalid model authorization version")
    return cast(Settings, value)
