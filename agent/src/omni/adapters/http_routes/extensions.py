"""Owner extension market, installation and version-bound authorization."""

from __future__ import annotations

import uuid
from collections.abc import Mapping
from typing import cast

from fastapi import APIRouter, Query, Request

from ...application.extensions import ExtensionService
from ...domain.contracts import BusinessError
from . import RuntimeLookup, bounded_configuration


def require_extension_service(services: Mapping[str, object]) -> ExtensionService:
    service = services.get("extensions")
    if not isinstance(service, ExtensionService):
        raise BusinessError("extensions_unavailable", "Extension service is unavailable", status=503)
    return service


def extensions_router(runtime: RuntimeLookup) -> APIRouter:
    router = APIRouter(prefix="/agents/{agent_id}/extensions")

    def service(request: Request) -> ExtensionService:
        return require_extension_service(runtime(request).feature_services)

    @router.post("", status_code=201)
    async def extension_install(agent_id: uuid.UUID, request: Request):
        return await service(request).install(str(agent_id), await bounded_configuration(request, 8192))

    @router.post("/market")
    async def extension_market(agent_id: uuid.UUID, request: Request):
        data = await bounded_configuration(request, 4096)
        if (
            not isinstance(cast(object, data), dict)
            or set(data) != {"source"}
            or not isinstance(data["source"], str)
            or not 1 <= len(data["source"]) <= 4000
        ):
            raise BusinessError("extension_invalid", "A bounded local market directory is required", status=422)
        return await service(request).market(str(agent_id), data["source"])

    @router.get("")
    async def extension_list(
        agent_id: uuid.UUID,
        request: Request,
        after: int = Query(default=0, ge=0),
        limit: int = Query(default=20, ge=1, le=50),
    ):
        return await service(request).page(str(agent_id), after, limit)

    @router.get("/{extension_id}")
    async def extension_detail(agent_id: uuid.UUID, extension_id: uuid.UUID, request: Request):
        return await service(request).get(str(agent_id), str(extension_id))

    @router.get("/{extension_id}/actions")
    async def extension_actions(
        agent_id: uuid.UUID,
        extension_id: uuid.UUID,
        request: Request,
        after: uuid.UUID | None = None,
        limit: int = Query(default=20, ge=1, le=50),
    ):
        return await service(request).actions(str(agent_id), str(extension_id), str(after) if after else "", limit)

    @router.put("/{extension_id}/authorization")
    async def extension_authorization(agent_id: uuid.UUID, extension_id: uuid.UUID, request: Request):
        return await service(request).authorize(
            str(agent_id), str(extension_id), await bounded_configuration(request, 8192)
        )

    @router.post("/{extension_id}/disable")
    async def extension_disable(agent_id: uuid.UUID, extension_id: uuid.UUID, request: Request):
        data = await bounded_configuration(request, 2048)
        if set(data) != {"revision"} or type(data.get("revision")) is not int or cast(int, data["revision"]) < 1:
            raise BusinessError("extension_invalid", "Use the observed plugin revision", status=422)
        return await service(request).disable(str(agent_id), str(extension_id), cast(int, data["revision"]))

    return router
