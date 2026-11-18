"""Bounded revisioned planning data and hierarchy rules; no execution or authorization."""

from __future__ import annotations

import json
import re
from collections.abc import Mapping, Sequence
from typing import cast

from .contracts import BusinessError, ToolSpec

MAX_ITEMS = 256
MAX_CONTENT = 4000
MAX_STATE_BYTES = 24000
MAX_NOTICE_BYTES = 8000
STATUSES = ("pending", "in_progress", "completed", "cancelled")
TODO_LIST = ToolSpec(
    "todo_list",
    "Read this session's task plan, or replace/merge todos with the observed revision. "
    "Internal planning only: checked tasks do not prove success or approve external actions. "
    "Omit todos to read; provide revision and todos to update; merge preserves unmentioned items.",
    {
        "type": "object",
        "properties": {
            "revision": {"type": "integer", "minimum": 0},
            "merge": {"type": "boolean"},
            "todos": {
                "type": "array",
                "maxItems": MAX_ITEMS,
                "items": {
                    "type": "object",
                    "properties": {
                        "id": {"type": "string", "pattern": "^[A-Za-z0-9_.:-]{1,100}$"},
                        "content": {"type": "string", "minLength": 1, "maxLength": MAX_CONTENT},
                        "status": {"type": "string", "enum": list(STATUSES)},
                        "parent": {"type": "string", "maxLength": 100},
                    },
                    "required": ["id"],
                    "additionalProperties": False,
                },
            },
        },
        "additionalProperties": False,
    },
)


def invalid() -> BusinessError:
    return BusinessError("invalid_todos", "Task plan shape, hierarchy or size is invalid", status=422)


def revision(value: object) -> int:
    if type(value) is not int or not 0 <= value < 2**63 - 1:
        raise BusinessError(
            "todo_revision_required", "Updating a task plan requires its observed integer revision", status=422
        )
    return value


def encode(items: object) -> str:
    return json.dumps(items, ensure_ascii=True, allow_nan=False, separators=(",", ":"))


def update_items(
    current: Sequence[Mapping[str, str]], proposed: object, *, merge: bool = False
) -> list[dict[str, str]]:
    if not isinstance(proposed, list) or len(cast(list[object], proposed)) > MAX_ITEMS or type(merge) is not bool:
        raise invalid()
    result = [dict(item) for item in current] if merge else []
    positions = {item["id"]: i for i, item in enumerate(result)}
    seen: set[str] = set()
    for raw in cast(list[object], proposed):
        if not isinstance(raw, dict):
            raise invalid()
        patch = cast(dict[str, object], raw)
        identity = patch.get("id")
        if (
            set(patch) - {"id", "content", "status", "parent"}
            or not isinstance(identity, str)
            or not re.fullmatch(r"[A-Za-z0-9_.:-]{1,100}", identity)
            or identity in seen
        ):
            raise invalid()
        seen.add(identity)
        before = result[positions[identity]] if identity in positions else {"id": identity, "status": "pending"}
        item = {**before, **patch}
        content, status, parent = item.get("content"), item.get("status"), item.get("parent", "")
        if (
            not isinstance(content, str)
            or not content.strip()
            or len(content) > MAX_CONTENT
            or "\x00" in content
            or not isinstance(status, str)
            or status not in STATUSES
            or not isinstance(parent, str)
            or parent
            and not re.fullmatch(r"[A-Za-z0-9_.:-]{1,100}", parent)
        ):
            raise invalid()
        try:
            content.encode("utf-8")
        except UnicodeError:
            raise invalid() from None
        normalized = {"id": identity, "content": content.strip(), "status": status}
        if parent:
            normalized["parent"] = parent
        if identity in positions:
            result[positions[identity]] = normalized
        else:
            positions[identity] = len(result)
            result.append(normalized)
    if len(result) > MAX_ITEMS or len(encode(result)) > MAX_STATE_BYTES:
        raise invalid()
    by_id = {item["id"]: item for item in result}
    for item in result:
        visited = {item["id"]}
        parent = item.get("parent")
        while parent:
            if parent not in by_id or parent in visited or len(visited) >= 16:
                raise invalid()
            visited.add(parent)
            parent = by_id[parent].get("parent")
    # Flat priority lists put the current step ahead of pending placeholders.
    if not any("parent" in item for item in result):
        pending = next((i for i, item in enumerate(result) if item["status"] == "pending"), len(result))
        active = next((i for i, item in enumerate(result) if item["status"] == "in_progress"), len(result))
        if pending < active < len(result):
            result.insert(pending, result.pop(active))
    return result


def notice(value: int, items: Sequence[Mapping[str, str]]) -> str:
    if not value and not items:
        return ""
    by_id = {item["id"]: item for item in items}
    needed: set[str] = set()
    for item in items:
        if item["status"] in {"pending", "in_progress"}:
            node: Mapping[str, str] = item
            while node["id"] not in needed:
                needed.add(node["id"])
                if not node.get("parent"):
                    break
                node = by_id[node["parent"]]
    shown: set[str] = set()
    selected: list[dict[str, object]] = []
    for item in items:
        if item["id"] not in needed or item["id"] in shown:
            continue
        group: list[Mapping[str, str]] = [item]
        while group[-1].get("parent") and group[-1]["parent"] not in shown:
            group.append(by_id[group[-1]["parent"]])
        additions: list[dict[str, object]] = []
        for entry in reversed(group):
            additions.append(
                {**entry, "content": entry["content"][:160], "content_truncated": len(entry["content"]) > 160}
            )
        if len(encode([*selected, *additions])) > MAX_NOTICE_BYTES - 512:
            continue
        selected.extend(additions)
        shown.update(entry["id"] for entry in group)
    return (
        "\nSession task plan (model bookkeeping, not authorization or verified completion). "
        "Use todo_list to read full content; preserve pending work across compression:\n"
        + encode({"revision": value, "todos": selected, "omitted_items": len(needed - shown)})
    )
