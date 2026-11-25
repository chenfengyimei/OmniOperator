"""Owner APIs for schedule definitions, history and occurrence decisions."""

from __future__ import annotations

import uuid

from fastapi import APIRouter, Query, Request
from pydantic import Field

from ...application.scheduling import SchedulingService
from ...domain.contracts import BusinessError
from . import RuntimeLookup, StrictInput, bounded_configuration, configuration_input


class ScheduleInput(StrictInput):
    name: str = Field(min_length=1, max_length=100)
    enabled: bool = Field(default=False, strict=True)
    value: dict[str, object]
    revision: int = Field(default=0, ge=0, strict=True)


class ScheduleReview(StrictInput):
    occurrence_id: uuid.UUID
    fingerprint: str = Field(pattern=r"^[a-f0-9]{64}$")


def schedules_router(runtime: RuntimeLookup) -> APIRouter:
    router = APIRouter(prefix="/agents/{agent_id}/schedules")

    def schedules_service(request: Request) -> SchedulingService:
        service = runtime(request).feature_services.get("schedules")
        if not isinstance(service, SchedulingService):
            raise BusinessError("scheduler_unavailable", "Scheduling service is unavailable", status=503)
        return service

    @router.get("")
    async def schedules(
        agent_id: uuid.UUID,
        request: Request,
        after: uuid.UUID | None = None,
        limit: int = Query(default=100, ge=1, le=200),
        include_archived: bool = False,
    ):
        items = await schedules_service(request).page(
            str(agent_id), str(after) if after else "", limit, include_archived
        )
        return {"items": items, "next_after": items[-1]["id"] if len(items) == limit else None}

    @router.post("", status_code=201)
    async def create_schedule(agent_id: uuid.UUID, request: Request):
        data = configuration_input(ScheduleInput, await bounded_configuration(request, 100000))
        return await schedules_service(request).save(
            str(agent_id), data.name, data.enabled, data.value, revision=data.revision
        )

    @router.get("/{schedule_id}")
    async def schedule(agent_id: uuid.UUID, schedule_id: uuid.UUID, request: Request):
        return await schedules_service(request).get(str(agent_id), str(schedule_id))

    @router.put("/{schedule_id}")
    async def save_schedule(agent_id: uuid.UUID, schedule_id: uuid.UUID, request: Request):
        data = configuration_input(ScheduleInput, await bounded_configuration(request, 100000))
        return await schedules_service(request).save(
            str(agent_id), data.name, data.enabled, data.value, str(schedule_id), data.revision
        )

    @router.delete("/{schedule_id}")
    async def archive_schedule(
        agent_id: uuid.UUID, schedule_id: uuid.UUID, request: Request, revision: int = Query(ge=1)
    ):
        await schedules_service(request).archive(str(agent_id), str(schedule_id), revision)
        return {"archived": True}

    @router.get("/{schedule_id}/history")
    async def schedule_history(
        agent_id: uuid.UUID,
        schedule_id: uuid.UUID,
        request: Request,
        after: int = Query(default=0, ge=0),
        limit: int = Query(default=50, ge=1, le=200),
    ):
        items = await schedules_service(request).history(str(agent_id), str(schedule_id), after, limit)
        return {"items": items, "next_after": items[-1]["revision"] if len(items) == limit else None}

    @router.get("/{schedule_id}/occurrences")
    async def schedule_occurrences(
        agent_id: uuid.UUID,
        schedule_id: uuid.UUID,
        request: Request,
        after: int = Query(default=0, ge=0),
        limit: int = Query(default=50, ge=1, le=200),
    ):
        items = await schedules_service(request).occurrences(str(agent_id), str(schedule_id), after, limit)
        return {"items": items, "next_after": items[-1]["sequence"] if len(items) == limit else None}

    @router.post("/{schedule_id}/review")
    async def schedule_review(agent_id: uuid.UUID, schedule_id: uuid.UUID, request: Request):
        data = configuration_input(ScheduleReview, await bounded_configuration(request, 1000))
        await schedules_service(request).review(
            str(agent_id), str(schedule_id), str(data.occurrence_id), data.fingerprint
        )
        return {"reviewed": True, "unknown_records_preserved": True}

    @router.post("/{schedule_id}/occurrences/{occurrence_id}/cancel")
    async def schedule_cancel(agent_id: uuid.UUID, schedule_id: uuid.UUID, occurrence_id: uuid.UUID, request: Request):
        await schedules_service(request).cancel(str(agent_id), str(schedule_id), str(occurrence_id))
        return {"accepted": True}

    return router
