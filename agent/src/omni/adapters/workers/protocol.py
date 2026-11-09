"""Strict, bounded IPC records shared by the host and capability worker; no dispatch or I/O."""

from __future__ import annotations

import json
import math
import re
import uuid
from dataclasses import asdict

from ...domain.contracts import ActionResult, Effect, Outcome

VERSION = 2
MAX_FRAME = 1_048_576
RESULT_FIELDS = {"status", "text", "effect", "error_code", "retryable", "artifacts", "verification", "data"}


def identity(value: object) -> bool:
    if not isinstance(value, str):
        return False
    try:
        return str(uuid.UUID(value)) == value
    except ValueError:
        return False


def _pairs(entries):
    result = {}
    for name, value in entries:
        if name in result:
            raise ValueError("Duplicate IPC field")
        result[name] = value
    return result


def _constant(_):
    raise ValueError("Nonfinite IPC number")


def _json(value: object, depth: int = 0, budget: list[int] | None = None) -> None:
    remaining = [10000] if budget is None else budget
    remaining[0] -= 1
    if remaining[0] < 0 or depth > 16:
        raise ValueError("IPC data exceeds structural limits")
    if isinstance(value, dict):
        for key, item in value.items():
            if not isinstance(key, str):
                raise ValueError("IPC keys must be strings")
            _json(item, depth + 1, remaining)
    elif isinstance(value, list):
        for item in value:
            _json(item, depth + 1, remaining)
    elif isinstance(value, float) and not math.isfinite(value):
        raise ValueError("Nonfinite IPC number")
    elif value is not None and not isinstance(value, (str, int, float, bool)):
        raise ValueError("Unsupported IPC value")


def decode(frame: bytes) -> dict:
    if len(frame) > MAX_FRAME or not frame.endswith(b"\n"):
        raise ValueError("Incomplete or oversized IPC frame")
    try:
        data = json.loads(frame, object_pairs_hook=_pairs, parse_constant=_constant)
        _json(data)
    except (UnicodeError, RecursionError) as exc:
        raise ValueError("Malformed IPC JSON") from exc
    if not isinstance(data, dict) or type(data.get("version")) is not int or data["version"] != VERSION:
        raise ValueError("IPC version mismatch")
    if not identity(data.get("id")):
        raise ValueError("IPC identity is invalid")
    return data


def request(frame: bytes) -> dict:
    data = decode(frame)
    if set(data) != {"version", "id", "method", "writes", "arguments", "context"}:
        raise ValueError("IPC request fields do not match")
    if not isinstance(data["method"], str) or not re.fullmatch(r"[A-Za-z0-9_-]{1,100}", data["method"]):
        raise ValueError("Invalid IPC capability")
    if type(data["writes"]) is not bool or not isinstance(data["arguments"], dict):
        raise ValueError("Invalid IPC capability metadata")
    context = data["context"]
    if (
        not isinstance(context, dict)
        or set(context) != {"agent_id", "run_id", "session_id", "policy_revision", "workspace"}
        or not all(identity(context[name]) for name in ("agent_id", "run_id", "session_id"))
        or type(context["policy_revision"]) is not int
        or context["policy_revision"] <= 0
        or not isinstance(context["workspace"], str)
        or len(context["workspace"]) > 4000
    ):
        raise ValueError("Invalid IPC invocation context")
    return data


def action(value: object, *, writes: bool) -> ActionResult:
    if not isinstance(value, dict) or set(value) != RESULT_FIELDS:
        raise ValueError("IPC result fields do not match")
    if (
        not all(isinstance(value[name], str) for name in ("status", "effect", "text", "error_code", "verification"))
        or not re.fullmatch(r"[a-zA-Z0-9_.-]{0,100}", value["error_code"])
        or type(value["retryable"]) is not bool
        or not isinstance(value["artifacts"], list)
        or len(value["artifacts"]) > 100
        or not all(identity(item) for item in value["artifacts"])
        or not isinstance(value["data"], dict)
    ):
        raise ValueError("Invalid IPC result types")
    _json(value)
    result = ActionResult(
        Outcome(value["status"]),
        value["text"],
        Effect(value["effect"]),
        error_code=value["error_code"],
        retryable=value["retryable"],
        artifacts=tuple(value["artifacts"]),
        verification=value["verification"],
        data=value["data"],
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
    def encode(current):
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
        verification=result.verification
        if isinstance(result.verification, str) and len(result.verification) <= 4096
        else "",
        data={"truncated": True},
    )
    return encode(compact)
