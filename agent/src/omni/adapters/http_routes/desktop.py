"""Owner desktop views, window sharing, control consent and approved actions."""

from __future__ import annotations

import uuid
from collections.abc import Mapping

from fastapi import APIRouter, Request

from ...application.desktop_services import DesktopServices
from ...domain.contracts import BusinessError
from . import RuntimeLookup, bounded_configuration


def require_desktop_services(services: Mapping[str, object]) -> DesktopServices:
    service = services.get("desktop")
    if not isinstance(service, DesktopServices):
        raise BusinessError("desktop_unavailable", "Desktop service is unavailable", status=503)
    return service


def desktop_router(runtime: RuntimeLookup) -> APIRouter:
    router = APIRouter(prefix="/agents/{agent_id}/desktop")

    def desktop_services(request: Request) -> DesktopServices:
        return require_desktop_services(runtime(request).feature_services)

    @router.get("")
    async def desktop_status(agent_id: uuid.UUID, request: Request):
        return await desktop_services(request).observations.status(str(agent_id))

    @router.post("/view")
    async def desktop_begin(agent_id: uuid.UUID, request: Request):
        return await desktop_services(request).observations.begin(
            str(agent_id), await bounded_configuration(request, 512)
        )

    @router.post("/probe")
    async def desktop_probe(agent_id: uuid.UUID, request: Request):
        return await desktop_services(request).observations.probe(
            str(agent_id), await bounded_configuration(request, 512)
        )

    @router.post("/targets")
    async def desktop_targets(agent_id: uuid.UUID, request: Request):
        return await desktop_services(request).observations.targets(
            str(agent_id), await bounded_configuration(request, 512)
        )

    @router.post("/verify")
    async def desktop_verify(agent_id: uuid.UUID, request: Request):
        return await desktop_services(request).observations.verify(
            str(agent_id), await bounded_configuration(request, 512)
        )

    @router.post("/observe")
    async def desktop_observe(agent_id: uuid.UUID, request: Request):
        return await desktop_services(request).observations.observe(
            str(agent_id), await bounded_configuration(request, 512)
        )

    @router.post("/capture")
    async def desktop_capture(agent_id: uuid.UUID, request: Request):
        return await desktop_services(request).observations.capture(
            str(agent_id), await bounded_configuration(request, 512)
        )

    @router.post("/portal", status_code=202)
    async def desktop_portal_open(agent_id: uuid.UUID, request: Request):
        return await desktop_services(request).portal.open(str(agent_id), await bounded_configuration(request, 512))

    @router.get("/portal")
    async def desktop_portal_status(agent_id: uuid.UUID, view_id: uuid.UUID, request: Request):
        return await desktop_services(request).portal.status(str(agent_id), str(view_id))

    @router.post("/portal/capture")
    async def desktop_portal_capture(agent_id: uuid.UUID, request: Request):
        return await desktop_services(request).portal.capture(str(agent_id), await bounded_configuration(request, 512))

    @router.delete("/portal")
    async def desktop_portal_close(agent_id: uuid.UUID, view_id: uuid.UUID, grant_id: uuid.UUID, request: Request):
        return await desktop_services(request).portal.close_grant(str(agent_id), str(view_id), str(grant_id))

    @router.post("/control", status_code=202)
    async def desktop_control_open(agent_id: uuid.UUID, request: Request):
        return await desktop_services(request).control.open(str(agent_id), await bounded_configuration(request, 512))

    @router.get("/control")
    async def desktop_control_status(agent_id: uuid.UUID, view_id: uuid.UUID, request: Request):
        return await desktop_services(request).control.status(str(agent_id), str(view_id))

    @router.delete("/control")
    async def desktop_control_close(agent_id: uuid.UUID, view_id: uuid.UUID, grant_id: uuid.UUID, request: Request):
        return await desktop_services(request).control.close_grant(str(agent_id), str(view_id), str(grant_id))

    @router.post("/actions", status_code=202)
    async def desktop_action(agent_id: uuid.UUID, request: Request):
        receipt = await desktop_services(request).actions.submit(
            str(agent_id), await bounded_configuration(request, 4096)
        )
        runtime(request).application.notify()
        return receipt

    @router.delete("/snapshot")
    async def desktop_forget(agent_id: uuid.UUID, view_id: uuid.UUID, request: Request):
        return await desktop_services(request).observations.forget(str(agent_id), str(view_id))

    return router
