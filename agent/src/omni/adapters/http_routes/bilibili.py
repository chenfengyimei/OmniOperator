"""Owner APIs for Bilibili profile revisions, reads and publication drafts."""

from __future__ import annotations

import uuid
from collections.abc import Callable, Mapping
from typing import Literal, cast

from fastapi import APIRouter, Query, Request
from pydantic import Field

from ...application.bilibili import BilibiliService
from ...domain.contracts import BusinessError
from . import RuntimeLookup, StrictInput, bounded_configuration, configuration_input

Payload = dict[str, object]


class BilibiliProfileInput(StrictInput):
    enabled: bool = Field(strict=True)
    headless: bool = Field(default=True, strict=True)
    forms: Payload
    revision: int = Field(ge=0, strict=True)


class BilibiliReadInput(StrictInput):
    config_id: uuid.UUID
    operation: Literal[
        "account",
        "video",
        "comments",
        "replies",
        "search",
        "whois",
        "relation",
        "dynamic_list",
        "danmaku",
        "popular",
        "ranking",
        "following",
        "favorites",
        "fav_resources",
        "tags",
        "history",
        "msgfeed",
    ]
    target: str = Field(default="", max_length=100)
    limit: int = Field(default=10, ge=1, le=20, strict=True)


class BilibiliPublicationInput(StrictInput):
    config_id: uuid.UUID
    message_id: uuid.UUID
    kind: Literal["comment", "private_message"]
    target: str = Field(max_length=500)
    mentions: list[Payload] = Field(default_factory=cast(Callable[[], list[Payload]], list), max_length=10)
    key: str = Field(min_length=1, max_length=180)


def bilibili_router(runtime: RuntimeLookup) -> APIRouter:
    router = APIRouter(prefix="/agents/{agent_id}/bilibili")

    def bilibili_service(request: Request) -> BilibiliService:
        service = runtime(request).feature_services.get("bilibili")
        if not isinstance(service, BilibiliService):
            raise BusinessError("bilibili_unavailable", "Bilibili capability is unavailable", status=503)
        return service

    @router.get("/profiles/{config_id}")
    async def bilibili_profile(agent_id: uuid.UUID, config_id: uuid.UUID, request: Request):
        return await bilibili_service(request).profile(str(agent_id), str(config_id))

    @router.put("/profiles/{config_id}")
    async def bilibili_profile_save(agent_id: uuid.UUID, config_id: uuid.UUID, request: Request):
        data = configuration_input(BilibiliProfileInput, await bounded_configuration(request, 65536, strict=True))
        return await bilibili_service(request).save_profile(
            str(agent_id), str(config_id), data.enabled, data.forms, data.revision, data.headless
        )

    @router.get("/risk/{config_id}")
    async def bilibili_risk_state(agent_id: uuid.UUID, config_id: uuid.UUID, request: Request):
        return await bilibili_service(request).risk_state(str(agent_id), str(config_id))

    @router.post("/risk/{config_id}/resume")
    async def bilibili_risk_resume(agent_id: uuid.UUID, config_id: uuid.UUID, request: Request):
        return await bilibili_service(request).resume(str(agent_id), str(config_id))

    @router.post("/read")
    async def bilibili_read(agent_id: uuid.UUID, request: Request):
        data = configuration_input(BilibiliReadInput, await bounded_configuration(request, 4096, strict=True))
        return await bilibili_service(request).read(
            str(agent_id), str(data.config_id), data.operation, data.target, data.limit
        )

    @router.post("/publications", status_code=201)
    async def bilibili_prepare(agent_id: uuid.UUID, request: Request):
        data = configuration_input(BilibiliPublicationInput, await bounded_configuration(request, 16384, strict=True))
        return await bilibili_service(request).prepare(
            str(agent_id),
            str(data.message_id),
            str(data.config_id),
            data.kind,
            data.target,
            cast(list[Mapping[str, object]], data.mentions),
            data.key,
        )

    @router.get("/publications")
    async def bilibili_publications(
        agent_id: uuid.UUID,
        request: Request,
        after: uuid.UUID | None = None,
        limit: int = Query(default=50, ge=1, le=100),
    ):
        return await bilibili_service(request).page(str(agent_id), str(after) if after else "", limit)

    @router.get("/publications/{identity}")
    async def bilibili_publication(agent_id: uuid.UUID, identity: uuid.UUID, request: Request):
        return await bilibili_service(request).get(str(agent_id), str(identity))

    return router
