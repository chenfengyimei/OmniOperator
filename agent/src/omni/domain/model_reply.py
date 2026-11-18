"""One tool-reply contract for all model transports; validation never repairs or dispatches calls."""

from __future__ import annotations

import json
import re
from typing import cast

from .contracts import BusinessError, Invocation, ModelReply
from .model_stream import MAX_ARGUMENT_BYTES, MAX_TEXT_BYTES


def validate_reply(reply: ModelReply) -> None:
    text = cast(object, reply.text)
    calls = cast(object, reply.calls)
    if not isinstance(text, str):
        raise _invalid()
    try:
        if len(text.encode("utf-8")) > MAX_TEXT_BYTES:
            raise _invalid()
    except UnicodeError:
        raise _invalid() from None
    if not isinstance(calls, tuple):
        raise _invalid()
    entries = cast(tuple[object, ...], calls)
    if len(entries) > 32:
        raise _invalid()
    if not entries and not text.strip():
        raise BusinessError(
            "model_empty_reply", "The model returned no answer or tool calls; no retry was sent", status=502
        )
    seen: set[str] = set()
    total = 0
    for value in entries:
        if not isinstance(value, Invocation):
            raise _invalid()
        identifier = cast(object, value.id)
        name = cast(object, value.name)
        arguments = cast(object, value.arguments)
        if (
            not isinstance(identifier, str)
            or not re.fullmatch(r"[A-Za-z0-9_.:-]{1,200}", identifier)
            or identifier in seen
            or not isinstance(name, str)
            or not re.fullmatch(r"[A-Za-z0-9_-]{1,100}", name)
            or not isinstance(arguments, dict)
        ):
            raise _invalid()
        seen.add(identifier)
        try:
            encoded = json.dumps(arguments, ensure_ascii=False, allow_nan=False, separators=(",", ":"))
            total += len(encoded.encode("utf-8"))
            if total > MAX_ARGUMENT_BYTES or json.loads(encoded) != arguments:
                raise _invalid()
        except (TypeError, ValueError, UnicodeError, RecursionError):
            raise _invalid() from None


def _invalid() -> BusinessError:
    return BusinessError(
        "invalid_model_tool_calls", "The model tool-call batch is malformed; no action was dispatched", status=502
    )
