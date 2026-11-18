"""Reusable maintenance_ownership fixtures, extracted without collecting test cases."""

from __future__ import annotations

import asyncio
import copy
import datetime as dt
import json
from contextlib import asynccontextmanager

import pytest
from sqlalchemy import select

from omni.adapters.persistence import schema as s
from omni.adapters.primitives import UUIDs
from omni.application.documents import DocumentService
from omni.domain.contracts import BusinessError
from omni.domain.maintenance import INDEX_KIND, INDEX_VERSION, Claim
from omni_test_support.v2.objects import Clock, opened

PRIVATE = "maintenance-owned-source-body-is-private"


@asynccontextmanager
async def environment(home, clock=None):
    clock = clock or Clock()
    async with opened(home, clock) as (_, db, objects, agent):
        documents = DocumentService(db, objects)
        try:
            yield clock, db, documents, agent
        finally:
            assert await documents.close()


def request(source, key="maintenance"):
    return {
        "kind": "document_index_rebuild",
        "key": key,
        "document_id": source["id"],
        "revision": source["head_revision"],
    }


def identity(row):
    attempt = row["attempt"]
    return row["agent_id"], row["id"], attempt["id"], attempt["token"], attempt["fence"]


def captured(row):
    attempt = row["attempt"]
    return Claim(
        agent_id=row["agent_id"],
        job_id=row["id"],
        attempt_id=attempt["id"],
        token=attempt["token"],
        boot_id=attempt["boot_id"],
        fence=attempt["fence"],
        kind=row["kind"],
        document_id=row["document_id"],
        revision_id=row["revision_id"],
        source_revision=row["source_revision"],
        erasure_job_id=row["erasure_job_id"],
        index_version=row["index_version"],
    )


__all__ = [
    "BusinessError",
    "Claim",
    "Clock",
    "DocumentService",
    "INDEX_KIND",
    "INDEX_VERSION",
    "PRIVATE",
    "UUIDs",
    "asynccontextmanager",
    "asyncio",
    "captured",
    "copy",
    "dt",
    "environment",
    "identity",
    "json",
    "opened",
    "pytest",
    "request",
    "s",
    "select",
]
