"""Typed execution contracts and state transitions; performs no I/O."""

from __future__ import annotations

import math
import re
import uuid
from dataclasses import dataclass, field
from enum import StrEnum
from typing import TypeAlias, cast

JSON: TypeAlias = None | bool | int | float | str | list["JSON"] | dict[str, "JSON"]


def _empty_json() -> dict[str, JSON]:
    return {}


class BusinessError(Exception):
    """A public, non-secret failure. Original exceptions stay inside adapters."""

    def __init__(self, code: str, message: str, *, retryable: bool = False, status: int = 409) -> None:
        super().__init__(message)
        self.code, self.message, self.retryable, self.status = code, message, retryable, status


class RunState(StrEnum):
    QUEUED = "queued"
    RUNNING = "running"
    WAITING_APPROVAL = "waiting_approval"
    CANCELLING = "cancelling"
    SUCCEEDED = "succeeded"
    FAILED = "failed"
    CANCELLED = "cancelled"
    INTERRUPTED = "interrupted"


TERMINAL_RUNS = frozenset({RunState.SUCCEEDED, RunState.FAILED, RunState.CANCELLED, RunState.INTERRUPTED})
RUN_TRANSITIONS = {
    RunState.QUEUED: frozenset({RunState.RUNNING, RunState.CANCELLED, RunState.INTERRUPTED}),
    RunState.RUNNING: frozenset({RunState.WAITING_APPROVAL, RunState.CANCELLING, *TERMINAL_RUNS}),
    RunState.WAITING_APPROVAL: frozenset(
        {RunState.RUNNING, RunState.CANCELLING, RunState.CANCELLED, RunState.INTERRUPTED}
    ),
    RunState.CANCELLING: frozenset({RunState.CANCELLED, RunState.INTERRUPTED}),
}


def check_transition(before: str, after: RunState) -> None:
    if after not in RUN_TRANSITIONS.get(RunState(before), frozenset()):
        raise BusinessError("invalid_transition", f"Cannot transition {before} to {after}")


class Outcome(StrEnum):
    OK = "ok"
    ERROR = "error"
    DENIED = "denied"
    CANCELLED = "cancelled"
    UNKNOWN = "unknown"


class Effect(StrEnum):
    NONE = "none"
    APPLIED = "applied"
    UNKNOWN = "unknown"


@dataclass(frozen=True)
class ActionResult:
    status: Outcome
    text: str
    effect: Effect = Effect.NONE
    error_code: str = ""
    retryable: bool = False
    artifacts: tuple[str, ...] = ()
    verification: str = ""
    data: dict[str, JSON] = field(default_factory=_empty_json)

    def __post_init__(self) -> None:
        status: object = cast(object, self.status)
        effect: object = cast(object, self.effect)
        if not isinstance(status, Outcome) or not isinstance(effect, Effect):
            raise TypeError("Structured outcomes and effects are required")
        if self.effect != Effect.NONE and self.retryable:
            raise ValueError("Applied or unknown effects cannot be automatically retried")
        if self.effect == Effect.UNKNOWN and (self.status != Outcome.UNKNOWN or self.retryable):
            raise ValueError("Unknown effects require unknown outcome and prohibit automatic retry")
        if self.status == Outcome.UNKNOWN and self.effect != Effect.UNKNOWN:
            raise ValueError("Unknown outcome must preserve unknown effects")


@dataclass(frozen=True)
class ToolSpec:
    name: str
    description: str
    parameters: dict[str, JSON]
    writes: bool = False
    timeout_s: float = 60
    resource: str = ""
    binding_id: str = ""

    def __post_init__(self) -> None:
        if not re.fullmatch(r"[A-Za-z0-9_-]{1,100}", self.name):
            raise ValueError("Invalid capability name")
        if not math.isfinite(self.timeout_s) or self.timeout_s <= 0:
            raise ValueError("Capability timeout must be finite and positive")
        if self.binding_id and str(uuid.UUID(self.binding_id)) != self.binding_id:
            raise ValueError("Capability binding requires a canonical UUID")


@dataclass(frozen=True)
class Invocation:
    id: str
    name: str
    arguments: dict[str, JSON]


@dataclass(frozen=True)
class InvocationContext:
    agent_id: str
    run_id: str
    session_id: str
    policy_revision: int
    workspace: str = ""


@dataclass(frozen=True)
class ModelReply:
    text: str
    calls: tuple[Invocation, ...] = ()
    input_tokens: int | None = None
    output_tokens: int | None = None
    cost_micro: int | None = None
    completion_reason: str = "stop"

    def __post_init__(self) -> None:
        if self.completion_reason not in {"stop", "tool_calls", "length", "content_filter"}:
            raise ValueError("Invalid model completion reason")
        for value in (self.input_tokens, self.output_tokens, self.cost_micro):
            if value is not None and (type(value) is not int or value < 0):
                raise ValueError("Model usage and cost must be nonnegative integers or unknown")


@dataclass(frozen=True)
class CapabilityStatus:
    name: str
    supported: bool
    installed: bool
    state: str
    permission: str = "not_required"
    reason: str = ""
    recovery: str = ""
    limitations: tuple[str, ...] = ()


@dataclass(frozen=True)
class TurnReceipt:
    run_id: str
    session_id: str
    duplicate: bool = False
