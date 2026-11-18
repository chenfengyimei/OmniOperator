"""Bounded chat stream assembly. Partial text and tool arguments never become executable results."""

from __future__ import annotations

import json
import re
from collections.abc import Mapping
from typing import cast

from .contracts import JSON, BusinessError, Invocation, ModelReply

MAX_TEXT_BYTES = 1024 * 1024
MAX_ARGUMENT_BYTES = 262144
MAX_CHUNKS = 32768
MAX_FRAME_BYTES = 262144
MAX_STREAM_BYTES = 8 * 1024 * 1024
FINISH_REASONS = {"stop", "tool_calls", "length", "content_filter"}


def invalid(code: str = "invalid_model_stream") -> BusinessError:
    return BusinessError(code, "Model stream is incomplete, malformed or exceeds its declared limits", status=502)


def text(value: object) -> str:
    if not isinstance(value, str):
        raise invalid()
    try:
        value.encode("utf-8")
    except UnicodeError:
        raise invalid() from None
    return value


def integer(value: object) -> int:
    if type(value) is not int or not 0 <= value <= 2**63 - 1:
        raise invalid()
    return value


def _mapping(value: object) -> Mapping[str, object] | None:
    return cast(Mapping[str, object], value) if isinstance(value, dict) else None


class StreamAssembly:
    def __init__(self) -> None:
        self.fragments: list[str] = []
        self.calls: dict[int, dict[str, str]] = {}
        self.bytes = self.argument_bytes = self.chunks = self.wire_bytes = 0
        self.finish_reason = ""
        self.model = ""
        self.usage: dict[str, object] | None = None
        self.refused = False

    def feed(self, payload: object) -> str:
        self.chunks += 1
        data = _mapping(payload)
        if self.chunks > MAX_CHUNKS or data is None:
            raise invalid()
        try:
            size = len(json.dumps(data, ensure_ascii=False, allow_nan=False).encode("utf-8"))
        except (ValueError, TypeError, UnicodeError):
            raise invalid() from None
        self.wire_bytes += size
        if size > MAX_FRAME_BYTES or self.wire_bytes > MAX_STREAM_BYTES:
            raise invalid("model_stream_too_large")
        model = data.get("model")
        if model:
            label = text(model)
            if len(label) > 300 or self.model and label != self.model:
                raise invalid()
            self.model = label
        usage_value = data.get("usage")
        if usage_value is not None:
            usage = _mapping(usage_value)
            if usage is None or self.usage is not None:
                raise invalid()
            for name in ("prompt_tokens", "completion_tokens"):
                integer(usage.get(name))
            self.usage = dict(usage)
        choices_value = data.get("choices")
        if not isinstance(choices_value, list):
            raise invalid()
        choices = cast(list[object], choices_value)
        if len(choices) > 1:
            raise invalid()
        if not choices:
            if not self.finish_reason or usage_value is None:
                raise invalid()
            return ""
        choice = _mapping(choices[0])
        if choice is None or type(choice.get("index")) is not int or choice["index"] != 0 or self.finish_reason:
            raise invalid()
        delta = _mapping(choice.get("delta"))
        if delta is None or delta.get("role") not in (None, "assistant") or delta.get("function_call"):
            raise invalid()
        if delta.get("refusal") is not None:
            self.refused = bool(text(delta["refusal"])) or self.refused
        value = text(delta["content"]) if delta.get("content") is not None else ""
        self.bytes += len(value.encode())
        if self.bytes > MAX_TEXT_BYTES:
            raise invalid("model_stream_too_large")
        if value:
            self.fragments.append(value)
        calls_value = delta.get("tool_calls")
        if calls_value is None:
            calls: list[object] = []
        elif isinstance(calls_value, list):
            calls = cast(list[object], calls_value)
        else:
            raise invalid()
        if len(calls) > 32:
            raise invalid()
        for item_value in calls:
            item = _mapping(item_value)
            if item is None or item.get("type") not in (None, "function"):
                raise invalid()
            index = integer(item.get("index"))
            if index >= 32:
                raise invalid()
            call = self.calls.setdefault(index, {"id": "", "name": "", "arguments": ""})
            identifier_value = item.get("id")
            if identifier_value:
                identifier = text(identifier_value)
                if not re.fullmatch(r"[A-Za-z0-9_.:-]{1,200}", identifier) or call["id"] and identifier != call["id"]:
                    raise invalid()
                call["id"] = identifier
            function = _mapping(item.get("function") or {})
            if function is None:
                raise invalid()
            name = text(function["name"]) if function.get("name") is not None else ""
            arguments = text(function["arguments"]) if function.get("arguments") is not None else ""
            call["name"] += name
            if len(call["name"]) > 100:
                raise invalid()
            self.argument_bytes += len(arguments.encode())
            if self.argument_bytes > MAX_ARGUMENT_BYTES:
                raise invalid("model_stream_too_large")
            call["arguments"] += arguments
        finish = choice.get("finish_reason")
        if finish is not None:
            if not isinstance(finish, str) or finish not in FINISH_REASONS:
                raise invalid()
            self.finish_reason = finish
        if usage_value is not None and not self.finish_reason:
            raise invalid()
        return value

    def result(self, cost_micro: int | None = None) -> ModelReply:
        if (
            not self.finish_reason
            or self.calls
            and self.finish_reason == "stop"
            or self.finish_reason == "tool_calls"
            and not self.calls
        ):
            raise invalid("model_stream_incomplete")
        invocations: list[Invocation] = []
        if self.finish_reason == "tool_calls":

            def pairs(entries: list[tuple[str, object]]) -> dict[str, object]:
                result: dict[str, object] = {}
                for key, value in entries:
                    if key in result:
                        raise invalid()
                    result[key] = value
                return result

            for index, call in sorted(self.calls.items()):
                if (
                    index != len(invocations)
                    or not call["id"]
                    or not re.fullmatch(r"[A-Za-z0-9_-]{1,100}", call["name"])
                ):
                    raise invalid()
                try:
                    decoded = json.loads(
                        call["arguments"],
                        object_pairs_hook=pairs,
                        parse_constant=lambda _: (_ for _ in ()).throw(invalid()),
                    )
                except (ValueError, TypeError):
                    raise invalid("invalid_tool_arguments") from None
                if not isinstance(decoded, dict) or call["id"] in {item.id for item in invocations}:
                    raise invalid("invalid_tool_arguments")
                invocations.append(Invocation(call["id"], call["name"], cast(dict[str, JSON], decoded)))
        usage = self.usage or {}
        details = _mapping(usage.get("prompt_tokens_details")) or {}
        cached = usage.get("prompt_cache_hit_tokens")
        if cached is None:
            cached = details.get("cached_tokens")
        if cached is None:
            cached = usage.get("cache_read_input_tokens")
        created = usage.get("cache_creation_input_tokens")
        if created is None:
            created = details.get("cache_creation_tokens")
        return ModelReply(
            "".join(self.fragments),
            tuple(invocations),
            integer(self.usage["prompt_tokens"]) if self.usage else None,
            integer(self.usage["completion_tokens"]) if self.usage else None,
            cost_micro,
            completion_reason="content_filter" if self.refused else self.finish_reason,
            cached_prompt_tokens=cached if type(cached) is int and cached >= 0 else None,
            cache_creation_tokens=created if type(created) is int and created >= 0 else None,
        )
