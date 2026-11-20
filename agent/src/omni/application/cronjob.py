"""Model-side scheduled-job capability; a thin adapter over the one scheduling service, no second kernel."""

from __future__ import annotations

from collections.abc import Mapping
from typing import cast

from ..domain.contracts import JSON, ActionResult, BusinessError, Effect, InvocationContext, Outcome
from ..domain.cronjob import (
    CRONJOB_MANAGE,
    DEFAULT_NAME_CHARS,
    compile_schedule,
    job_projection,
    parse_request,
    render_jobs,
)
from .ports import Clock, Record
from .scheduling import SchedulingService

# Known public failures become model-visible errors; anything else stays unknown and is never replayed.
_BUSINESS_CODES = frozenset(
    {
        "invalid_tool_arguments",
        "invalid_schedule",
        "invalid_schedule_time",
        "schedule_in_past",
        "schedule_no_match",
        "schedule_limit",
        "invalid_skill",
        "invalid_context_ref",
        "invalid_context_cycle",
        "skill_channel_unsupported",
        "session_deleted",
        "revision_conflict",
        "not_found",
        "agent_not_found",
        "scheduler_closing",
    }
)


class CronjobCapabilities:
    def __init__(self, schedules: SchedulingService, clock: Clock) -> None:
        self._schedules, self._clock = schedules, clock

    async def invoke(
        self, name: str, arguments: Mapping[str, JSON], *, operation_id: str, context: InvocationContext
    ) -> ActionResult:
        try:
            return await self._invoke(name, arguments, context)
        except BusinessError as exc:
            if exc.code in _BUSINESS_CODES:
                return ActionResult(Outcome.ERROR, exc.message, error_code=exc.code)
            raise

    async def _invoke(self, name: str, arguments: Mapping[str, JSON], context: InvocationContext) -> ActionResult:
        if name != CRONJOB_MANAGE.name:
            return ActionResult(Outcome.ERROR, "Unknown scheduled-job tool", error_code="tool_not_found")
        request = parse_request(arguments)
        agent = context.agent_id
        if request.action == "list":
            rows = await self._schedules.page(agent, request.after, request.limit)
            jobs = [await self._projection(agent, row) for row in rows]
            return ActionResult(Outcome.OK, render_jobs(jobs), data={"jobs": cast(list[JSON], jobs)})
        if request.action == "create":
            value: dict[str, object] = {
                **compile_schedule(request.schedule, self._clock.now()),
                "prompt": request.prompt,
            }
            if request.timezone:
                value["timezone"] = request.timezone
            if request.skills is not None:
                value["skills"] = list(request.skills)
            if request.context_from is not None:
                value["context_from"] = list(request.context_from)
            if request.continuity is not None:
                value["continuity"] = request.continuity
            row = await self._schedules.save(
                agent, request.name or request.prompt.strip()[:DEFAULT_NAME_CHARS], True, value
            )
            return await self._written(agent, row, "created")
        if request.action == "remove":
            await self._schedules.archive(agent, request.job_id, request.revision)
            return ActionResult(
                Outcome.OK,
                f"Scheduled job removed: {request.job_id}",
                Effect.APPLIED,
                verification=f"revision={request.revision} archived",
            )
        current = await self._schedules.get(agent, request.job_id)
        if request.action == "update":
            value = dict(cast(Mapping[str, object], current["value"]))
            if request.schedule:
                value.update(compile_schedule(request.schedule, self._clock.now()))
            if request.prompt:
                value["prompt"] = request.prompt
            if request.timezone:
                value["timezone"] = request.timezone
            if request.skills is not None:
                value["skills"] = list(request.skills)
            if request.context_from is not None:
                value["context_from"] = list(request.context_from)
            if request.continuity is not None:
                value["continuity"] = request.continuity
            row = await self._schedules.save(
                agent,
                request.name or str(current["name"]),
                bool(current["enabled"]),
                value,
                request.job_id,
                request.revision,
            )
            return await self._written(agent, row, "updated")
        row = await self._schedules.save(
            agent,
            str(current["name"]),
            request.action == "resume",
            dict(cast(Mapping[str, object], current["value"])),
            request.job_id,
            request.revision,
        )
        return await self._written(agent, row, "paused" if request.action == "pause" else "resumed")

    async def _written(self, agent: str, row: Record, verb: str) -> ActionResult:
        projection = await self._projection(agent, row)
        progress = cast(Mapping[str, object], row["progress"])
        text = f"Scheduled job {verb}: {row['id']} revision={row['revision']}"
        if progress.get("next_at"):
            text += f" next_at={progress['next_at']}"
        return ActionResult(
            Outcome.OK,
            text,
            Effect.APPLIED,
            verification=f"revision={row['revision']} enabled={row['enabled']}",
            data=projection,
        )

    async def _projection(self, agent: str, row: Record) -> dict[str, JSON]:
        progress = cast(Mapping[str, object], row["progress"])
        sequence = cast(int, progress.get("sequence") or 0)
        last: Record | None = None
        if sequence > 0:
            occurrences = await self._schedules.occurrences(agent, str(row["id"]), sequence - 1, 1)
            last = occurrences[0] if occurrences else None
        return job_projection(row, last)

    async def close(self, timeout_s: float) -> bool:
        return True
