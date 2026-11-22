"""Reusable reflection fixtures extracted from collected tests; no test cases are defined here."""

from __future__ import annotations

import datetime as dt
from contextlib import asynccontextmanager

from sqlalchemy import select

from omni.adapters.persistence import schema as s
from omni.application.documents import DocumentService
from omni.domain.contracts import InvocationContext, Outcome

from .documents import Clock, opened, read_action

BODY = dict(
    encounter="what I saw",
    facts="📖",
    author_view="",
    inference="",
    previous_judgment="",
    revision="",
    missing="unknown ending",
    next_response="",
)

SENTENCE = "📖 Observe before inferring."


@asynccontextmanager
async def evidence(home):
    """Two live document captures behind one authorized run."""
    clock = Clock()
    clock.current = dt.datetime(2026, 11, 21, 15, 59, 59, tzinfo=dt.timezone.utc)
    async with opened(home, clock) as (_, db, objects, agent):
        service = DocumentService(db, objects)
        try:
            turn, action = await db.write(lambda u: read_action(u, agent))
            context = InvocationContext(agent, turn["run_id"], turn["session_id"], 1)
            docs, links = [], []
            for i in range(2):
                if i:
                    _, action = await db.write(lambda u: read_action(u, agent, turn=turn))
                doc = await service.save(agent, "source", f"ab📖{i}")
                result = await service.invoke(
                    "document_read",
                    {"document_id": doc["id"], "start": 2, "length": 1},
                    operation_id=action["id"],
                    context=context,
                )
                assert result.status == Outcome.OK
                await db.write(lambda u: u.settle_action(agent, action["id"], result))
                capture = await db.read(
                    lambda u: u.connection.scalar(
                        select(s.context_documents.c.id).where(s.context_documents.c.action_id == action["id"])
                    )
                )
                docs.append(doc)
                links.append({"capture_id": capture, "field": "facts"})
            yield db, agent, turn["run_id"], docs, links, clock
        finally:
            assert await service.close()


async def seed_review(home, sentence=SENTENCE):
    """Create one document-backed journal with a proposed candidate and return its ids."""
    clock = Clock()
    clock.current = dt.datetime(2026, 11, 21, 15, 59, 59, tzinfo=dt.timezone.utc)
    async with opened(home, clock) as (_, db, objects, agent):
        service = DocumentService(db, objects)
        try:
            turn, action = await db.write(lambda u: read_action(u, agent))
            run = turn["run_id"]
            context = InvocationContext(agent, run, turn["session_id"], 1)
            doc = await service.save(agent, "source", "ab📖")
            result = await service.invoke(
                "document_read",
                {"document_id": doc["id"], "start": 2, "length": 1},
                operation_id=action["id"],
                context=context,
            )
            assert result.status == Outcome.OK
            await db.write(lambda u: u.settle_action(agent, action["id"], result))
            capture = await db.read(
                lambda u: u.connection.scalar(
                    select(s.context_documents.c.id).where(s.context_documents.c.action_id == action["id"])
                )
            )
            journal = await db.write(
                lambda u: u.reflection_journal_save(
                    agent, run, {**BODY, "revision": sentence}, [{"capture_id": capture, "field": "facts"}]
                )
            )
            await db.write(
                lambda u: u.reflection_model_review(agent, run, {"operation": "read", "journal_id": journal["id"]})
            )
            candidate = await db.write(
                lambda u: u.reflection_model_review(
                    agent,
                    run,
                    {
                        "operation": "select",
                        "journal_id": journal["id"],
                        "revision": journal["revision"],
                        "field": "revision",
                        "start": 0,
                        "end": len(sentence),
                        "sentence": sentence,
                    },
                )
            )
            return {
                "agent": agent,
                "journal": str(journal["id"]),
                "candidate": str(candidate["candidate_id"]),
                "sentence": sentence,
                "document": str(doc["id"]),
            }
        finally:
            assert await service.close()


__all__ = ["BODY", "SENTENCE", "evidence", "seed_review"]
