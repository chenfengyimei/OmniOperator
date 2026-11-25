"""Person records, derived persona and associated revision history."""

from __future__ import annotations

import uuid
from typing import Literal

from fastapi import APIRouter, Query, Request
from pydantic import Field

from . import RuntimeLookup, StrictInput


class PersonInput(StrictInput):
    name: str = Field(min_length=1, max_length=100)


class PersonEdit(PersonInput):
    revision: int = Field(ge=1, strict=True)


class AccountInput(StrictInput):
    channel: str = Field(min_length=1, max_length=32)
    external_id: str = Field(min_length=1, max_length=200)


class DocumentVersion(StrictInput):
    entry_id: uuid.UUID | None
    revision: int = Field(ge=0, strict=True)


class DocumentInput(DocumentVersion):
    content: str = Field(max_length=8000)


class DocumentImport(DocumentVersion):
    object_id: uuid.UUID


def people_router(runtime: RuntimeLookup) -> APIRouter:
    router = APIRouter()

    @router.get("/agents/{agent_id}/people/owner")
    async def owner_person(agent_id: uuid.UUID, request: Request):
        return await runtime(request).people.owner(str(agent_id))

    @router.get("/agents/{agent_id}/people")
    async def list_people(
        agent_id: uuid.UUID,
        request: Request,
        after: uuid.UUID | None = None,
        limit: int = Query(default=100, ge=1, le=200),
    ):
        return await runtime(request).people.page(str(agent_id), str(after) if after else "", limit)

    @router.post("/agents/{agent_id}/people", status_code=201)
    async def create_person(agent_id: uuid.UUID, data: PersonInput, request: Request):
        return await runtime(request).people.create(str(agent_id), data.name)

    @router.get("/agents/{agent_id}/people/{person_id}")
    async def get_person(agent_id: uuid.UUID, person_id: uuid.UUID, request: Request):
        return await runtime(request).people.get(str(agent_id), str(person_id))

    @router.put("/agents/{agent_id}/people/{person_id}")
    async def rename_person(agent_id: uuid.UUID, person_id: uuid.UUID, data: PersonEdit, request: Request):
        return await runtime(request).people.rename(str(agent_id), str(person_id), data.name, data.revision)

    @router.delete("/agents/{agent_id}/people/{person_id}")
    async def erase_person(agent_id: uuid.UUID, person_id: uuid.UUID, request: Request, revision: int = Query(ge=1)):
        await runtime(request).people.erase(str(agent_id), str(person_id), revision)
        return {"erased": True}

    @router.get("/agents/{agent_id}/people/{person_id}/accounts")
    async def person_accounts(
        agent_id: uuid.UUID,
        person_id: uuid.UUID,
        request: Request,
        after: uuid.UUID | None = None,
        limit: int = Query(default=100, ge=1, le=200),
    ):
        return await runtime(request).people.accounts(str(agent_id), str(person_id), str(after) if after else "", limit)

    @router.post("/agents/{agent_id}/people/{person_id}/accounts", status_code=201)
    async def bind_account(agent_id: uuid.UUID, person_id: uuid.UUID, data: AccountInput, request: Request):
        return await runtime(request).people.bind(str(agent_id), str(person_id), data.channel, data.external_id)

    @router.delete("/agents/{agent_id}/people/{person_id}/accounts/{account_id}")
    async def unbind_account(
        agent_id: uuid.UUID, person_id: uuid.UUID, account_id: uuid.UUID, request: Request, revision: int = Query(ge=1)
    ):
        await runtime(request).people.unbind(str(agent_id), str(person_id), str(account_id), revision)
        return {"unlinked": True}

    @router.get("/agents/{agent_id}/people/{person_id}/documents/{kind}")
    async def personal_document(
        agent_id: uuid.UUID, person_id: uuid.UUID, kind: Literal["profile", "resident"], request: Request
    ):
        return await runtime(request).people.document(str(agent_id), str(person_id), kind)

    @router.put("/agents/{agent_id}/people/{person_id}/documents/{kind}")
    async def save_personal_document(
        agent_id: uuid.UUID,
        person_id: uuid.UUID,
        kind: Literal["profile", "resident"],
        data: DocumentInput,
        request: Request,
    ):
        return await runtime(request).people.save(
            str(agent_id),
            str(person_id),
            kind,
            data.content,
            str(data.entry_id) if data.entry_id else None,
            data.revision,
        )

    @router.post("/agents/{agent_id}/people/{person_id}/documents/{kind}/import")
    async def import_personal_document(
        agent_id: uuid.UUID,
        person_id: uuid.UUID,
        kind: Literal["profile", "resident"],
        data: DocumentImport,
        request: Request,
    ):
        return await runtime(request).people.import_object(
            str(agent_id),
            str(person_id),
            kind,
            str(data.object_id),
            str(data.entry_id) if data.entry_id else None,
            data.revision,
        )

    @router.get("/agents/{agent_id}/memory/{memory_id}/revisions")
    async def memory_revisions(
        agent_id: uuid.UUID,
        memory_id: uuid.UUID,
        request: Request,
        after: int = Query(default=0, ge=0),
        limit: int = Query(default=50, ge=1, le=100),
    ):
        return await runtime(request).people.history(str(agent_id), str(memory_id), after, limit)

    @router.get("/agents/{agent_id}/persona-growth")
    async def persona_growth(agent_id: uuid.UUID, request: Request):
        return await runtime(request).persona_growth.read(str(agent_id))

    @router.post("/agents/{agent_id}/persona-growth/rebuild")
    async def persona_growth_rebuild(agent_id: uuid.UUID, request: Request):
        return await runtime(request).persona_growth.rebuild(str(agent_id))

    return router
