"""Expose Ticket progress for the owning Web main conversation."""

from __future__ import annotations

from uuid import UUID

from fastapi import APIRouter, Request

from . import RuntimeLookup, StrictInput


class MainSessionInput(StrictInput):
    session_id: UUID | None = None


def omniweave_router(runtime: RuntimeLookup) -> APIRouter:
    router = APIRouter(prefix="/agents/{agent_id}/omniweave")

    @router.get("/main-session")
    async def main_session(agent_id: UUID, request: Request):
        return await runtime(request).omniweave.main_session(str(agent_id))

    @router.put("/main-session")
    async def select_main_session(agent_id: UUID, data: MainSessionInput, request: Request):
        return await runtime(request).omniweave.select_main_session(
            str(agent_id),
            str(data.session_id) if data.session_id else None,
            request.headers.get("authorization", "")[7:],
        )

    @router.get("/sessions/{session_id}/tickets")
    async def ticket(
        agent_id: UUID, session_id: UUID, request: Request, ticket_id: UUID | None = None, run_id: UUID | None = None
    ):
        return await runtime(request).omniweave.public_ticket(
            str(agent_id), str(session_id), str(ticket_id) if ticket_id else "", str(run_id) if run_id else ""
        )

    return router
