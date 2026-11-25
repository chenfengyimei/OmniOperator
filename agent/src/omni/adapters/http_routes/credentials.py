"""Owner credential registration, changes and authority reviews."""

from __future__ import annotations

import uuid
from typing import Literal

from fastapi import APIRouter, Query, Request
from pydantic import Field

from . import RuntimeLookup, StrictInput, bounded_configuration, configuration_input


class CredentialInput(StrictInput):
    value: str = Field(min_length=1, max_length=65_536)


class CredentialRegistration(StrictInput):
    reference: str = Field(min_length=1, max_length=200, pattern=r"^[A-Za-z0-9_.:/-]+$")


class CredentialReview(StrictInput):
    revision: int = Field(ge=1, strict=True)
    authority_epoch: int = Field(ge=1, strict=True)
    fingerprint: str = Field(pattern=r"^[a-f0-9]{64}$")
    policy: str = Field(min_length=1, max_length=100)
    decision: Literal["allow_current", "revoke"]
    evidence: str = Field(min_length=1, max_length=2000)


def credentials_router(runtime: RuntimeLookup) -> APIRouter:
    router = APIRouter(prefix="/credentials")

    @router.get("")
    async def credential_list(
        request: Request, after: str = Query(default="", max_length=36), limit: int = Query(default=100, ge=1, le=200)
    ):
        return await runtime(request).credentials.page(after, limit)

    @router.post("/register")
    async def credential_register(request: Request):
        data = configuration_input(CredentialRegistration, await bounded_configuration(request, 2048))
        return await runtime(request).credentials.register(data.reference)

    @router.get("/{identity}")
    async def credential_get(identity: uuid.UUID, request: Request):
        return await runtime(request).credentials.get(str(identity))

    @router.get("/{identity}/operations")
    async def credential_operations(
        identity: uuid.UUID,
        request: Request,
        after: str = Query(default="", max_length=36),
        limit: int = Query(default=50, ge=1, le=100),
    ):
        return await runtime(request).credentials.history(str(identity), after, limit)

    @router.get("/{identity}/reviews")
    async def credential_reviews(
        identity: uuid.UUID,
        request: Request,
        after: str = Query(default="", max_length=36),
        limit: int = Query(default=50, ge=1, le=100),
    ):
        return await runtime(request).credentials.history(str(identity), after, limit, reviews=True)

    @router.post("/{identity}/review")
    async def credential_review(identity: uuid.UUID, request: Request):
        data = configuration_input(CredentialReview, await bounded_configuration(request, 16384))
        return await runtime(request).credentials.review(
            str(identity),
            data.revision,
            data.authority_epoch,
            data.fingerprint,
            data.policy,
            data.decision,
            data.evidence,
        )

    @router.put("")
    async def put_credential(
        request: Request, reference: str = Query(min_length=1, max_length=200, pattern=r"^[A-Za-z0-9_.:/-]+$")
    ):
        data = configuration_input(CredentialInput, await bounded_configuration(request, 266240))
        await runtime(request).credentials.change(reference, data.value)
        return {"saved": True}

    @router.delete("")
    async def delete_credential(
        request: Request, reference: str = Query(min_length=1, max_length=200, pattern=r"^[A-Za-z0-9_.:/-]+$")
    ):
        await runtime(request).credentials.change(reference, None)
        return {"deleted": True}

    return router
