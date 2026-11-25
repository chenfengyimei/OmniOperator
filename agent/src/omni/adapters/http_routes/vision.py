"""Image admission, preparation and per-run image reports."""

from __future__ import annotations

import asyncio
import uuid

from fastapi import APIRouter, Request

from ...application.images import ImageService
from ...domain.contracts import BusinessError
from ...domain.objects import MAX_OBJECT_BYTES
from . import RuntimeLookup


def vision_router(runtime: RuntimeLookup) -> APIRouter:
    router = APIRouter()

    def image_service(request: Request) -> ImageService:
        service = runtime(request).images
        if service is None:
            raise BusinessError("image_protocol_unavailable", "Image admission is unavailable", status=503)
        return service

    @router.post("/agents/{agent_id}/images", status_code=201)
    async def upload_image(agent_id: uuid.UUID, request: Request):
        service = image_service(request)
        upload_slots = runtime(request).upload_slots
        if upload_slots.locked():
            raise BusinessError("upload_busy", "Upload slots are full", status=503)
        await upload_slots.acquire()
        try:
            content = bytearray()
            async with asyncio.timeout(30):
                async for chunk in request.stream():
                    if len(content) + len(chunk) > MAX_OBJECT_BYTES:
                        raise BusinessError("image_too_large", "Image exceeds the 16 MiB limit", status=413)
                    content.extend(chunk)
            return await service.upload(str(agent_id), bytes(content))
        except TimeoutError as exc:
            raise BusinessError("upload_timeout", "Upload exceeded its time budget", status=408) from exc
        finally:
            upload_slots.release()

    @router.post("/agents/{agent_id}/images/{object_id}/prepare")
    async def prepare_image(agent_id: uuid.UUID, object_id: uuid.UUID, request: Request):
        return await image_service(request).prepare(str(agent_id), str(object_id))

    @router.get("/agents/{agent_id}/runs/{run_id}/images")
    async def run_images(agent_id: uuid.UUID, run_id: uuid.UUID, request: Request):
        return await image_service(request).report(str(agent_id), str(run_id))

    return router
