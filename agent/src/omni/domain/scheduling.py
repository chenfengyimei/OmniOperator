"""Deterministic UTC scheduling and numeric cron rules; resolved timezone data is injected."""

from __future__ import annotations

import datetime as dt
import re
import uuid
from dataclasses import dataclass
from typing import Literal, cast

from pydantic import BaseModel, ConfigDict, Field, ValidationError, model_validator

from .contracts import BusinessError
from .skills import MAX_ACTIVE
from .skills import identifiers as skill_identifiers

UTC = dt.timezone.utc
LEASE_SECONDS = 60
# A binding may never exceed the runtime activation cap: saving more would block
# every occurrence once skill_activate_run rejects the fifth activation.
MAX_SKILLS = MAX_ACTIVE
MAX_CONTEXT_REFS = 4
MAX_CONTEXT_DEPTH = 4
MAX_CONTEXT_NODES = 64
EXCERPT_CHARS = 2000
EXCERPT_MARKER = "…[truncated]"
CONTEXT_MISSING = "(no completed output yet)"
CONTEXT_UNAVAILABLE = "(unavailable)"


def instant(value: str) -> dt.datetime:
    try:
        parsed = dt.datetime.fromisoformat(value)
        if parsed.tzinfo is None or parsed.utcoffset() is None:
            raise ValueError
        return parsed.astimezone(UTC)
    except (ValueError, TypeError, OverflowError) as exc:
        raise BusinessError(
            "invalid_schedule_time", "An ISO timestamp with explicit timezone is required", status=422
        ) from exc


def timestamp(value: dt.datetime) -> str:
    if value.tzinfo is None:
        raise ValueError("A timezone-aware time is required")
    return value.astimezone(UTC).isoformat(timespec="microseconds")


def cron_field(value: str, lower: int, upper: int) -> tuple[int, ...]:
    result: set[int] = set()
    for item in value.split(","):
        if not re.fullmatch(r"(?:\*|[0-9]{1,2}(?:-[0-9]{1,2})?)(?:/[0-9]{1,2})?", item):
            raise ValueError("Numeric cron fields are required")
        base, _, stride = item.partition("/")
        step = int(stride) if stride else 1
        if not 1 <= step <= upper - lower + 1:
            raise ValueError("Invalid cron step")
        if base == "*":
            start, end = lower, upper
        elif "-" in base:
            start, end = (int(n) for n in base.split("-"))
        else:
            start = int(base)
            end = upper if stride else start
        if not lower <= start <= end <= upper:
            raise ValueError("Cron value is outside its field")
        result.update(range(start, end + 1, step))
    return tuple(sorted(result))


@dataclass(frozen=True)
class Cron:
    minutes: tuple[int, ...]
    hours: tuple[int, ...]
    days: tuple[int, ...]
    months: tuple[int, ...]
    weekdays: tuple[int, ...]

    @classmethod
    def parse(cls, expression: str) -> Cron:
        try:
            fields = expression.split()
            if len(fields) != 5 or len(expression) > 100:
                raise ValueError
            minute, hour, day, month, week = [
                cron_field(f, a, b)
                for f, (a, b) in zip(fields, [(0, 59), (0, 23), (1, 31), (1, 12), (0, 7)], strict=True)
            ]
            return cls(minute, hour, day, month, tuple(sorted({n % 7 for n in week})))
        except (ValueError, AttributeError) as exc:
            raise BusinessError(
                "invalid_schedule", "Use five bounded numeric cron fields: minute hour day month weekday", status=422
            ) from exc

    def matches_day(self, date: dt.date) -> bool:
        dom = date.day in self.days
        dow = (date.weekday() + 1) % 7 in self.weekdays
        day_match = dom and dow if len(self.days) == 31 or len(self.weekdays) == 7 else dom or dow
        return date.month in self.months and day_match

    def find(self, boundary: dt.datetime, zone: dt.tzinfo, *, previous: bool = False) -> dt.datetime:
        boundary = boundary.astimezone(UTC)
        date = boundary.astimezone(zone).date()
        hours = tuple(reversed(self.hours)) if previous else self.hours
        minutes = tuple(reversed(self.minutes)) if previous else self.minutes
        # A valid numeric five-field rule has a match within an eight-year
        # Gregorian leap gap. Invalid dates such as 31 February fail boundedly.
        for offset in range(366 * 8 + 2):
            try:
                candidate_date = date + dt.timedelta(days=-offset if previous else offset)
            except OverflowError:
                break
            if not self.matches_day(candidate_date):
                continue
            for hour in hours:
                for minute in minutes:
                    local = dt.datetime.combine(candidate_date, dt.time(hour, minute))
                    utc = local.replace(tzinfo=zone, fold=0).astimezone(UTC)
                    returned = utc.astimezone(zone)
                    if returned.replace(tzinfo=None) != local or returned.fold != 0:
                        continue  # Nonexistent local minutes are never invented.
                    if (utc <= boundary) if previous else (utc > boundary):
                        return utc
        raise BusinessError(
            "schedule_no_match", "No schedule time exists within the supported calendar horizon", status=422
        )


def skill_refs(value: list[str]) -> list[str]:
    """Bound skills are unique canonical ids; the runtime activation cap is the only storage bound."""
    try:
        return list(skill_identifiers(value))
    except BusinessError as exc:
        raise BusinessError("invalid_skill", "Bound skills require unique canonical skill ids", status=422) from exc


def context_refs(value: list[str]) -> list[str]:
    result: list[str] = []
    for item in value:
        try:
            if str(uuid.UUID(item)) != item or item in result:
                raise ValueError
        except (ValueError, AttributeError):
            raise BusinessError(
                "invalid_context_ref", "Context references require unique canonical schedule ids", status=422
            ) from None
        result.append(item)
    return result


def excerpt(text: str) -> str:
    """Bound one excerpt; the marker only appears when content was actually dropped."""
    return text if len(text) <= EXCERPT_CHARS else text[:EXCERPT_CHARS] + EXCERPT_MARKER


def context_text(entries: list[tuple[str, str]], prompt: str) -> str:
    """Position-ordered scheduled-job context block followed by the original task."""
    if not entries:
        return prompt
    block = "\n".join(f"Scheduled job context ({name}): {value}" for name, value in entries)
    return f"{block}\n\nTask: {prompt}"


class ScheduleValue(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)
    kind: Literal["cron", "interval", "once"] = "cron"
    timezone: str = Field(default="UTC", min_length=1, max_length=100)
    expression: str = Field(default="0 9 * * *", max_length=100)
    interval_seconds: int | None = Field(default=None, ge=60, le=31536000)
    start_at: str | None = Field(default=None, max_length=40)
    session_id: str | None = None
    prompt: str = Field(min_length=1, max_length=20000)
    misfire: Literal["latest"] = "latest"
    skills: list[str] = Field(default_factory=list, max_length=MAX_SKILLS)
    context_from: list[str] = Field(default_factory=list, max_length=MAX_CONTEXT_REFS)
    continuity: bool = False

    @model_validator(mode="after")
    def valid(self):
        if not self.prompt.strip() or "\0" in self.prompt:
            raise ValueError("A bounded prompt is required")
        if self.session_id is not None and str(uuid.UUID(self.session_id)) != self.session_id:
            raise ValueError("Session requires a full canonical UUID")
        self.skills = skill_refs(self.skills)
        self.context_from = context_refs(self.context_from)
        if self.start_at:
            value = instant(self.start_at)
            if not 1970 <= value.year <= 9990:
                raise ValueError("Time is outside the supported range")
            self.start_at = timestamp(value)
        if self.kind == "cron":
            Cron.parse(self.expression)
            if self.interval_seconds is not None or self.start_at is not None:
                raise ValueError("Cron does not use interval anchors")
        elif (
            self.expression
            or (self.kind == "interval" and self.interval_seconds is None)
            or (self.kind == "once" and (self.interval_seconds is not None or self.start_at is None))
        ):
            raise ValueError("Use only the fields of the selected schedule kind")
        return self


def schedule_value(value: object) -> dict[str, object]:
    try:
        return ScheduleValue.model_validate(value).model_dump()
    except (ValueError, TypeError, ValidationError) as exc:
        raise BusinessError("invalid_schedule", "Schedule fields, time or recurrence are invalid", status=422) from exc


def next_due(value: dict[str, object], after: str, zone: dt.tzinfo) -> str | None:
    boundary = instant(after)
    if value["kind"] == "cron":
        return timestamp(Cron.parse(str(value["expression"])).find(boundary, zone))
    anchor = instant(str(value["start_at"]))
    if anchor > boundary:
        return timestamp(anchor)
    if value["kind"] == "once":
        return None
    interval = dt.timedelta(seconds=cast(float, value["interval_seconds"]))
    return timestamp(anchor + ((boundary - anchor) // interval + 1) * interval)


def due_selection(value: dict[str, object], first_due: str, now: str, zone: dt.tzinfo) -> dict[str, object]:
    first, current = instant(first_due), instant(now)
    if first > current:
        raise BusinessError("not_due", "Schedule time has not arrived")
    if value["kind"] == "cron":
        due = Cron.parse(str(value["expression"])).find(current, zone, previous=True)
    elif value["kind"] == "interval":
        interval = dt.timedelta(seconds=cast(float, value["interval_seconds"]))
        due = first + ((current - first) // interval) * interval
    else:
        due = first
    if due < first:
        raise BusinessError("invalid_schedule_cursor", "Schedule cursor does not match its recurrence")
    return {
        "scheduled_at": timestamp(due),
        "local_time": due.astimezone(zone).isoformat(),
        "coalesced_from": timestamp(first) if due > first else None,
        "next_at": next_due(value, timestamp(due), zone),
    }
