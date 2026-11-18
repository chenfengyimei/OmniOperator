"""Reusable memory_organization_repository fixtures, extracted without collecting test cases."""

from __future__ import annotations

import asyncio
import json

import pytest
from sqlalchemy import select, text, update

from omni.adapters.persistence import schema as s
from omni.domain.contracts import BusinessError
from omni_test_support.v2.maintenance_ownership import environment
from omni_test_support.v2.maintenance_ownership import request as document_request
from omni_test_support.v2.objects import Clock, opened


def organization_request(memories, key="organization"):
    return {"key": key, "sources": [{"memory_id": row["id"], "revision": row["revision"]} for row in memories]}


def owner_decision(batch, candidate, action="apply"):
    return {
        "revision": candidate["revision"],
        "fingerprint": candidate["fingerprint"],
        "epoch": batch["history_epoch"],
        "decision": action,
    }


def facts(unit, agent, content="organization exact fact", count=2, **options):
    return sorted([unit.remember(agent, content, **options) for _ in range(count)], key=lambda row: row["id"])


async def proposal(database, agent, memories, key="organization"):
    queued = await database.write(
        lambda unit: unit.memory_organization_enqueue(agent, organization_request(memories, key))
    )
    return await database.write(lambda unit: unit.memory_organization_process(agent, queued["id"]))


def memory_truth(unit, agent):
    return {
        table.name: [
            dict(row)
            for row in unit.connection.execute(
                select(table).where(table.c.agent_id == agent).order_by(table.c.id)
            ).mappings()
        ]
        for table in (s.memory_entries, s.memory_revisions, s.source_anchors, s.context_memories, s.tombstones)
    }


def organization_audits(unit, agent, kind):
    return [
        dict(row)
        for row in unit.connection.execute(
            select(s.audit).where(s.audit.c.agent_id == agent, s.audit.c.kind == kind).order_by(s.audit.c.sequence)
        ).mappings()
    ]


def candidate_memories(batch, candidate):
    sources = {row["id"]: row for row in batch["sources"]}
    return sources[candidate["keep_source_id"]]["memory_id"], sources[candidate["duplicate_source_id"]]["memory_id"]


__all__ = [
    "BusinessError",
    "Clock",
    "asyncio",
    "candidate_memories",
    "document_request",
    "environment",
    "facts",
    "json",
    "memory_truth",
    "opened",
    "organization_audits",
    "organization_request",
    "owner_decision",
    "proposal",
    "pytest",
    "s",
    "select",
    "text",
    "update",
]
