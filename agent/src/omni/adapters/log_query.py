"""Bounded diagnostic pages with cursors that survive rename and process restart."""

from __future__ import annotations

import base64
import datetime as dt
import hashlib
import json
import stat
from contextlib import AbstractContextManager
from dataclasses import dataclass
from pathlib import Path
from typing import Protocol

from ..application.ports import Record
from ..domain.contracts import BusinessError
from .diagnostics import Redactor

MAX_SCAN = 1_048_576
MAX_RECORD = 32768


class LogSource(Protocol):
    @property
    def directory(self) -> Path: ...
    @property
    def io_lock(self) -> AbstractContextManager: ...
    @property
    def stderr_only(self) -> bool: ...
    @property
    def segments(self) -> int: ...
    @property
    def redactor(self) -> Redactor: ...


@dataclass(frozen=True)
class LogQuery:
    limit: int = 100
    cursor: str = ""
    level: str = ""
    component: str = ""
    run_id: str = ""
    since: str = ""
    until: str = ""

    @classmethod
    def from_record(cls, values: Record) -> LogQuery:
        fields = ("cursor", "level", "component", "run_id", "since", "until")
        limit = values.get("limit", 100)
        if type(limit) is not int or set(values) - {"limit", *fields}:
            raise BusinessError("invalid_log_query", "Diagnostic query fields are invalid", status=422)
        text: dict[str, str] = {}
        for field in fields:
            value = values.get(field, "")
            if not isinstance(value, str):
                raise BusinessError("invalid_log_query", "Diagnostic filters must be strings", status=422)
            text[field] = value
        return cls(
            limit=limit,
            cursor=text["cursor"],
            level=text["level"],
            component=text["component"],
            run_id=text["run_id"],
            since=text["since"],
            until=text["until"],
        )

    def __post_init__(self) -> None:
        if not 1 <= self.limit <= 500 or len(self.cursor) > 256:
            raise BusinessError("invalid_log_query", "Log page bounds are invalid", status=422)
        if self.level and self.level not in {"DEBUG", "INFO", "WARNING", "ERROR", "CRITICAL"}:
            raise BusinessError("invalid_log_query", "Unknown log level", status=422)
        if len(self.component) > 200 or len(self.run_id) > 100:
            raise BusinessError("invalid_log_query", "Log filters are too long", status=422)
        for name in ("since", "until"):
            value = getattr(self, name)
            if value:
                try:
                    parsed = dt.datetime.fromisoformat(value)
                    if parsed.tzinfo is None or parsed.utcoffset() != dt.timedelta():
                        raise ValueError
                    object.__setattr__(self, name, parsed.isoformat(timespec="microseconds"))
                except ValueError as exc:
                    raise BusinessError(
                        "invalid_log_query", "Log times must use UTC with a timezone", status=422
                    ) from exc
        if self.since and self.until and self.since > self.until:
            raise BusinessError("invalid_log_query", "Log time range is inverted", status=422)


def _cursor(segment: str, offset: int) -> str:
    return (
        base64.urlsafe_b64encode(json.dumps([1, segment, offset], separators=(",", ":")).encode()).decode().rstrip("=")
    )


def _decode(value: str) -> tuple[str, int]:
    try:
        data = json.loads(base64.b64decode(value + "=" * (-len(value) % 4), altchars=b"-_", validate=True))
        if (
            not isinstance(data, list)
            or len(data) != 3
            or data[0] != 1
            or not isinstance(data[1], str)
            or len(data[1]) != 64
            or type(data[2]) is not int
            or data[2] < 0
        ):
            raise ValueError
        return data[1], data[2]
    except (ValueError, TypeError, UnicodeError) as exc:
        raise BusinessError("invalid_log_cursor", "Log cursor is malformed", status=422) from exc


class LogReader:
    def __init__(self, sink: LogSource) -> None:
        self.sink = sink

    def page(self, query: LogQuery) -> dict:
        if self.sink.stderr_only:
            raise BusinessError(
                "logs_external", "Diagnostics are directed to the deployment's stderr collector", status=409
            )
        # The writer owns rotation. A bounded page shares its lock, so Windows
        # never renames an open reader and a cursor cannot observe half a rotate.
        with self.sink.io_lock:
            return self._page(query)

    def tail(self, limit: int = 500) -> dict:
        """Read at most MAX_SCAN bytes/10000 records, retaining only bounded recent metadata."""
        if not 1 <= limit <= 500:
            raise BusinessError("invalid_log_query", "Bounded recent log count required", status=422)
        if self.sink.stderr_only:
            return {"available": False, "error_code": "logs_external", "items": []}
        with self.sink.io_lock:
            segments = self._paths()
            remaining, invalid, scanned = MAX_SCAN, 0, 0
            items = []
            truncated = False
            for index, path in enumerate(reversed(segments)):
                if not remaining:
                    truncated = True
                    break
                size = path.stat().st_size
                take = min(remaining, size)
                with path.open("rb") as stream:
                    stream.seek(size - take)
                    raw = stream.read(take)
                remaining -= len(raw)
                if size > take:
                    raw = raw.partition(b"\n")[2]
                    truncated = True
                end = len(raw)
                if raw.endswith(b"\n"):
                    end -= 1
                elif end:
                    invalid += 1
                    end = raw.rfind(b"\n")
                while end > 0 and len(items) < limit and scanned < 10000:
                    start = raw.rfind(b"\n", 0, end) + 1
                    line = raw[start:end]
                    end = start - 1
                    scanned += 1
                    try:
                        if len(line) > MAX_RECORD:
                            raise ValueError
                        record = json.loads(line)
                        if (
                            not isinstance(record, dict)
                            or not isinstance(record.get("record_id"), str)
                            or not isinstance(record.get("timestamp"), str)
                        ):
                            raise ValueError
                        items.append(record)
                    except (ValueError, UnicodeError, RecursionError):
                        invalid += 1
                if len(items) >= limit or scanned >= 10000:
                    truncated = truncated or end > 0 or index + 1 < len(segments)
                    break
            return {
                "available": True,
                "items": list(reversed(items)),
                "scanned_bytes": MAX_SCAN - remaining,
                "scanned_records": scanned,
                "invalid_records": invalid,
                "truncated": truncated,
            }

    def _paths(self) -> list[Path]:
        return self._checked_paths(
            ["runtime.jsonl" + (f".{number}" if number else "") for number in reversed(range(self.sink.segments))]
        )

    def _checked_paths(self, names: list[str]) -> list[Path]:
        if self.sink.directory.is_symlink() or self.sink.directory.is_junction():
            raise BusinessError("unsafe_log_path", "Log directory cannot be a link", status=503)
        result = []
        for name in names:
            path = self.sink.directory / name
            if path.is_symlink() or path.is_junction():
                raise BusinessError("unsafe_log_path", "Log segment cannot be a link", status=503)
            if path.exists():
                info = path.stat()
                if not stat.S_ISREG(info.st_mode) or info.st_nlink != 1:
                    raise BusinessError("unsafe_log_path", "Log segments must be single-link regular files", status=503)
                if info.st_size:
                    result.append(path)
        return result

    def _segments(self) -> list[tuple[Path, str]]:
        result = []
        for path in self._paths():
            with path.open("rb") as stream:
                first = stream.readline(MAX_RECORD + 1)
            if not first.endswith(b"\n") or len(first) > MAX_RECORD:
                raise BusinessError("log_record_invalid", "A diagnostic segment is incomplete or corrupt", status=503)
            result.append((path, hashlib.sha256(first).hexdigest()))
        return result

    def _page(self, query: LogQuery) -> dict:
        segments = self._segments()
        index, offset = 0, 0
        if query.cursor:
            segment, offset = _decode(query.cursor)
            index = next((i for i, (_, identity) in enumerate(segments) if identity == segment), -1)
            if index < 0:
                raise BusinessError(
                    "log_cursor_expired",
                    "The requested log segment is no longer retained; refresh the query",
                    status=410,
                )
        elif segments and not any((query.level, query.component, query.run_id, query.since, query.until)):
            # Initial unfiltered view is the current tail. Filters traverse the
            # retained history in bounded pages, including empty scanned pages.
            index = len(segments) - 1
            offset = max(0, segments[index][0].stat().st_size - MAX_SCAN)
            if offset:
                with segments[index][0].open("rb") as stream:
                    stream.seek(offset)
                    stream.readline(MAX_RECORD + 1)
                    offset = stream.tell()
        items, scanned, next_cursor, more = [], 0, query.cursor or None, False
        for i in range(index, len(segments)):
            path, identity = segments[i]
            with path.open("rb") as stream:
                size = path.stat().st_size
                position = offset if i == index else 0
                if position > size:
                    raise BusinessError(
                        "log_cursor_expired", "The log segment was truncated; refresh the query", status=410
                    )
                if position:
                    stream.seek(position - 1)
                    if stream.read(1) != b"\n":
                        raise BusinessError(
                            "invalid_log_cursor", "Cursor is not at a diagnostic record boundary", status=422
                        )
                stream.seek(position)
                next_cursor = _cursor(identity, position)
                while stream.tell() < size:
                    raw = stream.readline(MAX_RECORD + 1)
                    if not raw.endswith(b"\n") or len(raw) > MAX_RECORD:
                        raise BusinessError(
                            "log_record_invalid", "A diagnostic record is incomplete or corrupt", status=503
                        )
                    scanned += len(raw)
                    try:
                        entry = json.loads(raw)
                        if not isinstance(entry, dict) or not isinstance(entry.get("timestamp"), str):
                            raise ValueError
                    except (ValueError, UnicodeError) as exc:
                        raise BusinessError("log_record_invalid", "A diagnostic record is corrupt", status=503) from exc
                    next_cursor = _cursor(identity, stream.tell())
                    if (
                        (not query.level or entry.get("level") == query.level)
                        and (not query.component or entry.get("component") == query.component)
                        and (not query.run_id or entry.get("run_id") == query.run_id)
                        and (not query.since or entry["timestamp"] >= query.since)
                        and (not query.until or entry["timestamp"] <= query.until)
                    ):
                        items.append(self.sink.redactor.value(entry))
                    if len(items) >= query.limit or scanned >= MAX_SCAN:
                        more = stream.tell() < size or i < len(segments) - 1
                        return {"items": items, "next_cursor": next_cursor, "has_more": more, "scanned_bytes": scanned}
        return {"items": items, "next_cursor": next_cursor, "has_more": more, "scanned_bytes": scanned}


class CaptureLogReader(LogReader):
    """Only the authorized current boot capture; callers never supply filesystem paths."""

    def __init__(self, source: LogSource, operation_id: str) -> None:
        import uuid

        if str(uuid.UUID(operation_id)) != operation_id:
            raise BusinessError("invalid_debug_capture", "Debug capture identity is invalid", status=422)
        super().__init__(source)
        self._name = f"debug-{operation_id}.jsonl"

    def _paths(self) -> list[Path]:
        return self._checked_paths([self._name])
