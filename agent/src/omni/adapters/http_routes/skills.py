"""Owner skill editing, activation, version history and run reports."""

from __future__ import annotations

import uuid
from typing import Literal

from fastapi import APIRouter, Query, Request
from pydantic import Field

from ...application.skills import SkillService
from ...domain.contracts import BusinessError
from . import RuntimeLookup, StrictInput


class SkillSaveInput(StrictInput):
    content: str = Field(min_length=1, max_length=32000)
    skill_id: uuid.UUID | None = None
    revision: int = Field(default=0, ge=0, strict=True)


class SkillImportInput(StrictInput):
    object_id: uuid.UUID
    format: Literal["markdown", "zip"]
    skill_id: uuid.UUID | None = None
    revision: int = Field(default=0, ge=0, strict=True)


class SkillStateInput(StrictInput):
    revision: int = Field(ge=1, strict=True)
    state: Literal["enabled", "disabled", "archived"]
    revision_id: uuid.UUID
    manifest_sha256: str = Field(pattern=r"^[a-f0-9]{64}$")


class SkillRollbackInput(StrictInput):
    revision: int = Field(ge=1, strict=True)
    revision_id: uuid.UUID


def skills_router(runtime: RuntimeLookup) -> APIRouter:
    router = APIRouter()

    def skill_service(request: Request) -> SkillService:
        service = runtime(request).feature_services.get("skills")
        if not isinstance(service, SkillService):
            raise BusinessError("skills_unavailable", "Skill service is unavailable", status=503)
        return service

    @router.get("/agents/{agent_id}/skills")
    async def skill_list(
        agent_id: uuid.UUID, request: Request, after: str = "", limit: int = Query(default=50, ge=1, le=50)
    ):
        return await skill_service(request).page(str(agent_id), after, limit)

    @router.post("/agents/{agent_id}/skills", status_code=201)
    async def skill_save(agent_id: uuid.UUID, data: SkillSaveInput, request: Request):
        return await skill_service(request).save(
            str(agent_id), data.content, str(data.skill_id) if data.skill_id else None, data.revision
        )

    @router.post("/agents/{agent_id}/skills/import", status_code=201)
    async def skill_import(agent_id: uuid.UUID, data: SkillImportInput, request: Request):
        return await skill_service(request).import_object(
            str(agent_id),
            str(data.object_id),
            data.format,
            str(data.skill_id) if data.skill_id else None,
            data.revision,
        )

    @router.get("/agents/{agent_id}/skills/available")
    async def skill_available(agent_id: uuid.UUID, request: Request):
        return await skill_service(request).available(str(agent_id))

    @router.post("/agents/{agent_id}/skills/{skill_id}/export")
    async def skill_export(
        agent_id: uuid.UUID, skill_id: uuid.UUID, request: Request, revision_id: uuid.UUID | None = None
    ):
        return await skill_service(request).export(
            str(agent_id), str(skill_id), str(revision_id) if revision_id else None
        )

    @router.get("/agents/{agent_id}/skills/{skill_id}")
    async def skill_detail(
        agent_id: uuid.UUID,
        skill_id: uuid.UUID,
        request: Request,
        revision_id: uuid.UUID | None = None,
        path: str = Query(default="SKILL.md", max_length=180),
    ):
        return await skill_service(request).get(
            str(agent_id), str(skill_id), str(revision_id) if revision_id else None, path
        )

    @router.get("/agents/{agent_id}/skills/{skill_id}/history")
    async def skill_versions(
        agent_id: uuid.UUID,
        skill_id: uuid.UUID,
        request: Request,
        after: int = Query(default=0, ge=0),
        limit: int = Query(default=50, ge=1, le=50),
    ):
        return await skill_service(request).history(str(agent_id), str(skill_id), after, limit)

    @router.put("/agents/{agent_id}/skills/{skill_id}/state")
    async def skill_change_state(agent_id: uuid.UUID, skill_id: uuid.UUID, data: SkillStateInput, request: Request):
        return await skill_service(request).state(
            str(agent_id), str(skill_id), data.revision, data.state, str(data.revision_id), data.manifest_sha256
        )

    @router.post("/agents/{agent_id}/skills/{skill_id}/rollback")
    async def skill_rollback(agent_id: uuid.UUID, skill_id: uuid.UUID, data: SkillRollbackInput, request: Request):
        return await skill_service(request).rollback(str(agent_id), str(skill_id), str(data.revision_id), data.revision)

    @router.get("/agents/{agent_id}/runs/{run_id}/skills")
    async def skill_run_report(agent_id: uuid.UUID, run_id: uuid.UUID, request: Request):
        return await skill_service(request).run_report(str(agent_id), str(run_id))

    return router
