"""Owner-managed Agent names and bounds; no credentials, state copying or platform access."""

from __future__ import annotations

from .contracts import BusinessError

MAX_AGENTS = 64
MAX_NAME = 100
MAX_REVISION = 2**63 - 2


def name_value(value: object) -> str:
    if (
        not isinstance(value, str)
        or not value.strip()
        or len(value) > MAX_NAME
        or any(ord(character) < 32 or 127 <= ord(character) <= 159 for character in value)
    ):
        raise BusinessError("invalid_agent_name", "Use an Agent name with 1 to 100 printable characters", status=422)
    try:
        value.encode("utf-8")
    except UnicodeError:
        raise BusinessError("invalid_agent_name", "Agent name must be valid UTF-8 text", status=422) from None
    return value


def revision_value(value: object) -> int:
    if type(value) is not int or not 1 <= value <= MAX_REVISION:
        raise BusinessError("invalid_agent_revision", "Use the current positive Agent revision", status=422)
    return value
