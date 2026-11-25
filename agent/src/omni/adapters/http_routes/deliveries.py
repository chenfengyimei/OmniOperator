"""Owner delivery queues, approval, cancellation and unknown-effect review."""

from __future__ import annotations

import uuid
from typing import Literal

from fastapi import APIRouter, Query, Request
from pydantic import Field

from . import RuntimeLookup, StrictInput, bounded_configuration, configuration_input


class DeliveryInput(StrictInput):
    message_id: uuid.UUID
    config_id: uuid.UUID
    target: str = Field(min_length=1, max_length=512)
    key: str = Field(min_length=1, max_length=200)
    parent_id: uuid.UUID | None = None
    acknowledge_duplicate: bool = Field(default=False, strict=True)


class DeliveryDecision(StrictInput):
    allowed: bool = Field(strict=True)
    revision: int = Field(ge=1, strict=True)
    fingerprint: str = Field(pattern=r"^[a-f0-9]{64}$")


class DeliveryReview(StrictInput):
    revision: int = Field(ge=1, strict=True)
    fingerprint: str = Field(pattern=r"^[a-f0-9]{64}$")
    decision: Literal["confirmed_sent", "confirmed_not_sent", "allow_future"]
    evidence: str = Field(min_length=1, max_length=2000)


def deliveries_router(runtime: RuntimeLookup) -> APIRouter:
    router = APIRouter(prefix="/agents/{agent_id}/deliveries")

    @router.get("/channels")
    async def delivery_channels(
        agent_id: uuid.UUID,
        request: Request,
        after: uuid.UUID | None = None,
        limit: int = Query(default=100, ge=1, le=200),
    ):
        return await runtime(request).deliveries.channels(str(agent_id), str(after) if after else "", limit)

    @router.get("/capabilities")
    async def delivery_capabilities(agent_id: uuid.UUID, request: Request):
        return await runtime(request).deliveries.capabilities(str(agent_id))

    @router.get("")
    async def deliveries(
        agent_id: uuid.UUID,
        request: Request,
        after: int = Query(default=0, ge=0),
        limit: int = Query(default=100, ge=1, le=200),
        include_archived: bool = False,
    ):
        rows = await runtime(request).deliveries.page(str(agent_id), after, limit, include_archived)
        return {"items": rows, "next_after": rows[-1]["queue_sequence"] if len(rows) == limit else None}

    @router.post("", status_code=201)
    async def create_delivery(agent_id: uuid.UUID, request: Request):
        data = configuration_input(DeliveryInput, await bounded_configuration(request, 8192))
        return await runtime(request).deliveries.create(
            str(agent_id),
            str(data.message_id),
            str(data.config_id),
            data.target,
            data.key,
            str(data.parent_id) if data.parent_id else None,
            data.acknowledge_duplicate,
        )

    @router.get("/{delivery_id}")
    async def delivery(agent_id: uuid.UUID, delivery_id: uuid.UUID, request: Request):
        return await runtime(request).deliveries.get(str(agent_id), str(delivery_id))

    @router.get("/{delivery_id}/attempts")
    async def delivery_attempts(
        agent_id: uuid.UUID,
        delivery_id: uuid.UUID,
        request: Request,
        after: int = Query(default=0, ge=0),
        limit: int = Query(default=50, ge=1, le=200),
    ):
        rows = await runtime(request).deliveries.attempts(str(agent_id), str(delivery_id), after, limit)
        return {"items": rows, "next_after": rows[-1]["seq"] if len(rows) == limit else None}

    @router.post("/{delivery_id}/decision")
    async def decide_delivery(agent_id: uuid.UUID, delivery_id: uuid.UUID, request: Request):
        data = configuration_input(DeliveryDecision, await bounded_configuration(request, 2048))
        return await runtime(request).deliveries.decide(
            str(agent_id), str(delivery_id), data.allowed, data.revision, data.fingerprint
        )

    @router.post("/{delivery_id}/cancel")
    async def cancel_delivery(agent_id: uuid.UUID, delivery_id: uuid.UUID, request: Request):
        return await runtime(request).deliveries.cancel(str(agent_id), str(delivery_id))

    @router.post("/{delivery_id}/review")
    async def review_delivery(agent_id: uuid.UUID, delivery_id: uuid.UUID, request: Request):
        data = configuration_input(DeliveryReview, await bounded_configuration(request, 16384))
        return await runtime(request).deliveries.review(
            str(agent_id), str(delivery_id), data.revision, data.fingerprint, data.decision, data.evidence
        )

    @router.delete("/{delivery_id}")
    async def archive_delivery(
        agent_id: uuid.UUID, delivery_id: uuid.UUID, request: Request, revision: int = Query(ge=1)
    ):
        await runtime(request).deliveries.archive(str(agent_id), str(delivery_id), revision)
        return {"ok": True}

    return router
