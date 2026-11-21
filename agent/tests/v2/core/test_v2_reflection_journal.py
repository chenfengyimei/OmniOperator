"""K17 document-backed drafts through the live V2 document reader and isolated store."""

import asyncio
import datetime as dt
import hashlib
from contextlib import asynccontextmanager
from uuid import uuid4

import pytest
from omni_test_support.v2.documents import Clock, opened, read_action
from sqlalchemy import func, select

from omni.adapters.persistence import schema as s
from omni.application.documents import DocumentService
from omni.domain.contracts import BusinessError, InvocationContext, Outcome

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


def test_atomic_queue_dedup_and_shanghai_quota(tmp_path):
    async def scenario():
        async with evidence(tmp_path) as (db, agent, run, _, links, clock):

            async def save(body, anchors):
                return await db.write(lambda u: u.reflection_journal_save(agent, run, body, anchors))

            maximum = {**BODY, "revision": "📖" * 12000}
            first = await save(maximum, links * 10)
            assert first["body"] == maximum and first["source_day"] == "2026-11-21"
            assert first["state"] == "pending" and first["revision"] == 1
            assert (await save(maximum, links[::-1]))["id"] == first["id"]
            hashes = await db.read(lambda u: list(u.connection.scalars(select(s.reflection_document_links.c.sha256))))
            assert hashes == [hashlib.sha256("📖".encode()).hexdigest()] * 2
            outcomes = await asyncio.gather(
                save(BODY, links), save({**BODY, "facts": "other"}, links), return_exceptions=True
            )
            assert sum(isinstance(row, dict) for row in outcomes) == 1
            assert [e.code for e in outcomes if isinstance(e, BusinessError)] == ["journal_daily_limit"]
            clock.current += dt.timedelta(seconds=1)
            assert (await save(maximum, links))["source_day"] == "2026-11-21"
            assert (await save({**BODY, "facts": "next day"}, links))["source_day"] == "2026-11-22"

    asyncio.run(scenario())


@pytest.mark.parametrize(
    "mode,code",
    [
        ("empty", "invalid_journal_anchor"),
        ("21", "invalid_journal_anchor"),
        ("unknown_capture", "not_found"),
        ("unknown_field", "invalid_journal_anchor"),
        ("blank", "invalid_journal"),
        ("long", "invalid_journal"),
        ("extra", "invalid_journal"),
        ("expired", "run_lease_lost"),
        ("surrogate", "invalid_journal"),
        ("foreign_run", "journal_source_unobserved"),
        ("foreign_agent", "not_found"),
    ],
)
def test_rejected_capture_leaves_no_draft(tmp_path, mode, code):
    async def scenario():
        async with evidence(tmp_path) as (db, agent, run, _, links, clock):
            body = dict(BODY)
            if mode == "empty":
                links = []
            elif mode == "21":
                links = links * 10 + links[:1]
            elif mode == "unknown_capture":
                links += [{"capture_id": str(uuid4()), "field": "facts"}]
            elif mode == "unknown_field":
                links[0]["field"] = "never_observed"
            elif mode == "blank":
                body["missing"] = "  "
            elif mode == "long":
                body["revision"] = "📖" * 12001
            elif mode == "expired":
                clock.current += dt.timedelta(days=1)
            elif mode == "surrogate":
                body["facts"] = chr(0xD800)
            elif mode == "extra":
                body["owner_approved"] = "true"
            elif mode in ("foreign_run", "foreign_agent"):
                if mode == "foreign_agent":
                    agent = (await db.write(lambda u: u.create_agent("other")))["id"]

                def fresh(u):
                    turn = u.accept_turn(agent, None, "different", "read", {})
                    u.transition(agent, turn["run_id"], "running")
                    u.prepare_context(agent, turn["run_id"])
                    return turn["run_id"]

                run = await db.write(fresh)
            with pytest.raises(BusinessError) as error:
                await db.write(lambda u: u.reflection_journal_save(agent, run, body, links))
            assert error.value.code == code
            assert not await db.read(
                lambda u: u.connection.scalar(select(func.count()).select_from(s.reflection_journals))
            )

    asyncio.run(scenario())


@pytest.mark.parametrize("barrier", ["document_erasure_begin", "document_erase"])
def test_erasure_scrubs_whole_multisource_draft_and_preserves_day_slot(tmp_path, barrier):
    async def scenario():
        async with evidence(tmp_path) as (db, agent, run, docs, links, _):
            await db.write(lambda u: u.reflection_journal_save(agent, run, BODY, links))
            await db.write(lambda u: getattr(u, barrier)(agent, docs[0]["id"], 1))
            row = await db.read(lambda u: dict(u.connection.execute(select(s.reflection_journals)).mappings().one()))
            assert (row["state"], row["body"], row["fingerprint"], row["revision"], row["slot"]) == (
                "erased",
                None,
                None,
                2,
                1,
            )
            assert not await db.read(
                lambda u: u.connection.scalar(select(func.count()).select_from(s.reflection_document_links))
            )
            with pytest.raises(BusinessError):
                await db.write(lambda u: u.reflection_journal_save(agent, run, BODY, links))

    asyncio.run(scenario())


def test_queue_and_links_roll_back_as_one(tmp_path):
    async def scenario():
        async with evidence(tmp_path) as (db, agent, run, _, links, _):

            def abort(u):
                u.reflection_journal_save(agent, run, BODY, links)
                raise RuntimeError("rollback probe")

            with pytest.raises(RuntimeError, match="rollback probe"):
                await db.write(abort)
            for table in (s.reflection_journals, s.reflection_document_links):
                assert not await db.read(lambda u: u.connection.scalar(select(func.count()).select_from(table)))
            assert (await db.write(lambda u: u.reflection_journal_save(agent, run, BODY, links)))["slot"] == 1

    asyncio.run(scenario())
