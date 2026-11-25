"""Owner APIs for QQ channel status and consenting peer bindings."""

from __future__ import annotations

import uuid
from typing import Literal

from fastapi import APIRouter, Query, Request
from pydantic import Field

from ...application.qq import QQService
from ...domain.contracts import BusinessError
from . import RuntimeLookup, StrictInput


class QQBindingInput(StrictInput):
    config_id: uuid.UUID
    scene: Literal["private", "group"]
    peer_id: str = Field(min_length=1, max_length=19)
    enabled: bool = Field(strict=True)
    persona: str = Field(min_length=1, max_length=8000)
    cooldown_s: int = Field(ge=1, le=3600, strict=True)
    revision: int = Field(default=0, ge=0, strict=True)


def qq_router(runtime: RuntimeLookup) -> APIRouter:
    router = APIRouter(prefix="/agents/{agent_id}/qq")

    def qq_service(request: Request) -> QQService:
        service = runtime(request).qq
        if service is None:
            raise BusinessError("qq_unavailable", "QQ channel is unavailable", status=503)
        return service

    @router.get("/bindings")
    async def qq_bindings(
        agent_id: uuid.UUID,
        request: Request,
        after: uuid.UUID | None = None,
        limit: int = Query(default=50, ge=1, le=100),
    ):
        return await qq_service(request).page(str(agent_id), str(after) if after else "", limit)

    @router.get("/status")
    async def qq_status(agent_id: uuid.UUID, request: Request):
        return await qq_service(request).status(str(agent_id))

    @router.post("/bindings", status_code=201)
    async def qq_binding_create(agent_id: uuid.UUID, data: QQBindingInput, request: Request):
        if data.revision != 0:
            raise BusinessError("revision_conflict", "New QQ grants start at revision zero", status=422)
        return await qq_service(request).save(
            str(agent_id), str(data.config_id), data.model_dump(exclude={"config_id", "revision"})
        )

    @router.put("/bindings/{binding_id}")
    async def qq_binding_edit(agent_id: uuid.UUID, binding_id: uuid.UUID, data: QQBindingInput, request: Request):
        return await qq_service(request).save(
            str(agent_id),
            str(data.config_id),
            data.model_dump(exclude={"config_id", "revision"}),
            str(binding_id),
            data.revision,
        )

    return router
