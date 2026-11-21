"""Strict, bounded IPC records shared by the host and capability worker; no dispatch or I/O."""

from __future__ import annotations

import json
import math
import re
import uuid
from dataclasses import asdict
from typing import Any, cast

from ...domain.contracts import MAX_IMAGES, ActionResult, BusinessError, Effect, ImageRef, Outcome

VERSION = 2
MAX_FRAME = 1_048_576
RESULT_FIELDS = {"status", "text", "effect", "error_code", "retryable", "artifacts", "verification", "data"}
# ActionResult.images 是 1b 图像协议加入的字段。版本号未变，因此接受旧 worker 省略该键，
# 同时把新 worker 发出的图像引用按冻结的 ImageRef 校验后透传，绝不静默丢弃。
OPTIONAL_RESULT_FIELDS = {"images"}
IMAGE_FIELDS = {"object_id", "media_type", "width", "height", "sha256", "bytes"}


def identity(value: object) -> bool:
    if not isinstance(value, str):
        return False
    try:
        return str(uuid.UUID(value)) == value
    except ValueError:
        return False


def _pairs(entries: list[tuple[str, Any]]) -> dict[str, Any]:
    result: dict[str, Any] = {}
    for name, value in entries:
        if name in result:
            raise ValueError("Duplicate IPC field")
        result[name] = value
    return result


def _constant(_value: str) -> None:
    raise ValueError("Nonfinite IPC number")


def _json(value: object, depth: int = 0, budget: list[int] | None = None) -> None:
    remaining = [10000] if budget is None else budget
    remaining[0] -= 1
    if remaining[0] < 0 or depth > 16:
        raise ValueError("IPC data exceeds structural limits")
    if isinstance(value, dict):
        for key, item in cast(dict[Any, Any], value).items():
            if not isinstance(key, str):
                raise ValueError("IPC keys must be strings")
            _json(item, depth + 1, remaining)
    elif isinstance(value, list):
        for item in cast(list[Any], value):
            _json(item, depth + 1, remaining)
    elif isinstance(value, float) and not math.isfinite(value):
        raise ValueError("Nonfinite IPC number")
    elif value is not None and not isinstance(value, (str, int, float, bool)):
        raise ValueError("Unsupported IPC value")


def decode(frame: bytes) -> dict[str, Any]:
    if len(frame) > MAX_FRAME or not frame.endswith(b"\n"):
        raise ValueError("Incomplete or oversized IPC frame")
    try:
        data = json.loads(frame, object_pairs_hook=_pairs, parse_constant=_constant)
        _json(data)
    except (UnicodeError, RecursionError) as exc:
        raise ValueError("Malformed IPC JSON") from exc
    if not isinstance(data, dict):
        raise ValueError("IPC version mismatch")
    decoded = cast(dict[str, Any], data)
    if type(decoded.get("version")) is not int or decoded["version"] != VERSION:
        raise ValueError("IPC version mismatch")
    if not identity(decoded.get("id")):
        raise ValueError("IPC identity is invalid")
    return decoded


def request(frame: bytes) -> dict[str, Any]:
    data = decode(frame)
    if set(data) != {"version", "id", "method", "writes", "arguments", "context"}:
        raise ValueError("IPC request fields do not match")
    if not isinstance(data["method"], str) or not re.fullmatch(r"[A-Za-z0-9_-]{1,100}", data["method"]):
        raise ValueError("Invalid IPC capability")
    if type(data["writes"]) is not bool or not isinstance(data["arguments"], dict):
        raise ValueError("Invalid IPC capability metadata")
    raw_context = data["context"]
    if not isinstance(raw_context, dict):
        raise ValueError("Invalid IPC invocation context")
    context = cast(dict[str, Any], raw_context)
    if (
        set(context) != {"agent_id", "run_id", "session_id", "policy_revision", "workspace"}
        or not all(identity(context[name]) for name in ("agent_id", "run_id", "session_id"))
        or type(context["policy_revision"]) is not int
        or context["policy_revision"] <= 0
        or not isinstance(context["workspace"], str)
        or len(context["workspace"]) > 4000
    ):
        raise ValueError("Invalid IPC invocation context")
    return data


def images(value: object) -> tuple[ImageRef, ...]:
    """Validate the additive image wire field; legacy workers may omit it (empty tuple)."""
    if not isinstance(value, list) or len(cast(list[object], value)) > MAX_IMAGES:
        raise ValueError("Invalid IPC image results")
    checked: list[ImageRef] = []
    for item in cast(list[object], value):
        if not isinstance(item, dict):
            raise ValueError("Invalid IPC image results")
        fields = cast(dict[str, Any], item)
        if set(fields) != IMAGE_FIELDS:
            raise ValueError("Invalid IPC image results")
        try:
            checked.append(
                ImageRef(
                    cast(str, fields["object_id"]),
                    cast(str, fields["media_type"]),
                    cast(int, fields["width"]),
                    cast(int, fields["height"]),
                    cast(str, fields["sha256"]),
                    cast(int, fields["bytes"]),
                )
            )
        except (TypeError, ValueError, BusinessError):
            raise ValueError("Invalid IPC image results") from None
    return tuple(checked)


def action(value: object, *, writes: bool) -> ActionResult:
    if not isinstance(value, dict):
        raise ValueError("IPC result fields do not match")
    fields = cast(dict[str, Any], value)
    if not RESULT_FIELDS <= set(fields) <= RESULT_FIELDS | OPTIONAL_RESULT_FIELDS:
        raise ValueError("IPC result fields do not match")
    if (
        not all(isinstance(fields[name], str) for name in ("status", "effect", "text", "error_code", "verification"))
        or not re.fullmatch(r"[a-zA-Z0-9_.-]{0,100}", cast(str, fields["error_code"]))
        or type(fields["retryable"]) is not bool
        or not isinstance(fields["artifacts"], list)
        or len(cast(list[object], fields["artifacts"])) > 100
        or not all(identity(item) for item in cast(list[object], fields["artifacts"]))
        or not isinstance(fields["data"], dict)
    ):
        raise ValueError("Invalid IPC result types")
    _json(fields)
    result = ActionResult(
        Outcome(cast(str, fields["status"])),
        cast(str, fields["text"]),
        Effect(cast(str, fields["effect"])),
        error_code=cast(str, fields["error_code"]),
        retryable=fields["retryable"],
        artifacts=tuple(cast(list[str], fields["artifacts"])),
        verification=cast(str, fields["verification"]),
        data=cast(dict[str, Any], fields["data"]),
        images=images(fields.get("images", [])),
    )
    if not writes and result.effect != Effect.NONE:
        raise ValueError("Read-only capability reported an external effect")
    return result


def response(frame: bytes, operation_id: str, *, writes: bool) -> ActionResult:
    data = decode(frame)
    if set(data) != {"version", "id", "result"} or data["id"] != operation_id:
        raise ValueError("IPC response identity mismatch")
    return action(data["result"], writes=writes)


def encode_response(operation_id: str, result: ActionResult) -> bytes:
    def encode(current: ActionResult) -> bytes:
        return (
            json.dumps(
                {"version": VERSION, "id": operation_id, "result": asdict(current)}, ensure_ascii=False, allow_nan=False
            )
            + "\n"
        ).encode("utf-8")

    try:
        frame = encode(result)
        if len(frame) <= MAX_FRAME:
            return frame
    except (TypeError, ValueError, UnicodeError, RecursionError):
        pass
    # The transport cannot negate a side effect the worker has already observed.
    compact = ActionResult(
        result.status,
        "Result payload omitted at transport limit; original outcome retained",
        result.effect,
        error_code=result.error_code,
        retryable=result.retryable,
        artifacts=result.artifacts
        if len(result.artifacts) <= 100 and all(identity(item) for item in result.artifacts)
        else (),
        verification=result.verification if len(result.verification) <= 4096 else "",
        data={"truncated": True},
    )
    return encode(compact)
