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

    def __init__(
        self, code: str, message: str, *, retryable: bool = False, status: int = 409, detail: int | None = None
    ) -> None:
        super().__init__(message)
        self.code, self.message, self.retryable, self.status = code, message, retryable, status
        self.detail = detail


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


MAX_IMAGES = 4
MAX_BYTES = 16 * 1024 * 1024


def invalid(code: str = "image_invalid") -> BusinessError:
    return BusinessError(code, "Image, model image policy or source reference is invalid", status=422)


@dataclass(frozen=True)
class ImageInfo:
    media_type: str
    width: int
    height: int
    sha256: str
    bytes: int

    def validate(self) -> None:
        if (
            self.media_type not in {"image/png", "image/jpeg", "image/webp"}
            or type(self.width) is not int
            or type(self.height) is not int
            or type(self.bytes) is not int
            or not 1 <= self.width <= 8192
            or not 1 <= self.height <= 8192
            or self.width * self.height > 16000000
            or not 1 <= self.bytes <= MAX_BYTES
            or not re.fullmatch(r"[0-9a-f]{64}", self.sha256)
        ):
            raise invalid()


@dataclass(frozen=True)
class ImageRef:
    """A verified image object a tool result hands back for same-turn model visibility."""

    object_id: str
    media_type: str
    width: int
    height: int
    sha256: str
    bytes: int

    def __post_init__(self) -> None:
        object_id: object = cast(object, self.object_id)
        try:
            if not isinstance(object_id, str) or str(uuid.UUID(object_id)) != object_id:
                raise invalid()
        except (ValueError, AttributeError):
            raise invalid() from None
        ImageInfo(self.media_type, self.width, self.height, self.sha256, self.bytes).validate()


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
    images: tuple[ImageRef, ...] = ()

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
        images: object = cast(object, self.images)
        if not isinstance(images, tuple):
            raise ValueError("Action images are a bounded tuple of validated references")
        checked = cast(tuple[object, ...], cast(object, self.images))
        if len(checked) > MAX_IMAGES:
            raise ValueError("Action images are a bounded tuple of validated references")
        for image in checked:
            if not isinstance(image, ImageRef):
                raise ValueError("Action images are a bounded tuple of validated references")


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
        binding: object = cast(object, self.binding_id)
        if not isinstance(binding, str) or (binding and str(uuid.UUID(binding)) != binding):
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
    # 供应商上报的提示缓存用量；未上报保持 None，不得补 0。
    cached_prompt_tokens: int | None = None
    cache_creation_tokens: int | None = None

    def __post_init__(self) -> None:
        if self.completion_reason not in {"stop", "tool_calls", "length", "content_filter"}:
            raise ValueError("Invalid model completion reason")
        for value in (
            self.input_tokens,
            self.output_tokens,
            self.cost_micro,
            self.cached_prompt_tokens,
            self.cache_creation_tokens,
        ):
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
