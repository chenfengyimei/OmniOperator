"""Source document revisions, bounded indexing and search."""

from __future__ import annotations

import uuid
from typing import Literal

from fastapi import APIRouter, Query, Request
from pydantic import Field

from ...domain.contracts import BusinessError
from . import RuntimeLookup, StrictInput, bounded_configuration, configuration_input


class SourceDocumentInput(StrictInput):
    document_id: uuid.UUID | None = None
    title: str = Field(min_length=1, max_length=200)
    content: str = Field(min_length=1, max_length=100000)
    revision: int = Field(default=0, ge=0, strict=True)


class SourceDocumentImport(StrictInput):
    title: str = Field(min_length=1, max_length=200)
    object_id: uuid.UUID
    document_id: uuid.UUID | None = None
    revision: int = Field(default=0, ge=0, strict=True)


class SourceIndexInput(StrictInput):
    kind: Literal["full_text", "vector"]


def documents_router(runtime: RuntimeLookup) -> APIRouter:
    router = APIRouter(prefix="/agents/{agent_id}/documents")

    @router.get("")
    async def source_documents(
        agent_id: uuid.UUID,
        request: Request,
        after: uuid.UUID | None = None,
        limit: int = Query(default=100, ge=1, le=200),
    ):
        return await runtime(request).documents.page(str(agent_id), str(after) if after else "", limit)

    @router.post("", status_code=201)
    async def source_document_create(agent_id: uuid.UUID, request: Request):
        data = configuration_input(SourceDocumentInput, await bounded_configuration(request, 1048576))
        return await runtime(request).documents.save(
            str(agent_id),
            data.title,
            data.content,
            document_id=str(data.document_id) if data.document_id else None,
            revision=data.revision,
        )

    @router.post("/import", status_code=201)
    async def source_document_import(agent_id: uuid.UUID, request: Request):
        data = configuration_input(SourceDocumentImport, await bounded_configuration(request))
        return await runtime(request).documents.import_object(
            str(agent_id),
            data.title,
            str(data.object_id),
            str(data.document_id) if data.document_id else None,
            data.revision,
        )

    @router.get("/search")
    async def source_document_search(
        agent_id: uuid.UUID,
        request: Request,
        q: str = Query(min_length=1, max_length=500),
        limit: int = Query(default=20, ge=1, le=20),
    ):
        return await runtime(request).documents.search(str(agent_id), q, limit)

    @router.get("/{document_id}")
    async def source_document_get(
        agent_id: uuid.UUID, document_id: uuid.UUID, request: Request, revision_id: uuid.UUID | None = None
    ):
        return await runtime(request).documents.get(
            str(agent_id), str(document_id), str(revision_id) if revision_id else None
        )

    @router.put("/{document_id}")
    async def source_document_save(agent_id: uuid.UUID, document_id: uuid.UUID, request: Request):
        data = configuration_input(SourceDocumentInput, await bounded_configuration(request, 1048576))
        if data.document_id and data.document_id != document_id:
            raise BusinessError("invalid_document", "Document identity must match the request target", status=422)
        return await runtime(request).documents.save(
            str(agent_id), data.title, data.content, str(document_id), data.revision
        )

    @router.delete("/{document_id}")
    async def source_document_erase(
        agent_id: uuid.UUID, document_id: uuid.UUID, request: Request, revision: int = Query(ge=1)
    ):
        await runtime(request).documents.erase(str(agent_id), str(document_id), revision)
        return {"erased": True}

    @router.get("/{document_id}/revisions")
    async def source_document_history(
        agent_id: uuid.UUID,
        document_id: uuid.UUID,
        request: Request,
        after: int = Query(default=0, ge=0),
        limit: int = Query(default=50, ge=1, le=100),
    ):
        return await runtime(request).documents.history(str(agent_id), str(document_id), after, limit)

    @router.get("/{document_id}/indexes")
    async def source_document_indexes(
        agent_id: uuid.UUID,
        document_id: uuid.UUID,
        request: Request,
        after: uuid.UUID | None = None,
        limit: int = Query(default=50, ge=1, le=100),
    ):
        return await runtime(request).documents.index_status(
            str(agent_id), str(document_id), str(after) if after else "", limit
        )

    @router.post("/{document_id}/indexes")
    async def source_document_rebuild(agent_id: uuid.UUID, document_id: uuid.UUID, request: Request):
        data = configuration_input(SourceIndexInput, await bounded_configuration(request))
        return await runtime(request).documents.rebuild(str(agent_id), str(document_id), data.kind)

    return router
