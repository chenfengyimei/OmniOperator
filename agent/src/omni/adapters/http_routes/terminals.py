"""Owner terminal control, operation receipts and review history."""

from __future__ import annotations

import uuid

from fastapi import APIRouter, Query, Request

from ...domain.contracts import BusinessError
from . import RuntimeLookup, bounded_configuration


def terminals_router(runtime: RuntimeLookup) -> APIRouter:
    router = APIRouter()

    @router.get("/agents/{agent_id}/terminal-reviews")
    async def terminal_review_list(
        agent_id: uuid.UUID, request: Request, after: str = "", limit: int = Query(20, ge=1, le=50)
    ):
        return await runtime(request).terminal_reviews.page(str(agent_id), after, limit)

    @router.get("/agents/{agent_id}/terminal-reviews/{operation_id}")
    async def terminal_review_detail(agent_id: uuid.UUID, operation_id: uuid.UUID, request: Request):
        return await runtime(request).terminal_reviews.get(str(agent_id), str(operation_id))

    @router.get("/agents/{agent_id}/terminal-reviews/{operation_id}/history")
    async def terminal_review_history(
        agent_id: uuid.UUID,
        operation_id: uuid.UUID,
        request: Request,
        after: int = Query(0, ge=0),
        limit: int = Query(20, ge=1, le=50),
    ):
        return await runtime(request).terminal_reviews.history(str(agent_id), str(operation_id), after, limit)

    @router.post("/agents/{agent_id}/terminal-reviews/{operation_id}", status_code=201)
    async def terminal_review_record(agent_id: uuid.UUID, operation_id: uuid.UUID, request: Request):
        return await runtime(request).terminal_reviews.record(
            str(agent_id), str(operation_id), await bounded_configuration(request, 32768)
        )

    @router.delete("/agents/{agent_id}/terminal-reviews/{operation_id}/history/{review_id}/evidence")
    async def terminal_review_erase_evidence(
        agent_id: uuid.UUID, operation_id: uuid.UUID, review_id: uuid.UUID, request: Request
    ):
        if await bounded_configuration(request, 128) != {}:
            raise BusinessError("terminal_review_invalid", "Evidence erasure requires an empty object", status=422)
        return await runtime(request).terminal_reviews.erase_evidence(str(agent_id), str(operation_id), str(review_id))

    @router.get("/agents/{agent_id}/terminals/options")
    async def terminal_options(agent_id: uuid.UUID, request: Request):
        return await runtime(request).terminals.options(str(agent_id))

    @router.get("/agents/{agent_id}/terminals")
    async def terminal_list(
        agent_id: uuid.UUID,
        request: Request,
        after: int = Query(default=0, ge=0, le=9223372036854775807),
        limit: int = Query(default=20, ge=1, le=50),
    ):
        return await runtime(request).terminals.page(str(agent_id), after, limit)

    @router.post("/agents/{agent_id}/terminals", status_code=201)
    async def terminal_open(agent_id: uuid.UUID, request: Request):
        return await runtime(request).terminals.open(str(agent_id), await bounded_configuration(request, 4096))

    @router.get("/agents/{agent_id}/terminal-operations/{operation_id}")
    async def terminal_lookup(agent_id: uuid.UUID, operation_id: uuid.UUID, request: Request):
        return await runtime(request).terminals.lookup(str(agent_id), str(operation_id))

    @router.get("/agents/{agent_id}/terminals/{terminal_id}")
    async def terminal_detail(agent_id: uuid.UUID, terminal_id: uuid.UUID, request: Request):
        return await runtime(request).terminals.get(str(agent_id), str(terminal_id))

    @router.get("/agents/{agent_id}/terminals/{terminal_id}/operations/{operation_id}")
    async def terminal_receipt(agent_id: uuid.UUID, terminal_id: uuid.UUID, operation_id: uuid.UUID, request: Request):
        return await runtime(request).terminals.receipt(str(agent_id), str(terminal_id), str(operation_id))

    @router.post("/agents/{agent_id}/terminals/{terminal_id}/attach")
    async def terminal_attach(agent_id: uuid.UUID, terminal_id: uuid.UUID, request: Request):
        if await bounded_configuration(request, 1024) != {}:
            raise BusinessError("terminal_invalid", "Attach requires an empty object", status=422)
        return await runtime(request).terminals.attach(str(agent_id), str(terminal_id))

    @router.post("/agents/{agent_id}/terminals/{terminal_id}/detach")
    async def terminal_detach(agent_id: uuid.UUID, terminal_id: uuid.UUID, request: Request):
        data = await bounded_configuration(request, 1024)
        if set(data) != {"connection_id"} or not isinstance(data["connection_id"], str):
            raise BusinessError("terminal_invalid", "Detach requires its captured connection identity", status=422)
        return await runtime(request).terminals.detach(str(agent_id), str(terminal_id), data["connection_id"])

    @router.get("/agents/{agent_id}/terminals/{terminal_id}/output")
    async def terminal_output(
        agent_id: uuid.UUID,
        terminal_id: uuid.UUID,
        request: Request,
        connection_id: uuid.UUID,
        stream_id: uuid.UUID,
        after: int = Query(default=0, ge=0, le=9223372036854775807),
    ):
        return await runtime(request).terminals.output(
            str(agent_id), str(terminal_id), str(connection_id), str(stream_id), after
        )

    @router.post("/agents/{agent_id}/terminals/{terminal_id}/input")
    async def terminal_input(agent_id: uuid.UUID, terminal_id: uuid.UUID, request: Request):
        return await runtime(request).terminals.input(
            str(agent_id), str(terminal_id), await bounded_configuration(request, 65536)
        )

    @router.post("/agents/{agent_id}/terminals/{terminal_id}/resize")
    async def terminal_resize(agent_id: uuid.UUID, terminal_id: uuid.UUID, request: Request):
        return await runtime(request).terminals.resize(
            str(agent_id), str(terminal_id), await bounded_configuration(request, 2048)
        )

    @router.post("/agents/{agent_id}/terminals/{terminal_id}/close")
    async def terminal_close(agent_id: uuid.UUID, terminal_id: uuid.UUID, request: Request):
        return await runtime(request).terminals.end(
            str(agent_id), str(terminal_id), await bounded_configuration(request, 2048)
        )

    return router
