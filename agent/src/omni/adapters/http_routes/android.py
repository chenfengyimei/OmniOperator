"""Owner Android grants, observations and approved actions."""

from __future__ import annotations

import uuid
from typing import cast

from fastapi import APIRouter, Query, Request

from ...application.android import AndroidService
from ...application.ports import Record
from ...domain.contracts import BusinessError
from . import RuntimeLookup, bounded_configuration


def android_router(runtime: RuntimeLookup) -> APIRouter:
    router = APIRouter(prefix="/agents/{agent_id}/android")

    def android_service(request: Request) -> AndroidService:
        service = runtime(request).feature_services.get("android")
        if not isinstance(service, AndroidService):
            raise BusinessError("android_unavailable", "Android service is unavailable", status=503)
        return service

    async def android_input(request: Request, fields: set[str]) -> Record:
        value = await bounded_configuration(request, 4096)
        if not isinstance(cast(object, value), dict) or set(value) != fields:
            raise BusinessError("android_invalid", "Exact Android request fields are required", status=422)
        return value

    @router.get("/grants")
    async def android_grants(
        agent_id: uuid.UUID, request: Request, after: str = "", limit: int = Query(50, ge=1, le=100)
    ):
        return await android_service(request).page(str(agent_id), after, limit)

    @router.post("/probe")
    async def android_probe(agent_id: uuid.UUID, request: Request):
        value = await android_input(request, {"config_id", "consent"})
        return await android_service(request).probe(
            str(agent_id), str(value["config_id"]), cast(bool, value["consent"])
        )

    @router.get("/grants/{grant_id}/unicode")
    async def android_unicode_profile(agent_id: uuid.UUID, grant_id: uuid.UUID, request: Request):
        return await android_service(request).unicode_profile(str(agent_id), str(grant_id))

    @router.put("/grants/{grant_id}/unicode")
    async def android_unicode_profile_save(agent_id: uuid.UUID, grant_id: uuid.UUID, request: Request):
        value = await android_input(request, {"revision", "profile"})
        return await android_service(request).save_unicode_profile(
            str(agent_id), str(grant_id), cast(Record, value["profile"]), cast(int, value["revision"])
        )

    @router.get("/environments")
    async def android_ime_environments(
        agent_id: uuid.UUID, request: Request, after: str = "", limit: int = Query(50, ge=1, le=100)
    ):
        return await android_service(request).environments(str(agent_id), after, limit)

    @router.post("/environments/{environment_id}/restore", status_code=202)
    async def android_ime_restore(agent_id: uuid.UUID, environment_id: uuid.UUID, request: Request):
        value = await android_input(request, {"consent", "session_id", "request_id", "revision"})
        receipt = await android_service(request).restore_ime(str(agent_id), str(environment_id), value)
        runtime(request).application.notify()
        return receipt

    @router.get("/access")
    async def android_access_list(
        agent_id: uuid.UUID, request: Request, after: str = "", limit: int = Query(50, ge=1, le=100)
    ):
        return await android_service(request).access_page(str(agent_id), after, limit)

    @router.post("/access/{access_id}/revoke")
    async def android_access_revoke(agent_id: uuid.UUID, access_id: uuid.UUID, request: Request):
        value = await android_input(request, {"revision"})
        return await android_service(request).revoke_access(str(agent_id), str(access_id), cast(int, value["revision"]))

    @router.post("/grants/{grant_id}/apps")
    async def android_apps(agent_id: uuid.UUID, grant_id: uuid.UUID, request: Request):
        value = await bounded_configuration(request, 4096)
        if (
            not isinstance(cast(object, value), dict)
            or set(value) - {"consent", "after"}
            or value.get("consent") is not True
            or not isinstance(value.get("after", ""), str)
        ):
            raise BusinessError("android_invalid", "Explicit bounded application query is required", status=422)
        return await android_service(request).apps(str(agent_id), str(grant_id), cast(str, value.get("after", "")))

    @router.post("/grants", status_code=201)
    async def android_grant(agent_id: uuid.UUID, request: Request):
        value = await android_input(request, {"probe_id", "consent"})
        return await android_service(request).grant(
            str(agent_id), cast(str, value["probe_id"]), cast(bool, value["consent"])
        )

    @router.post("/grants/{grant_id}/revoke")
    async def android_revoke(agent_id: uuid.UUID, grant_id: uuid.UUID, request: Request):
        value = await android_input(request, {"revision"})
        return await android_service(request).revoke(str(agent_id), str(grant_id), cast(int, value["revision"]))

    @router.post("/grants/{grant_id}/observe")
    async def android_observe(agent_id: uuid.UUID, grant_id: uuid.UUID, request: Request):
        value = await android_input(request, {"consent"})
        if cast(bool, value["consent"]) is not True:
            raise BusinessError("android_consent_required", "Explicit observation consent is required", status=422)
        return await android_service(request).observe(str(agent_id), str(grant_id))

    @router.post("/grants/{grant_id}/capture")
    async def android_capture(agent_id: uuid.UUID, grant_id: uuid.UUID, request: Request):
        value = await android_input(request, {"consent", "session_id"})
        if cast(bool, value["consent"]) is not True:
            raise BusinessError("android_consent_required", "Explicit screen capture consent is required", status=422)
        return await android_service(request).capture(str(agent_id), str(grant_id), cast(str, value["session_id"]))

    @router.delete("/observations/{observation_id}")
    async def android_dismiss(agent_id: uuid.UUID, observation_id: uuid.UUID, request: Request):
        return await android_service(request).dismiss(str(agent_id), str(observation_id))

    @router.post("/actions", status_code=202)
    async def android_action(agent_id: uuid.UUID, request: Request):
        instance = runtime(request)
        receipt = await android_service(request).submit(str(agent_id), await bounded_configuration(request, 4096))
        instance.application.notify()
        return receipt

    return router
