"""Model-side scheduled-job contract: decidable syntax, bounded requests and projections; no I/O."""

from __future__ import annotations

import datetime as dt
import re
import uuid
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from typing import cast

from .contracts import JSON, BusinessError, ToolSpec
from .scheduling import MAX_CONTEXT_REFS, MAX_SKILLS, Cron, instant, timestamp

TOOL_NAME = "cronjob_manage"
ACTIONS = ("create", "list", "update", "pause", "resume", "remove")
LIST_LIMIT = 50
PROMPT_PREVIEW = 200
DEFAULT_NAME_CHARS = 50
MAX_NAME = 100
MAX_PROMPT = 20000
MAX_TIMEZONE = 100
MAX_SCHEDULE_TEXT = 100
MIN_INTERVAL_SECONDS = 60
MAX_INTERVAL_SECONDS = 31536000

CRONJOB_MANAGE = ToolSpec(
    TOOL_NAME,
    "Manage this Agent's scheduled jobs. action=create requires schedule and prompt (name defaults to the "
    "prompt head); schedule accepts five-field numeric cron ('0 9 * * *'), an interval ('30m', '2h', "
    "'every 2 hours'; 60s to 365d), a relative one-shot ('in 30m', resolved on the server clock) or an ISO "
    "timestamp with an explicit UTC offset; natural-language dates are rejected. update is a patch: omitted "
    "fields keep their value, skills=[]/context_from=[]/continuity=false clear them; it requires job_id and "
    "the current revision (conflicts reload via list). pause/resume/remove also require job_id and revision; "
    "remove archives and cancels pending occurrences. list returns a bounded projection (at most 50 per "
    "page, after=last job_id cursor). skills bind up to 4 enabled skill ids as guidance only, never "
    "permissions; context_from references up to 4 live jobs whose latest succeeded output is injected before "
    "the prompt; continuity=true also injects this job's own previous output. Every action waits for owner "
    "approval; an unknown outcome is never replayed - reconcile with list.",
    {
        "type": "object",
        "properties": {
            "action": {"type": "string", "enum": list(ACTIONS)},
            "schedule": {"type": "string", "minLength": 1, "maxLength": MAX_SCHEDULE_TEXT},
            "name": {"type": "string", "minLength": 1, "maxLength": MAX_NAME},
            "prompt": {"type": "string", "minLength": 1, "maxLength": MAX_PROMPT},
            "timezone": {"type": "string", "minLength": 1, "maxLength": MAX_TIMEZONE},
            "skills": {
                "type": "array",
                "items": {"type": "string", "minLength": 1, "maxLength": 64},
                "maxItems": MAX_SKILLS,
            },
            "context_from": {
                "type": "array",
                "items": {"type": "string", "minLength": 1, "maxLength": 64},
                "maxItems": MAX_CONTEXT_REFS,
            },
            "continuity": {"type": "boolean"},
            "job_id": {"type": "string", "minLength": 1, "maxLength": 64},
            "revision": {"type": "integer", "minimum": 1},
            "after": {"type": "string", "maxLength": 64},
            "limit": {"type": "integer", "minimum": 1, "maximum": LIST_LIMIT},
        },
        "required": ["action"],
        "additionalProperties": False,
    },
    writes=True,
    timeout_s=30,
    resource="schedules",
)

_UNIT_SECONDS = {
    "s": 1,
    "sec": 1,
    "second": 1,
    "seconds": 1,
    "m": 60,
    "min": 60,
    "minute": 60,
    "minutes": 60,
    "h": 3600,
    "hr": 3600,
    "hour": 3600,
    "hours": 3600,
    "d": 86400,
    "day": 86400,
    "days": 86400,
}
_DURATION = re.compile(r"([0-9]+)\s*([a-z]+)", re.IGNORECASE)


def invalid(message: str) -> BusinessError:
    return BusinessError("invalid_tool_arguments", message, status=422)


def invalid_schedule(message: str) -> BusinessError:
    return BusinessError("invalid_schedule", message, status=422)


def _duration(text: str) -> int | None:
    match = _DURATION.fullmatch(text.strip())
    if match is None:
        return None
    unit = match.group(2).lower()
    if unit not in _UNIT_SECONDS:
        return None
    return int(match.group(1)) * _UNIT_SECONDS[unit]


def _once(start_at: str) -> dict[str, object]:
    return {"kind": "once", "expression": "", "interval_seconds": None, "start_at": start_at}


def compile_schedule(text: str, now: str) -> dict[str, object]:
    """Compile one decidable form; relative one-shots are resolved by the server clock, never the model."""
    value = text.strip()
    if not value or len(value) > MAX_SCHEDULE_TEXT or "\0" in value:
        raise invalid_schedule("Use numeric cron, an interval, 'in <duration>' or an ISO timestamp with offset")
    relative = re.fullmatch(r"in\s+(.+)", value, re.IGNORECASE)
    if relative is not None:
        seconds = _duration(relative.group(1))
        if seconds is None or seconds <= 0:
            raise invalid_schedule("A relative one-shot needs a positive duration such as 'in 30m'")
        return _once(timestamp(instant(now) + dt.timedelta(seconds=seconds)))
    if re.match(r"[0-9]{4}-[0-9]{2}-[0-9]{2}", value):
        return _once(timestamp(instant(value)))
    seconds = _duration(re.sub(r"^every\s+", "", value, flags=re.IGNORECASE))
    if seconds is not None:
        if not MIN_INTERVAL_SECONDS <= seconds <= MAX_INTERVAL_SECONDS:
            raise invalid_schedule("An interval must be between 60 seconds and 365 days")
        return {"kind": "interval", "expression": "", "interval_seconds": seconds, "start_at": None}
    if len(value.split()) == 5:
        Cron.parse(value)
        return {"kind": "cron", "expression": value, "interval_seconds": None, "start_at": None}
    raise invalid_schedule(
        "Use five-field numeric cron, '30m'/'every 2 hours', 'in 30m' or an ISO timestamp with explicit offset"
    )


@dataclass(frozen=True)
class Request:
    action: str
    schedule: str = ""
    name: str = ""
    prompt: str = ""
    timezone: str = ""
    skills: tuple[str, ...] | None = None
    context_from: tuple[str, ...] | None = None
    continuity: bool | None = None
    job_id: str = ""
    revision: int = 0
    after: str = ""
    limit: int = LIST_LIMIT


def _text(arguments: Mapping[str, JSON], key: str) -> str:
    value = arguments.get(key)
    if value is None:
        return ""
    if not isinstance(value, str):
        raise invalid(f"{key} must be a string")
    return value


def _ident(arguments: Mapping[str, JSON], key: str) -> str:
    value = _text(arguments, key)
    if value:
        try:
            if str(uuid.UUID(value)) != value:
                raise ValueError
        except (ValueError, AttributeError):
            raise invalid(f"{key} must be a canonical job id") from None
    return value


def _refs(arguments: Mapping[str, JSON], key: str) -> tuple[str, ...] | None:
    value = arguments.get(key)
    if value is None:
        return None
    if not isinstance(value, list):
        raise invalid(f"{key} must be a list of ids")
    items = cast(list[object], value)
    if not all(isinstance(item, str) for item in items):
        raise invalid(f"{key} must be a list of ids")
    return tuple(cast(list[str], items))


def parse_request(arguments: Mapping[str, JSON]) -> Request:
    action = arguments.get("action")
    if not isinstance(action, str) or action not in ACTIONS:
        raise invalid("action must be one of create/list/update/pause/resume/remove")
    continuity = arguments.get("continuity")
    if continuity is not None and type(continuity) is not bool:
        raise invalid("continuity must be a boolean")
    revision = arguments.get("revision")
    if revision is not None and (type(revision) is not int or revision < 1):
        raise invalid("revision must be a positive integer")
    limit = arguments.get("limit")
    if limit is not None and (type(limit) is not int or not 1 <= limit <= LIST_LIMIT):
        raise invalid(f"limit must be an integer between 1 and {LIST_LIMIT}")
    request = Request(
        action=action,
        schedule=_text(arguments, "schedule"),
        name=_text(arguments, "name"),
        prompt=_text(arguments, "prompt"),
        timezone=_text(arguments, "timezone"),
        skills=_refs(arguments, "skills"),
        context_from=_refs(arguments, "context_from"),
        continuity=continuity,
        job_id=_ident(arguments, "job_id"),
        revision=revision if revision is not None else 0,
        after=_ident(arguments, "after"),
        limit=limit if limit is not None else LIST_LIMIT,
    )
    if request.action == "create":
        if not request.schedule or not request.prompt:
            raise invalid("create requires schedule and prompt")
    elif request.action != "list":
        if not request.job_id or request.revision < 1:
            raise invalid(f"{request.action} requires job_id and the current revision")
        if request.action == "update" and not (
            request.schedule
            or request.name
            or request.prompt
            or request.timezone
            or request.skills is not None
            or request.context_from is not None
            or request.continuity is not None
        ):
            raise invalid("update requires at least one changed field")
    return request


def job_projection(record: Mapping[str, object], last: Mapping[str, object] | None) -> dict[str, JSON]:
    """Bounded model-facing view; session identity, the full prompt and tzdb pins never cross."""
    value = cast(Mapping[str, object], record["value"])
    progress = cast(Mapping[str, object], record["progress"])
    kind = str(value["kind"])
    result: dict[str, JSON] = {
        "job_id": str(record["id"]),
        "name": str(record["name"]),
        "enabled": bool(record["enabled"]),
        "revision": cast(int, record["revision"]),
        "kind": kind,
        "timezone": str(value["timezone"]),
        "next_at": cast(str | None, progress.get("next_at")),
        "last_scheduled_at": cast(str | None, progress.get("last_scheduled_at")),
        "prompt_preview": str(value["prompt"])[:PROMPT_PREVIEW],
        "skills": cast(list[JSON], value.get("skills") or []),
        "context_from": cast(list[JSON], value.get("context_from") or []),
        "continuity": bool(value.get("continuity")),
        "last_occurrence": None,
    }
    if kind == "cron":
        result["expression"] = str(value["expression"])
    elif kind == "interval":
        result["interval_seconds"] = cast(int, value["interval_seconds"])
    if value.get("start_at"):
        result["start_at"] = str(value["start_at"])
    if last is not None:
        result["last_occurrence"] = {
            "state": str(last.get("run_state") or last["state"]),
            "error_code": str(last.get("error_code") or last.get("run_error_code") or ""),
        }
    return result


def render_jobs(jobs: Sequence[Mapping[str, JSON]]) -> str:
    if not jobs:
        return "No scheduled jobs"
    lines = [f"Scheduled jobs ({len(jobs)}):"]
    for job in jobs:
        lines.append(
            f"- {job['job_id']} {job['name']} enabled={job['enabled']} revision={job['revision']} "
            f"kind={job['kind']} next_at={job['next_at'] or '-'}"
        )
    return "\n".join(lines)
