"""Owner admission, readback and cancellation for maintenance jobs."""

from __future__ import annotations

import uuid
from typing import cast

from fastapi import APIRouter, Query, Request

from ...domain.contracts import BusinessError
from . import RuntimeLookup, bounded_configuration


def maintenance_router(runtime: RuntimeLookup) -> APIRouter:
    router = APIRouter(prefix="/agents/{agent_id}/maintenance")

    @router.post("", status_code=202)
    async def maintenance_submit(agent_id: uuid.UUID, request: Request):
        return await runtime(request).maintenance.submit(str(agent_id), await bounded_configuration(request, 4096))

    @router.get("")
    async def maintenance_list(
        agent_id: uuid.UUID,
        request: Request,
        after: int = Query(default=0, ge=0),
        limit: int = Query(default=50, ge=1, le=100),
    ):
        return await runtime(request).maintenance.page(str(agent_id), after, limit)

    @router.get("/{job_id}")
    async def maintenance_get(agent_id: uuid.UUID, job_id: uuid.UUID, request: Request):
        return await runtime(request).maintenance.get(str(agent_id), str(job_id))

    @router.post("/{job_id}/cancel")
    async def maintenance_cancel(agent_id: uuid.UUID, job_id: uuid.UUID, request: Request):
        data = await bounded_configuration(request, 2048)
        if set(data) != {"revision"} or type(cast(int, data["revision"])) is not int or cast(int, data["revision"]) < 1:
            raise BusinessError("maintenance_invalid", "Cancel requires the current positive revision", status=422)
        return await runtime(request).maintenance.cancel(str(agent_id), str(job_id), cast(int, data["revision"]))

    return router
