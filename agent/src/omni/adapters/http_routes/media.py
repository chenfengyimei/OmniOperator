"""Owner media sources, job receipts and processor diagnostics."""

from __future__ import annotations

import uuid
from typing import Literal, cast

from fastapi import APIRouter, Query, Request
from pydantic import Field

from ...application.media import MediaService
from ...application.ports import Record
from ...domain.contracts import BusinessError
from . import RuntimeLookup, StrictInput, bounded_configuration, configuration_input


class MediaSourceInput(StrictInput):
    config_id: uuid.UUID
    target: str = Field(min_length=12, max_length=12, pattern=r"^BV[0-9A-Za-z]{10}$")


class MediaJobInput(StrictInput):
    operation: Literal["subtitle", "transcribe", "frames"]
    model: str = Field(default="small", pattern=r"^[a-z0-9][a-z0-9._-]{0,40}$")
    device: Literal["cpu", "auto"] = "cpu"
    compute_type: Literal["int8", "float16", "float32"] = "int8"
    count: int = Field(default=6, ge=1, le=12, strict=True)
    width: int = Field(default=640, ge=320, le=1280, strict=True)
    timestamps: list[float] | None = Field(default=None, max_length=12)
    select: Literal["equidistant", "scenes"] = "equidistant"


class MediaUploadSourceInput(StrictInput):
    object_id: uuid.UUID
    title: str = Field(min_length=1, max_length=300)


def media_router(runtime: RuntimeLookup) -> APIRouter:
    router = APIRouter(prefix="/agents/{agent_id}/media")

    def media_service(request: Request) -> MediaService:
        service = runtime(request).feature_services.get("media")
        if not isinstance(service, MediaService):
            raise BusinessError("media_unavailable", "Media capability is unavailable", status=503)
        return service

    @router.post("/sources", status_code=201)
    async def media_source_register(agent_id: uuid.UUID, request: Request):
        data = configuration_input(MediaSourceInput, await bounded_configuration(request, 4096, strict=True))
        return await media_service(request).register(str(agent_id), str(data.config_id), data.target)

    @router.get("/sources")
    async def media_sources(
        agent_id: uuid.UUID,
        request: Request,
        after: uuid.UUID | None = None,
        limit: int = Query(default=50, ge=1, le=100),
    ):
        return await media_service(request).sources(str(agent_id), str(after) if after else "", limit)

    @router.post("/uploads", status_code=201)
    async def media_upload_source(agent_id: uuid.UUID, request: Request):
        data = configuration_input(MediaUploadSourceInput, await bounded_configuration(request, 4096, strict=True))
        return await media_service(request).register_upload(str(agent_id), str(data.object_id), data.title)

    @router.get("/sources/{source_id}")
    async def media_source(agent_id: uuid.UUID, source_id: uuid.UUID, request: Request):
        return await media_service(request).source(str(agent_id), str(source_id))

    @router.delete("/sources/{source_id}")
    async def media_source_delete(agent_id: uuid.UUID, source_id: uuid.UUID, request: Request):
        await media_service(request).delete(str(agent_id), str(source_id))
        return {"deleted": True}

    @router.post("/sources/{source_id}/jobs", status_code=201)
    async def media_job_submit(agent_id: uuid.UUID, source_id: uuid.UUID, request: Request):
        data = configuration_input(MediaJobInput, await bounded_configuration(request, 4096, strict=True))
        params: Record = {}
        if data.operation == "transcribe":
            params = {"model": data.model, "device": data.device, "compute_type": data.compute_type}
        if data.operation == "frames":
            params = {"count": data.count, "width": data.width}
            if data.timestamps is not None:
                params = cast(
                    Record, {"count": len(data.timestamps), "width": data.width, "timestamps": data.timestamps}
                )
            if data.select == "scenes":
                params["select"] = "scenes"
        return await media_service(request).submit(str(agent_id), str(source_id), data.operation, params)

    @router.get("/sources/{source_id}/jobs")
    async def media_jobs(
        agent_id: uuid.UUID,
        source_id: uuid.UUID,
        request: Request,
        after: uuid.UUID | None = None,
        limit: int = Query(default=50, ge=1, le=100),
    ):
        return await media_service(request).jobs(str(agent_id), str(source_id), str(after) if after else "", limit)

    @router.get("/jobs/{job_id}")
    async def media_job(agent_id: uuid.UUID, job_id: uuid.UUID, request: Request):
        return await media_service(request).job(str(agent_id), str(job_id))

    @router.post("/jobs/{job_id}/cancel")
    async def media_job_cancel(agent_id: uuid.UUID, job_id: uuid.UUID, request: Request):
        return await media_service(request).cancel(str(agent_id), str(job_id))

    @router.get("/transcripts/{transcript_id}")
    async def media_transcript(agent_id: uuid.UUID, transcript_id: uuid.UUID, request: Request):
        return await media_service(request).transcript(str(agent_id), str(transcript_id))

    @router.get("/transcripts/{transcript_id}/window")
    async def media_transcript_window(
        agent_id: uuid.UUID,
        transcript_id: uuid.UUID,
        request: Request,
        start: int = Query(default=0, ge=0),
        length: int = Query(default=12000, ge=1, le=12000),
    ):
        return await media_service(request).transcript_window_for(str(agent_id), str(transcript_id), start, length)

    @router.get("/frames/{frames_id}")
    async def media_frames(agent_id: uuid.UUID, frames_id: uuid.UUID, request: Request):
        return await media_service(request).frames(str(agent_id), str(frames_id))

    @router.get("/processor")
    async def media_processor(agent_id: uuid.UUID, request: Request):
        return media_service(request).health()

    @router.post("/processor/probe")
    async def media_processor_probe(agent_id: uuid.UUID, request: Request):
        return await media_service(request).processor_probe()

    return router
