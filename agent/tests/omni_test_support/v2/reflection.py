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


__all__ = ["BODY", "evidence"]
