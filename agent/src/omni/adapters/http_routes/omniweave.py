"""Expose Ticket progress for the owning Web main conversation."""

from __future__ import annotations

from uuid import UUID

from fastapi import APIRouter, Request

from . import RuntimeLookup


def omniweave_router(runtime: RuntimeLookup) -> APIRouter:
    router = APIRouter(prefix="/agents/{agent_id}/omniweave")

    @router.get("/sessions/{session_id}/tickets")
    async def ticket(
        agent_id: UUID, session_id: UUID, request: Request, ticket_id: UUID | None = None, run_id: UUID | None = None
    ):
        return await runtime(request).omniweave.public_ticket(
            str(agent_id), str(session_id), str(ticket_id) if ticket_id else "", str(run_id) if run_id else ""
        )

    return router
