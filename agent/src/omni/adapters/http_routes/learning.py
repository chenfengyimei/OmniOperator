"""Owner review for reflection journals and post-turn proposals."""

from __future__ import annotations

import uuid
from typing import cast

from fastapi import APIRouter, Query, Request

from ...domain.contracts import BusinessError
from . import RuntimeLookup, bounded_configuration


def learning_router(runtime: RuntimeLookup) -> APIRouter:
    router = APIRouter()

    @router.get("/agents/{agent_id}/reflections")
    async def reflections(
        agent_id: uuid.UUID,
        request: Request,
        after: str = "",
        limit: int = Query(20, ge=1, le=50),
        approved: bool = False,
    ):
        return await runtime(request).reflections.page(str(agent_id), after, limit, approved)

    @router.get("/agents/{agent_id}/reflections/{journal_id}")
    async def reflection_detail(agent_id: uuid.UUID, journal_id: uuid.UUID, request: Request):
        return await runtime(request).reflections.get(str(agent_id), str(journal_id))

    @router.put("/agents/{agent_id}/reflections/{journal_id}")
    async def reflection_edit(agent_id: uuid.UUID, journal_id: uuid.UUID, request: Request):
        return await runtime(request).reflections.edit(
            str(agent_id), str(journal_id), await bounded_configuration(request, 480000)
        )

    @router.post("/agents/{agent_id}/reflections/candidates/{candidate_id}/decision")
    async def reflection_decision(agent_id: uuid.UUID, candidate_id: uuid.UUID, request: Request):
        return await runtime(request).reflections.decide(
            str(agent_id), str(candidate_id), await bounded_configuration(request, 2048)
        )

    @router.post("/agents/{agent_id}/memory/reviews", status_code=202)
    async def background_review_create(agent_id: uuid.UUID, request: Request):
        return await runtime(request).background_review.submit(
            str(agent_id), await bounded_configuration(request, 2048)
        )

    @router.get("/agents/{agent_id}/memory/reviews")
    async def background_reviews(
        agent_id: uuid.UUID,
        request: Request,
        after: int = Query(default=0, ge=0),
        limit: int = Query(default=20, ge=1, le=50),
    ):
        return await runtime(request).background_review.page(str(agent_id), after, limit)

    @router.get("/agents/{agent_id}/memory/reviews/{batch_id}")
    async def background_review_detail(agent_id: uuid.UUID, batch_id: uuid.UUID, request: Request):
        return await runtime(request).background_review.get(str(agent_id), str(batch_id))

    @router.post("/agents/{agent_id}/memory/reviews/{batch_id}/cancel")
    async def background_review_cancel(agent_id: uuid.UUID, batch_id: uuid.UUID, request: Request):
        data = await bounded_configuration(request, 2048)
        if set(data) != {"revision"} or type(data.get("revision")) is not int or cast(int, data["revision"]) < 1:
            raise BusinessError("background_review_invalid", "A positive cancellation revision is required", status=422)
        return await runtime(request).background_review.cancel(
            str(agent_id), str(batch_id), cast(int, data["revision"])
        )

    @router.post("/agents/{agent_id}/memory/reviews/candidates/{candidate_id}/decision")
    async def background_review_decision(agent_id: uuid.UUID, candidate_id: uuid.UUID, request: Request):
        return await runtime(request).background_review.decide(
            str(agent_id), str(candidate_id), await bounded_configuration(request, 2048)
        )

    return router
