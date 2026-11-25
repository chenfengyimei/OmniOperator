"""Read-only audit and diagnostic views, bounded debug controls and retention reports."""

from __future__ import annotations

import uuid

from fastapi import APIRouter, Query, Request
from fastapi.responses import Response
from pydantic import Field

from ...domain.contracts import BusinessError
from ..log_query import LogQuery
from . import RuntimeLookup, StrictInput, bounded_configuration, configuration_input


class DebugInput(StrictInput):
    duration_s: int = Field(default=3600, ge=1, le=3600, strict=True)


class RunPinInput(StrictInput):
    pinned: bool = Field(strict=True)
    revision: int = Field(ge=1, strict=True)


def operations_router(runtime: RuntimeLookup) -> APIRouter:
    router = APIRouter()

    @router.get("/agents/{agent_id}/events/retention-preview")
    async def event_retention_preview(
        agent_id: uuid.UUID,
        request: Request,
        cursor: str = Query(default="", max_length=1024),
        limit: int = Query(default=20, ge=1, le=50),
        as_of: str = Query(default="", max_length=64),
    ):
        if "after" in request.query_params:
            raise BusinessError(
                "invalid_event_retention_query", "Use the event-preview cursor instead of after", status=422
            )
        return await runtime(request).application.event_retention_preview(
            str(agent_id), cursor=cursor, limit=limit, as_of=as_of
        )

    @router.get("/agents/{agent_id}/audit/retention-preview")
    async def audit_retention_preview(
        agent_id: uuid.UUID,
        request: Request,
        cursor: str = Query(default="", max_length=1024),
        limit: int = Query(default=20, ge=1, le=50),
        as_of: str = Query(default="", max_length=64),
    ):
        if "after" in request.query_params:
            raise BusinessError("invalid_audit_retention_query", "Use the preview cursor instead of after", status=422)
        return await runtime(request).application.audit_retention_preview(
            str(agent_id), cursor=cursor, limit=limit, as_of=as_of
        )

    @router.get("/agents/{agent_id}/runs/{run_id}/retention")
    async def run_retention(agent_id: uuid.UUID, run_id: uuid.UUID, request: Request):
        return await runtime(request).application.run_retention(str(agent_id), str(run_id))

    @router.put("/agents/{agent_id}/runs/{run_id}/retention-pin")
    async def run_retention_pin(agent_id: uuid.UUID, run_id: uuid.UUID, request: Request):
        data = configuration_input(RunPinInput, await bounded_configuration(request, 2048))
        return await runtime(request).application.pin_run(str(agent_id), str(run_id), data.pinned, data.revision)

    @router.get("/agents/{agent_id}/audit")
    async def audit(
        agent_id: uuid.UUID,
        request: Request,
        cursor: str = Query(default="", max_length=1024),
        limit: int = Query(default=100, ge=1, le=200),
    ):
        if "after" in request.query_params:
            raise BusinessError("invalid_history_query", "Use a history cursor instead of after", status=422)
        return await runtime(request).application.history("audit", str(agent_id), cursor=cursor, limit=limit)

    @router.get("/system/metrics")
    async def metrics(request: Request):
        return await runtime(request).operations.status()

    @router.get("/agents/{agent_id}/debug")
    async def debug_status(agent_id: uuid.UUID, request: Request):
        return await runtime(request).operations.debug_status(str(agent_id))

    @router.post("/agents/{agent_id}/debug")
    async def debug_start(agent_id: uuid.UUID, request: Request):
        data = configuration_input(DebugInput, await bounded_configuration(request, 1024))
        return await runtime(request).operations.debug_start(str(agent_id), data.duration_s)

    @router.delete("/agents/{agent_id}/debug/{operation_id}")
    async def debug_stop(agent_id: uuid.UUID, operation_id: uuid.UUID, request: Request):
        return await runtime(request).operations.debug_stop(str(agent_id), str(operation_id))

    @router.get("/agents/{agent_id}/debug/{operation_id}/logs")
    async def debug_logs(
        agent_id: uuid.UUID,
        operation_id: uuid.UUID,
        request: Request,
        limit: int = Query(default=100, ge=1, le=500),
        cursor: str = Query(default="", max_length=256),
    ):
        return await runtime(request).operations.debug_logs(
            str(agent_id), str(operation_id), {"limit": limit, "cursor": cursor}
        )

    @router.get("/system/diagnostics")
    async def diagnostic_bundle(request: Request):
        content = await runtime(request).operations.export()
        return Response(
            content,
            media_type="application/zip",
            headers={"Content-Disposition": 'attachment; filename="omni-diagnostics.zip"'},
        )

    @router.get("/system/logs")
    async def logs(
        request: Request,
        limit: int = Query(default=100, ge=1, le=500),
        cursor: str = Query(default="", max_length=256),
        level: str = Query(default="", max_length=10),
        component: str = Query(default="", max_length=200),
        run_id: str = Query(default="", max_length=100),
        since: str = Query(default="", max_length=40),
        until: str = Query(default="", max_length=40),
    ):
        query = LogQuery(limit, cursor, level, component, run_id, since, until)
        return await runtime(request).logs(query)

    @router.get("/system/audit")
    async def instance_audit(
        request: Request,
        cursor: str = Query(default="", max_length=1024),
        limit: int = Query(default=100, ge=1, le=200),
    ):
        if "after" in request.query_params:
            raise BusinessError("invalid_history_query", "Use a history cursor instead of after", status=422)
        return await runtime(request).credentials.audit(cursor, limit)

    return router
