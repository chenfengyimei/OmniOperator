"""Owner extension market, installation and version-bound authorization."""

from __future__ import annotations

import uuid
from collections.abc import Awaitable, Callable
from typing import TYPE_CHECKING, cast

from fastapi import APIRouter, Query, Request

from ...application.ports import Record
from ...domain.contracts import BusinessError

if TYPE_CHECKING:
    from ..http import HttpRuntime


def extensions_router(
    runtime: Callable[[Request], HttpRuntime], bounded_configuration: Callable[[Request, int], Awaitable[Record]]
) -> APIRouter:
    router = APIRouter()

    @router.post("/agents/{agent_id}/extensions", status_code=201)
    async def extension_install(agent_id: uuid.UUID, request: Request):
        return await runtime(request).extensions.install(str(agent_id), await bounded_configuration(request, 8192))

    @router.post("/agents/{agent_id}/extensions/market")
    async def extension_market(agent_id: uuid.UUID, request: Request):
        data = await bounded_configuration(request, 4096)
        if (
            not isinstance(cast(object, data), dict)
            or set(data) != {"source"}
            or not isinstance(data["source"], str)
            or not 1 <= len(data["source"]) <= 4000
        ):
            raise BusinessError("extension_invalid", "A bounded local market directory is required", status=422)
        return await runtime(request).extensions.market(str(agent_id), data["source"])

    @router.get("/agents/{agent_id}/extensions")
    async def extension_list(
        agent_id: uuid.UUID,
        request: Request,
        after: int = Query(default=0, ge=0),
        limit: int = Query(default=20, ge=1, le=50),
    ):
        return await runtime(request).extensions.page(str(agent_id), after, limit)

    @router.get("/agents/{agent_id}/extensions/{extension_id}")
    async def extension_detail(agent_id: uuid.UUID, extension_id: uuid.UUID, request: Request):
        return await runtime(request).extensions.get(str(agent_id), str(extension_id))

    @router.get("/agents/{agent_id}/extensions/{extension_id}/actions")
    async def extension_actions(
        agent_id: uuid.UUID,
        extension_id: uuid.UUID,
        request: Request,
        after: uuid.UUID | None = None,
        limit: int = Query(default=20, ge=1, le=50),
    ):
        return await runtime(request).extensions.actions(
            str(agent_id), str(extension_id), str(after) if after else "", limit
        )

    @router.put("/agents/{agent_id}/extensions/{extension_id}/authorization")
    async def extension_authorization(agent_id: uuid.UUID, extension_id: uuid.UUID, request: Request):
        return await runtime(request).extensions.authorize(
            str(agent_id), str(extension_id), await bounded_configuration(request, 8192)
        )

    @router.post("/agents/{agent_id}/extensions/{extension_id}/disable")
    async def extension_disable(agent_id: uuid.UUID, extension_id: uuid.UUID, request: Request):
        data = await bounded_configuration(request, 2048)
        if set(data) != {"revision"} or type(data.get("revision")) is not int or cast(int, data["revision"]) < 1:
            raise BusinessError("extension_invalid", "Use the observed plugin revision", status=422)
        return await runtime(request).extensions.disable(str(agent_id), str(extension_id), cast(int, data["revision"]))

    return router
