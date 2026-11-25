"""Owner review for actions that require durable effect verification."""

from __future__ import annotations

import uuid

from fastapi import APIRouter, Query, Request

from . import RuntimeLookup, bounded_configuration


def action_reviews_router(runtime: RuntimeLookup) -> APIRouter:
    router = APIRouter()

    @router.get("/agents/{agent_id}/action-reviews")
    async def action_review_list(
        agent_id: uuid.UUID,
        request: Request,
        after: int = Query(default=0, ge=0, le=9223372036854775807),
        limit: int = Query(default=20, ge=1, le=50),
    ):
        return await runtime(request).action_reviews.page(str(agent_id), after, limit)

    @router.get("/agents/{agent_id}/actions/{action_id}/review")
    async def action_review_detail(agent_id: uuid.UUID, action_id: uuid.UUID, request: Request):
        return await runtime(request).action_reviews.get(str(agent_id), str(action_id))

    @router.get("/agents/{agent_id}/actions/{action_id}/reviews")
    async def action_review_history(
        agent_id: uuid.UUID,
        action_id: uuid.UUID,
        request: Request,
        after: int = Query(default=0, ge=0, le=9223372036854775807),
        limit: int = Query(default=20, ge=1, le=50),
    ):
        return await runtime(request).action_reviews.history(str(agent_id), str(action_id), after, limit)

    @router.post("/agents/{agent_id}/actions/{action_id}/review")
    async def action_review_commit(agent_id: uuid.UUID, action_id: uuid.UUID, request: Request):
        return await runtime(request).action_reviews.review(
            str(agent_id), str(action_id), await bounded_configuration(request, 16384)
        )

    return router
