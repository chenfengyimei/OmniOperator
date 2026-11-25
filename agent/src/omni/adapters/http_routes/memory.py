"""Owner memory search, organization and evidence-aware changes."""

from __future__ import annotations

import uuid
from typing import cast

from fastapi import APIRouter, Query, Request
from pydantic import Field

from ...application.memory_semantics import MemorySemanticService
from ...domain.contracts import BusinessError
from . import RuntimeLookup, StrictInput, bounded_configuration


class MemoryInput(StrictInput):
    content: str = Field(min_length=1, max_length=20_000)
    message_id: uuid.UUID | None = None
    quote: str = Field(default="", max_length=20_000)
    action_id: uuid.UUID | None = None
    document_revision_id: uuid.UUID | None = None


def memory_router(runtime: RuntimeLookup) -> APIRouter:
    router = APIRouter(prefix="/agents/{agent_id}/memory")

    @router.get("")
    async def memories(agent_id: uuid.UUID, request: Request, q: str = Query(default="", max_length=500)):
        semantic = runtime(request).memory_semantics
        if q and semantic is not None:
            return await semantic.search(str(agent_id), q)
        application = runtime(request).application
        return {
            "items": await application.search_memory(str(agent_id), q)
            if q
            else await application.query("memory", str(agent_id))
        }

    @router.get("/decay")
    async def memory_decay_state(agent_id: uuid.UUID, request: Request):
        return await runtime(request).application.memory_decay_state(str(agent_id))

    @router.post("/decay")
    async def memory_decay(agent_id: uuid.UUID, request: Request):
        data = await bounded_configuration(request, 2048, strict=True)
        if set(data) - {"force"} or ("force" in data and type(data["force"]) is not bool):
            raise BusinessError("memory_decay_invalid", "Only an explicit boolean force is accepted", status=422)
        return await runtime(request).application.memory_decay(str(agent_id), bool(data.get("force", False)))

    def memory_semantic_service(request: Request) -> MemorySemanticService:
        service = runtime(request).memory_semantics
        if service is None:
            raise BusinessError("memory_semantics_unavailable", "Memory semantic service is unavailable", status=503)
        return service

    @router.get("/semantics")
    async def semantic_batches(
        agent_id: uuid.UUID, request: Request, after: str = "", limit: int = Query(default=20, ge=1, le=32)
    ):
        return await memory_semantic_service(request).page(str(agent_id), after, limit)

    @router.post("/index")
    async def memory_index(
        agent_id: uuid.UUID, request: Request, after: str = "", limit: int = Query(default=32, ge=1, le=32)
    ):
        return await memory_semantic_service(request).index(str(agent_id), after, limit)

    @router.post("/semantics", status_code=201)
    async def semantic_propose(agent_id: uuid.UUID, request: Request):
        return await memory_semantic_service(request).propose(str(agent_id), await bounded_configuration(request, 8192))

    @router.get("/semantics/{batch_id}")
    async def semantic_detail(agent_id: uuid.UUID, batch_id: uuid.UUID, request: Request):
        return await memory_semantic_service(request).get(str(agent_id), str(batch_id))

    @router.post("/semantic-links/{link_id}/decision")
    async def semantic_decide(agent_id: uuid.UUID, link_id: uuid.UUID, request: Request):
        return await memory_semantic_service(request).decide(
            str(agent_id), str(link_id), await bounded_configuration(request, 2048)
        )

    @router.post("/organizations", status_code=202)
    async def memory_organization_create(agent_id: uuid.UUID, request: Request):
        return await runtime(request).memory_organization.submit(
            str(agent_id), await bounded_configuration(request, 8192)
        )

    @router.get("/organizations")
    async def memory_organizations(
        agent_id: uuid.UUID,
        request: Request,
        after: int = Query(default=0, ge=0),
        limit: int = Query(default=20, ge=1, le=50),
    ):
        return await runtime(request).memory_organization.page(str(agent_id), after, limit)

    @router.get("/organizations/{batch_id}")
    async def memory_organization_detail(agent_id: uuid.UUID, batch_id: uuid.UUID, request: Request):
        return await runtime(request).memory_organization.get(str(agent_id), str(batch_id))

    @router.post("/organizations/{batch_id}/cancel")
    async def memory_organization_cancel(agent_id: uuid.UUID, batch_id: uuid.UUID, request: Request):
        data = await bounded_configuration(request, 2048)
        if set(data) != {"revision"} or type(data.get("revision")) is not int or cast(int, data["revision"]) < 1:
            raise BusinessError(
                "memory_organization_invalid", "A positive cancellation revision is required", status=422
            )
        return await runtime(request).memory_organization.cancel(
            str(agent_id), str(batch_id), cast(int, data["revision"])
        )

    @router.post("/organizations/candidates/{candidate_id}/decision")
    async def memory_organization_decision(agent_id: uuid.UUID, candidate_id: uuid.UUID, request: Request):
        return await runtime(request).memory_organization.decide(
            str(agent_id), str(candidate_id), await bounded_configuration(request, 2048)
        )

    @router.post("", status_code=201)
    async def remember(agent_id: uuid.UUID, data: MemoryInput, request: Request):
        return await runtime(request).application.remember(
            str(agent_id),
            data.content,
            message_id=str(data.message_id) if data.message_id else None,
            quote=data.quote,
            action_id=str(data.action_id) if data.action_id else None,
            document_revision_id=str(data.document_revision_id) if data.document_revision_id else None,
        )

    @router.delete("/{memory_id}")
    async def forget(agent_id: uuid.UUID, memory_id: uuid.UUID, request: Request, revision: int = Query(ge=1)):
        await runtime(request).application.forget(str(agent_id), str(memory_id), revision)
        return {"deleted": True}

    return router
