"""Bounded chat stream assembly. Partial text and tool arguments never become executable results."""

from __future__ import annotations

import json
import re

from .contracts import BusinessError, Invocation, ModelReply

MAX_TEXT_BYTES = 1024 * 1024
MAX_ARGUMENT_BYTES = 262144
MAX_CHUNKS = 32768
MAX_FRAME_BYTES = 262144
MAX_STREAM_BYTES = 8 * 1024 * 1024


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


class StreamAssembly:
    def __init__(self):
        self.fragments: list[str] = []
        self.calls: dict[int, dict[str, str]] = {}
        self.bytes = self.argument_bytes = self.chunks = self.wire_bytes = 0
        self.finish_reason = ""
        self.model = ""
        self.usage: dict | None = None
        self.refused = False

    def feed(self, payload: dict) -> str:
        self.chunks += 1
        if self.chunks > MAX_CHUNKS or not isinstance(payload, dict):
            raise invalid()
        try:
            size = len(json.dumps(payload, ensure_ascii=False, allow_nan=False).encode("utf-8"))
        except (ValueError, TypeError, UnicodeError):
            raise invalid() from None
        self.wire_bytes += size
        if size > MAX_FRAME_BYTES or self.wire_bytes > MAX_STREAM_BYTES:
            raise invalid("model_stream_too_large")
        model = payload.get("model")
        if model:
            model = text(model)
            if len(model) > 300 or self.model and model != self.model:
                raise invalid()
            self.model = model
        usage = payload.get("usage")
        if usage is not None:
            if not isinstance(usage, dict) or self.usage is not None:
                raise invalid()
            for name in ("prompt_tokens", "completion_tokens"):
                integer(usage.get(name))
            self.usage = dict(usage)
        choices = payload.get("choices")
        if not isinstance(choices, list) or len(choices) > 1:
            raise invalid()
        if not choices:
            if not self.finish_reason or usage is None:
                raise invalid()
            return ""
        choice = choices[0]
        if (
            not isinstance(choice, dict)
            or type(choice.get("index")) is not int
            or choice["index"] != 0
            or self.finish_reason
        ):
            raise invalid()
        delta = choice.get("delta")
        if not isinstance(delta, dict) or delta.get("role") not in (None, "assistant") or delta.get("function_call"):
            raise invalid()
        if delta.get("refusal") is not None:
            self.refused = bool(text(delta["refusal"])) or self.refused
        value = text(delta["content"]) if delta.get("content") is not None else ""
        self.bytes += len(value.encode())
        if self.bytes > MAX_TEXT_BYTES:
            raise invalid("model_stream_too_large")
        if value:
            self.fragments.append(value)
        calls = delta.get("tool_calls") or []
        if not isinstance(calls, list) or len(calls) > 32:
            raise invalid()
        for item in calls:
            if not isinstance(item, dict) or item.get("type") not in (None, "function"):
                raise invalid()
            index = integer(item.get("index"))
            if index >= 32:
                raise invalid()
            call = self.calls.setdefault(index, {"id": "", "name": "", "arguments": ""})
            identifier = item.get("id")
            if identifier:
                identifier = text(identifier)
                if not re.fullmatch(r"[A-Za-z0-9_.:-]{1,200}", identifier) or call["id"] and identifier != call["id"]:
                    raise invalid()
                call["id"] = identifier
            function = item.get("function") or {}
            if not isinstance(function, dict):
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
            if finish not in {"stop", "tool_calls", "length", "content_filter"}:
                raise invalid()
            self.finish_reason = finish
        if usage is not None and not self.finish_reason:
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
        invocations = []
        if self.finish_reason == "tool_calls":

            def pairs(entries):
                result = {}
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
                    arguments = json.loads(
                        call["arguments"],
                        object_pairs_hook=pairs,
                        parse_constant=lambda _: (_ for _ in ()).throw(invalid()),
                    )
                except (ValueError, TypeError):
                    raise invalid("invalid_tool_arguments") from None
                if not isinstance(arguments, dict) or call["id"] in {c.id for c in invocations}:
                    raise invalid("invalid_tool_arguments")
                invocations.append(Invocation(call["id"], call["name"], arguments))
        return ModelReply(
            "".join(self.fragments),
            tuple(invocations),
            self.usage["prompt_tokens"] if self.usage else None,
            self.usage["completion_tokens"] if self.usage else None,
            cost_micro,
            completion_reason="content_filter" if self.refused else self.finish_reason,
        )
