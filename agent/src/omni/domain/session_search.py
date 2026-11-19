"""Pure rules for model-side history recall: mode inference, bounded shaping and lineage-free dedup.

V2 sessions carry no source/parent columns and messages have no FTS index, so discovery groups
token-matched messages by session instead of walking a compression lineage. Every shape returns
original-text anchors (message id + session id + seq) and bounded content; erasure is honored by the
repository read filters, never reconstructed here.
"""

from __future__ import annotations

import re
import unicodedata
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from typing import cast

from .contracts import JSON, BusinessError

TOOL_NAME = "session_search"
MODES = ("discover", "scroll", "read", "browse")
ROLES = ("user", "assistant", "system")
DISCOVER_ROLES = ("user", "assistant")

DEFAULT_RESULTS = 3
MAX_RESULTS = 10
DEFAULT_WINDOW = 5
MAX_WINDOW = 20
DEFAULT_HEAD = 20
DEFAULT_TAIL = 10
MAX_HEAD_TAIL = 50
MESSAGE_MAX_CONTENT = 2000
DISCOVER_SNIPPET_CHARS = 600
PREVIEW_CHARS = 200
SCAN_LIMIT = 300
MAX_EXCLUDE = 20
MAX_QUERY_CHARS = 500
MAX_TOKENS = 20
MAX_ID_CHARS = 64


@dataclass(frozen=True)
class SessionSearchRequest:
    mode: str
    query: str = ""
    tokens: tuple[str, ...] = ()
    session_id: str = ""
    around_seq: int | None = None
    window: int = DEFAULT_WINDOW
    limit: int = DEFAULT_RESULTS
    head: int = DEFAULT_HEAD
    tail: int = DEFAULT_TAIL
    roles: tuple[str, ...] = DISCOVER_ROLES
    exclude: tuple[str, ...] = ()


def _invalid(message: str) -> BusinessError:
    return BusinessError("invalid_session_query", message, status=422)


def _bounded_text(value: object, max_chars: int, field: str) -> str:
    if value is None:
        return ""
    if not isinstance(value, str):
        raise _invalid(f"{field} must be a string")
    text = value.strip()
    if len(text) > max_chars:
        raise _invalid(f"{field} exceeds {max_chars} characters")
    if any(unicodedata.category(ch) == "Cc" and ch not in "\n\r\t" for ch in text):
        raise _invalid(f"{field} contains control characters")
    return text


def _clamp_int(value: object, default: int, lo: int, hi: int) -> int:
    if value is None:
        return default
    if isinstance(value, bool) or not isinstance(value, int):
        raise _invalid("numeric bounds must be integers")
    return max(lo, min(value, hi))


def query_tokens(query: str) -> tuple[str, ...]:
    return tuple(re.findall(r"[^\W_]+", query, flags=re.UNICODE)[:MAX_TOKENS])


def _roles(value: object) -> tuple[str, ...]:
    if value is None:
        return DISCOVER_ROLES
    items: list[object]
    if isinstance(value, str):
        items = cast(list[object], value.split(","))
    elif isinstance(value, list):
        items = cast(list[object], value)
    else:
        raise _invalid("role_filter must be a comma-separated string or a list")
    out: list[str] = []
    for item in items:
        role = item.strip() if isinstance(item, str) else ""
        if role and role not in ROLES:
            raise _invalid(f"unknown role: {role}")
        if role and role not in out:
            out.append(role)
    return tuple(out) or DISCOVER_ROLES


def _exclude(value: object) -> tuple[str, ...]:
    if value is None:
        return ()
    items: list[object]
    if isinstance(value, str):
        items = [value]
    elif isinstance(value, list):
        items = cast(list[object], value)
    else:
        raise _invalid("exclude_session_ids must be a string or a list")
    out: list[str] = []
    for item in items:
        sid = _bounded_text(item, MAX_ID_CHARS, "exclude_session_ids")
        if sid and sid not in out:
            out.append(sid)
        if len(out) >= MAX_EXCLUDE:
            break
    return tuple(out)


def parse_request(arguments: Mapping[str, JSON]) -> SessionSearchRequest:
    session_id = _bounded_text(arguments.get("session_id"), MAX_ID_CHARS, "session_id")
    query = _bounded_text(arguments.get("query"), MAX_QUERY_CHARS, "query")
    around_raw = arguments.get("around_seq")
    around_seq: int | None = None
    if around_raw is not None:
        if isinstance(around_raw, bool) or not isinstance(around_raw, int) or around_raw < 1:
            raise _invalid("around_seq must be a positive integer")
        around_seq = around_raw
    if session_id:
        mode = "scroll" if around_seq is not None else "read"
    elif query:
        mode = "discover"
    else:
        mode = "browse"
    return SessionSearchRequest(
        mode=mode,
        query=query,
        tokens=query_tokens(query),
        session_id=session_id,
        around_seq=around_seq,
        window=_clamp_int(arguments.get("window"), DEFAULT_WINDOW, 1, MAX_WINDOW),
        limit=_clamp_int(arguments.get("limit"), DEFAULT_RESULTS, 1, MAX_RESULTS),
        head=_clamp_int(arguments.get("head"), DEFAULT_HEAD, 0, MAX_HEAD_TAIL),
        tail=_clamp_int(arguments.get("tail"), DEFAULT_TAIL, 0, MAX_HEAD_TAIL),
        roles=_roles(arguments.get("role_filter")),
        exclude=_exclude(arguments.get("exclude_session_ids")),
    )


def truncate_content(text: str, cap: int) -> tuple[str, bool, int]:
    original = len(text)
    if original > cap:
        return text[:cap] + "…", True, original
    return text, False, original


def shape_message(row: Mapping[str, JSON], *, anchor_seq: int | None = None, max_content: int) -> dict[str, JSON]:
    content = row.get("content")
    text = content if isinstance(content, str) else ""
    bounded, truncated, original = truncate_content(text, max_content)
    entry: dict[str, JSON] = {
        "id": row.get("id"),
        "seq": row.get("seq"),
        "role": row.get("role"),
        "content": bounded,
        "created_at": row.get("created_at"),
    }
    seq = row.get("seq")
    if anchor_seq is not None and isinstance(seq, int) and seq == anchor_seq:
        entry["anchor"] = True
    if truncated:
        entry["content_truncated"] = True
        entry["original_content_chars"] = original
    return entry


def group_discover(rows: Sequence[Mapping[str, JSON]], limit: int) -> list[dict[str, JSON]]:
    seen: set[str] = set()
    results: list[dict[str, JSON]] = []
    for row in rows:
        session_id = row.get("session_id")
        if not isinstance(session_id, str) or session_id in seen:
            continue
        seen.add(session_id)
        content = row.get("content")
        snippet, truncated, original = truncate_content(
            content if isinstance(content, str) else "", DISCOVER_SNIPPET_CHARS
        )
        entry: dict[str, JSON] = {
            "session_id": session_id,
            "title": row.get("title"),
            "when": row.get("session_created_at"),
            "match_message_id": row.get("id"),
            "match_seq": row.get("seq"),
            "matched_role": row.get("role"),
            "snippet": snippet,
        }
        if truncated:
            entry["content_truncated"] = True
            entry["original_content_chars"] = original
        results.append(entry)
        if len(results) >= limit:
            break
    return results


def browse_entry(row: Mapping[str, JSON]) -> dict[str, JSON]:
    preview = row.get("preview")
    bounded, truncated, _ = truncate_content(preview if isinstance(preview, str) else "", PREVIEW_CHARS)
    entry: dict[str, JSON] = {
        "session_id": row.get("session_id"),
        "title": row.get("title"),
        "when": row.get("created_at"),
        "message_count": row.get("message_count"),
        "preview": bounded,
    }
    if truncated:
        entry["preview_truncated"] = True
    return entry


def render(mode: str, data: Mapping[str, JSON]) -> str:
    if mode == "discover":
        results = data.get("results")
        items = results if isinstance(results, list) else []
        if not items:
            return "No matching sessions found in this Agent space."
        lines = [f"Found {len(items)} matching session(s):"]
        for entry in items:
            if isinstance(entry, dict):
                lines.append(
                    f"- session {entry.get('session_id')} ({entry.get('title')}): "
                    f"match seq={entry.get('match_seq')} role={entry.get('matched_role')}: {entry.get('snippet')}"
                )
        lines.append("Scroll with session_id + around_seq to read more around a match.")
        return "\n".join(lines)
    if mode == "browse":
        results = data.get("results")
        items = results if isinstance(results, list) else []
        if not items:
            return "No recent sessions in this Agent space."
        lines = [f"{len(items)} recent session(s):"]
        for entry in items:
            if isinstance(entry, dict):
                lines.append(
                    f"- session {entry.get('session_id')} ({entry.get('title')}), "
                    f"{entry.get('message_count')} messages: {entry.get('preview')}"
                )
        return "\n".join(lines)
    messages = data.get("messages")
    items = messages if isinstance(messages, list) else []
    if mode == "read":
        header = f"Session {data.get('session_id')} ({data.get('title')}), {data.get('message_count')} messages"
        if data.get("truncated"):
            header += f"; showing first {data.get('head')} + last {data.get('tail')}"
    else:
        header = (
            f"Session {data.get('session_id')} around seq={data.get('around_seq')} "
            f"(window={data.get('window')}, before={data.get('messages_before')}, after={data.get('messages_after')})"
        )
    lines = [header]
    for entry in items:
        if isinstance(entry, dict):
            marker = " [anchor]" if entry.get("anchor") else ""
            lines.append(f"seq={entry.get('seq')} {entry.get('role')}{marker}: {entry.get('content')}")
    return "\n".join(lines)
