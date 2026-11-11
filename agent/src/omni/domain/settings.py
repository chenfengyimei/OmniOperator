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
    validate_settings(cast(dict[str, object], value))
    return cast(AgentSettings, value)


def validate_settings(value: dict[str, object]) -> None:
    if set(value) != KEYS:
        raise BusinessError(
            "invalid_settings", "Settings must match the complete Agent configuration schema", status=422
        )
    for name, limit in (("persona", 50000), ("workspace", 4000)):
        text = value[name]
        if not isinstance(text, str) or len(text) > limit or "\0" in text:
            raise BusinessError("invalid_settings", f"Invalid {name}", status=422)
    for name in ("daily_budget_micro", "call_reservation_micro", "max_steps"):
        number = cast(int, value[name])
        if type(value[name]) is not int or not 0 <= number <= 1_000_000_000_000:
            raise BusinessError("invalid_settings", f"Invalid {name}", status=422)
    steps = cast(int, value["max_steps"])
    if not 1 <= steps <= 32 or value["call_reservation_micro"] == 0:
        raise BusinessError("invalid_settings", "Positive reservation and 1 to 32 steps are required", status=422)
    try:
        uuid.UUID(str(value["model_profile_id"]))
    except (ValueError, TypeError, AttributeError) as exc:
        raise BusinessError("invalid_settings", "Select a model configuration UUID", status=422) from exc
    tools = value["disabled_tools"]
    if not isinstance(tools, list):
        raise BusinessError("invalid_settings", "Invalid disabled tool list", status=422)
    items = cast(list[object], tools)
    if (
        len(items) > 200
        or len(set(str(x) for x in items)) != len(items)
        or not all(isinstance(n, str) and re.fullmatch(r"[A-Za-z0-9_-]{1,100}", n) for n in items)
    ):
        raise BusinessError("invalid_settings", "Invalid disabled tool list", status=422)


def settings_value(value: object) -> Settings:
    if not isinstance(value, dict):
        raise BusinessError("invalid_runtime_configuration", "Run configuration is unresolved or invalid")
    record = cast(dict[str, object], value)
    if set(record) != KEYS | MODEL_KEYS | {"model_revision_id", "model_permission_epoch"}:
        raise BusinessError("invalid_runtime_configuration", "Run configuration is unresolved or invalid")
    validate_settings({k: record[k] for k in KEYS})
    config_value("model", {k: record[k] for k in MODEL_KEYS})
    epoch = cast(int, record["model_permission_epoch"])
    if type(record["model_permission_epoch"]) is not int or epoch < 1:
        raise BusinessError("invalid_runtime_configuration", "Invalid model authorization version")
    return cast(Settings, record)
