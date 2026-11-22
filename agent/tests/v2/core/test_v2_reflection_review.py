"""Revision-bound proposals, day slots and invalidation in the V2 unit of work."""

import asyncio
import datetime as dt
import hashlib

import pytest
from omni_test_support.v2.reflection import BODY, evidence
from sqlalchemy import select

from omni.adapters.persistence import schema as s
from omni.domain.contracts import BusinessError


def selection(journal, sentence, **fields):
    return {
        "operation": "select",
        "journal_id": journal["id"],
        "revision": journal["revision"],
        "field": "revision",
        "start": 0,
        "end": len(sentence),
        "sentence": sentence,
        **fields,
    }


async def propose(db, agent, run, journal, sentence, **fields):
    read = await db.write(
        lambda u: u.reflection_model_review(agent, run, {"operation": "read", "journal_id": journal["id"]})
    )
    result = await db.write(lambda u: u.reflection_model_review(agent, run, selection(journal, sentence, **fields)))
    assert read["body"] == journal["body"] and result["state"] == "proposed"
    return (await db.read(lambda u: u.reflection_detail(agent, journal["id"])))["candidate"]


async def decide(db, agent, candidate, decision):
    return await db.write(
        lambda u: u.reflection_decide(agent, candidate["id"], candidate["revision"], candidate["digest"], decision)
    )


@pytest.mark.parametrize("count", [239, 240, 241])
def test_exact_excerpt_and_stale_decisions(tmp_path, count):
    async def scenario():
        async with evidence(tmp_path) as (db, agent, run, docs, links, _):
            sentence = "📖" * count
            j = await db.write(lambda u: u.reflection_journal_save(agent, run, {**BODY, "revision": sentence}, links))
            with pytest.raises(BusinessError, match="changed"):
                await db.write(lambda u: u.reflection_model_review(agent, run, selection(j, sentence)))
            if count == 241:
                with pytest.raises(BusinessError, match="exact characters"):
                    await propose(db, agent, run, j, sentence)
                return
            c = await propose(db, agent, run, j, sentence)
            assert c["digest"] == hashlib.sha256(sentence.encode()).hexdigest()
            with pytest.raises(BusinessError):
                await decide(db, agent, {**c, "digest": "0" * 64}, "approve")
            assert (await decide(db, agent, c, "approve")) == (await decide(db, agent, c, "approve"))
            await db.write(lambda u: u.document_erasure_begin(agent, docs[0]["id"], 1))
            assert not (await db.read(lambda u: u.reflection_page(agent, "", 20, True)))["items"]
            for table in (s.reflection_candidates, s.reflection_reads, s.reflection_document_links):
                assert not await db.read(lambda u: u.connection.scalar(select(table.c.id)))
            with pytest.raises(BusinessError):
                await decide(db, agent, c, "approve")

    asyncio.run(scenario())


def test_replace_and_retract_preserves_both_quotas(tmp_path):
    async def scenario():
        async with evidence(tmp_path) as (db, agent, run, _, links, _):
            j = await db.write(lambda u: u.reflection_journal_save(agent, run, {**BODY, "revision": "first"}, links))
            c = await propose(db, agent, run, j, "first")
            await decide(db, agent, c, "approve")
            changed = await db.write(lambda u: u.reflection_edit(agent, j["id"], 1, {**BODY, "revision": "second"}))
            assert changed["revision"] == 2 and changed["candidate"] is None and changed["replaces"] == c["id"]
            assert not (await db.read(lambda u: u.reflection_page(agent, "", 20, True)))["items"]
            with pytest.raises(BusinessError):
                await propose(db, agent, run, changed, "second")
            replacement = await propose(db, agent, run, changed, "second", replaces=c["id"])
            approved = await decide(db, agent, replacement, "approve")
            assert (await db.read(lambda u: u.reflection_page(agent, "", 20, True)))["items"][0]["sentence"] == "second"
            assert (await decide(db, agent, approved, "retract"))["state"] == "retracted"
            assert (await decide(db, agent, approved, "retract"))["state"] == "retracted"
            edited = await db.write(lambda u: u.reflection_edit(agent, j["id"], 2, {**BODY, "revision": "third"}))
            with pytest.raises(BusinessError):
                await propose(db, agent, run, edited, "third", replaces=replacement["id"])
            assert await db.read(lambda u: u.connection.scalar(select(s.reflection_slots.c.withdrawn))) is True

    asyncio.run(scenario())


@pytest.mark.parametrize("mode", ["skip", "reject", "wrong_text", "other_session", "erased_before_decision"])
def test_review_guards(tmp_path, mode):
    async def scenario():
        async with evidence(tmp_path) as (db, agent, run, docs, links, _):
            j = await db.write(lambda u: u.reflection_journal_save(agent, run, {**BODY, "revision": "quote"}, links))
            if mode == "other_session":

                def other(u):
                    receipt = u.accept_turn(agent, None, "other", "input", {})
                    u.transition(agent, receipt["run_id"], "running")
                    u.prepare_context(agent, receipt["run_id"])
                    return receipt["run_id"]

                run = await db.write(other)
                with pytest.raises(BusinessError):
                    await db.write(
                        lambda u: u.reflection_model_review(agent, run, {"operation": "read", "journal_id": j["id"]})
                    )
            elif mode == "wrong_text":
                with pytest.raises(BusinessError, match="differs"):
                    await propose(db, agent, run, j, "other")
            elif mode == "skip":
                await db.write(
                    lambda u: u.reflection_model_review(agent, run, {"operation": "read", "journal_id": j["id"]})
                )
                row = await db.write(
                    lambda u: u.reflection_model_review(
                        agent,
                        run,
                        {"operation": "skip", "journal_id": j["id"], "revision": 1, "reason": "already_known"},
                    )
                )
                assert row["state"] == "skipped"
            else:
                c = await propose(db, agent, run, j, "quote")
                if mode == "reject":
                    await decide(db, agent, c, "reject")
                else:
                    await db.write(lambda u: u.document_erase(agent, docs[0]["id"], 1))
                with pytest.raises(BusinessError):
                    await decide(db, agent, c, "approve")
            assert not (await db.read(lambda u: u.reflection_page(agent, "", 20, True)))["items"]

    asyncio.run(scenario())


def test_actual_and_source_day_are_independent_under_competing_decisions(tmp_path):
    async def scenario():
        async with evidence(tmp_path) as (db, agent, run, _, links, clock):
            # A and B share a source day; C is written the next Shanghai day within the same lease.
            candidates = []
            for word in ("A", "B", "C"):
                if word == "C":
                    clock.current += dt.timedelta(seconds=1)
                j = await db.write(lambda u: u.reflection_journal_save(agent, run, {**BODY, "revision": word}, links))
                candidates.append(await propose(db, agent, run, j, word))
            a, b, c = candidates
            outcomes = await asyncio.gather(
                decide(db, agent, a, "approve"), decide(db, agent, c, "approve"), return_exceptions=True
            )
            assert sum(isinstance(o, BusinessError) for o in outcomes) == 1
            clock.current += dt.timedelta(days=1)
            await decide(db, agent, a if isinstance(outcomes[0], BusinessError) else c, "approve")
            clock.current += dt.timedelta(days=1)
            with pytest.raises(BusinessError):
                await decide(db, agent, b, "approve")
            assert len((await db.read(lambda u: u.reflection_page(agent, "", 20, True)))["items"]) == 2

    asyncio.run(scenario())


def test_same_sentence_cannot_claim_another_day_and_pending_notice_is_id_only(tmp_path):
    async def scenario():
        async with evidence(tmp_path) as (db, agent, run, _, links, clock):
            a = await db.write(lambda u: u.reflection_journal_save(agent, run, {**BODY, "revision": "same"}, links))
            assert await db.read(lambda u: u.reflection_pending(agent, run)) == a["id"]
            await decide(db, agent, await propose(db, agent, run, a, "same"), "approve")
            clock.current += dt.timedelta(seconds=1)
            b = await db.write(
                lambda u: u.reflection_journal_save(
                    agent, run, {**BODY, "revision": "same", "missing": "something else"}, links
                )
            )
            proposal = await propose(db, agent, run, b, "same")
            with pytest.raises(BusinessError) as error:
                await decide(db, agent, proposal, "approve")
            assert error.value.code == "reflection_duplicate"
            assert len((await db.read(lambda u: u.reflection_page(agent, "", 20, True)))["items"]) == 1

    asyncio.run(scenario())
