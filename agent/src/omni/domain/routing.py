"""Deterministic capability tiers and immutable route policy; neither permissions nor model/network calls."""

from __future__ import annotations

import json
import re
import uuid
from collections.abc import Sequence
from typing import cast

from .contracts import JSON, BusinessError, ToolSpec
from .metering import wire_tools

MODES = ("chat", "retrieval", "agent")
REQUESTED = (*MODES, "auto", "default")
CHAT_TOOLS = frozenset({"memory_search"})
RETRIEVAL_TOOLS = CHAT_TOOLS | {
    "document_search",
    "document_read",
    "session_search",
    "system_info",
    "files_list",
    "files_read",
    "files_search",
    "browser_read",
    "bilibili_read",
    "skills_list",
    "skill_view",
    "skill_activate",
    "skill_history",
}
POLICY_DEFAULT = {
    "default_mode": "auto",
    "allow_escalation": True,
    **{f"{mode}_model_id": None for mode in MODES},
    **{f"{mode}_reservation_micro": None for mode in MODES},
}
KERNEL_DEFAULT: dict[str, JSON] = {
    "fallback_model_ids": [],
    "tool_discovery": False,
    "safe_resume": False,
    "empty_recovery": False,
    "verify_completion": False,
    "background_learning": True,
}
DISCOVER_TOOLS = ToolSpec(
    "discover_tools",
    "Find currently permitted tools by name or description. Matching tools with room in the context become available on the next call.",
    {
        "type": "object",
        "properties": {"query": {"type": "string", "minLength": 1, "maxLength": 200}},
        "required": ["query"],
        "additionalProperties": False,
    },
)
_WINDOW_CORE = frozenset({"files_read", "files_list", "files_search", "todo_list", "clarify", "request_mode"})
REQUEST_MODE = ToolSpec(
    "request_mode",
    "Request one upward capability tier for the next model call. Keeps this run's captured model, budget and permissions; never approves actions. Use when more tools are needed.",
    {
        "type": "object",
        "properties": {"mode": {"type": "string", "enum": ["retrieval", "agent"]}},
        "required": ["mode"],
        "additionalProperties": False,
    },
)


def invalid(code: str = "invalid_routing") -> BusinessError:
    return BusinessError(code, "Routing mode, policy or transition is invalid", status=422)


def requested(value: object) -> str:
    if value is None:
        return "default"
    if not isinstance(value, str) or value not in REQUESTED:
        raise invalid()
    return value


def kernel_policy(value: object) -> dict[str, JSON]:
    if not isinstance(value, dict) or value.keys() - KERNEL_DEFAULT.keys():
        raise invalid()
    checked = {**KERNEL_DEFAULT, **cast(dict[str, JSON], value)}
    ids = checked["fallback_model_ids"]
    if not isinstance(ids, list) or len(ids) > 3 or not all(isinstance(i, str) for i in ids):
        raise invalid()
    model_ids = cast(list[str], ids)
    try:
        ordered = [str(uuid.UUID(identity)) for identity in model_ids]
    except ValueError:
        raise invalid() from None
    if ordered != model_ids or len(set(ordered)) != len(ordered):
        raise invalid()
    if any(type(checked[key]) is not bool for key in KERNEL_DEFAULT if key != "fallback_model_ids"):
        raise invalid()
    return checked


def policy(value: object) -> dict[str, object]:
    if not isinstance(value, dict):
        raise invalid()
    record = cast(dict[str, object], value)
    if (
        set(record) - {"kernel"} != set(POLICY_DEFAULT)
        or not isinstance(record["default_mode"], str)
        or record["default_mode"] not in (*MODES, "auto")
        or type(record["allow_escalation"]) is not bool
    ):
        raise invalid()
    for mode in MODES:
        identity = record[f"{mode}_model_id"]
        amount = record[f"{mode}_reservation_micro"]
        if identity is not None:
            try:
                if not isinstance(identity, str) or str(uuid.UUID(identity)) != identity:
                    raise invalid()
            except ValueError:
                raise invalid() from None
        number = cast(int, amount)
        if amount is not None and (type(amount) is not int or not 1 <= number <= 10**12):
            raise invalid()
    return {**record, "kernel": kernel_policy(record.get("kernel", {}))}


def choose(text: str, mode: str, default: str, *, images: bool, remote: bool) -> dict[str, str]:
    mode = requested(mode)
    if remote:
        return {"mode": "chat", "source": "channel", "reason": "restricted_channel"}
    effective = default if mode == "default" else mode
    if effective != "auto":
        return {
            "mode": effective,
            "source": "default" if mode == "default" else "manual",
            "reason": "policy_default" if mode == "default" else "explicit_mode",
        }
    if images:
        return {"mode": "agent", "source": "rule", "reason": "image_input"}
    normalized = text.strip().casefold().strip("!！。.?？ ")
    if normalized in {"你好", "您好", "早上好", "晚上好", "谢谢", "hi", "hello", "thanks", "thank you"}:
        return {"mode": "chat", "source": "rule", "reason": "greeting"}
    if (
        len(text) <= 1000
        and re.match(r"^(搜索|检索|查找|查询|search\b|look up\b)", normalized)
        and not re.search(
            r"删除|发送|发布|写入|修改|执行|运行|安装|重启|打开|\b(delete|send|publish|write|execute|run|install|restart)\b",
            normalized,
        )
    ):
        return {"mode": "retrieval", "source": "rule", "reason": "read_request"}
    return {"mode": "agent", "source": "fallback", "reason": "conservative_full"}


def select_tools(mode: str, specs: Sequence[ToolSpec], *, escalate: bool) -> tuple[ToolSpec, ...]:
    if mode not in MODES or any(s.name == "request_mode" for s in specs):
        raise invalid("routing_tool_collision")
    selected = (
        tuple(specs)
        if mode == "agent"
        else tuple(s for s in specs if not s.writes and s.name in (CHAT_TOOLS if mode == "chat" else RETRIEVAL_TOOLS))
    )
    return (*selected, REQUEST_MODE) if mode != "agent" and escalate and len(selected) < len(specs) else selected


def tool_window(specs: tuple[ToolSpec, ...], context_tokens: int, exposed: Sequence[str] = ()) -> tuple[ToolSpec, ...]:
    def size(items: Sequence[ToolSpec]) -> int:
        return len(json.dumps(wire_tools(items), ensure_ascii=False, separators=(",", ":")).encode("utf-8"))

    ceiling = max(512, min(8000, context_tokens // 4))
    if size(specs) <= ceiling:
        return specs
    by_name = {spec.name: spec for spec in specs}
    window = [DISCOVER_TOOLS]
    if REQUEST_MODE.name in by_name:
        window.append(REQUEST_MODE)
    for name in dict.fromkeys((*exposed[:4], *(spec.name for spec in specs if spec.name in _WINDOW_CORE))):
        if name in by_name and name != REQUEST_MODE.name and size([*window, by_name[name]]) <= ceiling:
            window.append(by_name[name])
    return tuple(window)


def discover(specs: tuple[ToolSpec, ...], query: str, context_tokens: int) -> tuple[ToolSpec, ...]:
    words = set(re.findall(r"[\w]+", query.casefold()))
    if not words:
        return ()
    scored: list[tuple[int, str, ToolSpec]] = []
    for spec in specs:
        name = spec.name.casefold()
        keywords = set(re.findall(r"[^\W_]+", (name + " " + spec.description).casefold()))
        score = (
            100 if name == query.strip().casefold() else 5 * len(words & set(name.split("_"))) + len(words & keywords)
        )
        if score and spec.name != REQUEST_MODE.name:
            scored.append((-score, spec.name, spec))
    ranked = sorted(scored, key=lambda match: (match[0], match[1]))[:4]
    chosen = [name for _, name, _ in ranked]
    offered = {spec.name for spec in tool_window(specs, context_tokens, chosen)}
    return tuple(spec for _, name, spec in ranked if name in offered)


def notice(mode: str, escalate: bool) -> str:
    return f"\nCapability tier: {mode}. Tier filtering never grants permission. " + (
        "If the supplied tools cannot complete the request, request_mode may enlarge the next call's toolset once. The model remains fixed; actions still require authorization."
        if mode != "agent" and escalate
        else "No further tier enlargement is available in this run."
    )
