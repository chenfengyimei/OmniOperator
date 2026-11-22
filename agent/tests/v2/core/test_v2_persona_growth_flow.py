"""K17 approved ledger drives the owner-only growth cache, including stale and restart paths."""

from __future__ import annotations

import asyncio
import hashlib

import pytest
from omni_test_support.paths import REPOSITORY_ROOT
from omni_test_support.v2.objects import Clock, InstancePaths, opened
from omni_test_support.v2.reflection import BODY, SENTENCE, evidence

from omni.adapters.persona_files import PersonaGrowthFiles
from omni.application.persona import PersonaGrowthService
from omni.domain.contracts import BusinessError
from omni.domain.persona_growth import EMPTY


def files(home):
    return PersonaGrowthFiles(InstancePaths.resolve(home).persona)


def selection(journal, sentence, replaces=None):
    args = {
        "operation": "select",
        "journal_id": journal["id"],
        "revision": journal["revision"],
        "field": "revision",
        "start": 0,
        "end": len(sentence),
        "sentence": sentence,
    }
    if replaces is not None:
        args["replaces"] = replaces
    return args


async def approve(db, agent, run, journal, sentence, replaces=None):
    await db.write(lambda u: u.reflection_model_review(agent, run, {"operation": "read", "journal_id": journal["id"]}))
    candidate = await db.write(lambda u: u.reflection_model_review(agent, run, selection(journal, sentence, replaces)))
    return await db.write(
        lambda u: u.reflection_decide(
            agent,
            candidate["candidate_id"],
            1,
            hashlib.sha256(sentence.encode()).hexdigest(),
            "approve",
        )
    )


def test_approved_entry_builds_and_retraction_never_serves_stale_bytes(tmp_path):
    async def scenario():
        async with evidence(tmp_path) as (db, agent, run, _, links, _):
            journal = await db.write(
                lambda u: u.reflection_journal_save(agent, run, {**BODY, "revision": SENTENCE}, links)
            )
            approved = await approve(db, agent, run, journal, SENTENCE)
            entries = await db.read(lambda u: u.persona_growth_entries(agent))
            assert [row["sentence"] for row in entries] == [SENTENCE]
            assert entries[0]["approved_day"] and entries[0]["journal_id"] == journal["id"]

            service = PersonaGrowthService(db, files(tmp_path))
            with pytest.raises(BusinessError) as error:
                await service.read(agent)
            assert error.value.code == "persona_growth_stale"
            ready = await service.rebuild(agent)
            assert ready["state"] == "ready" and ready["approved_count"] == 1
            assert SENTENCE in str(ready["content"]) and not ready["truncated"]
            assert ready["sha256"] == hashlib.sha256(str(ready["content"]).encode()).hexdigest()
            assert (await service.read(agent))["sha256"] == ready["sha256"]

            await db.write(
                lambda u: u.reflection_decide(
                    agent, str(approved["id"]), int(approved["revision"]), str(approved["digest"]), "retract"
                )
            )
            with pytest.raises(BusinessError) as error:
                await service.read(agent)
            assert error.value.code == "persona_growth_stale"
            empty = await service.rebuild(agent)
            assert empty["approved_count"] == 0 and SENTENCE not in str(empty["content"])
            assert EMPTY in str(empty["content"])

    asyncio.run(scenario())


def test_edit_and_source_erasure_remove_entries_and_invalidate_the_cache(tmp_path):
    async def scenario():
        async with evidence(tmp_path) as (db, agent, run, docs, links, _):
            journal = await db.write(
                lambda u: u.reflection_journal_save(agent, run, {**BODY, "revision": SENTENCE}, links)
            )
            await approve(db, agent, run, journal, SENTENCE)
            service = PersonaGrowthService(db, files(tmp_path))
            await service.rebuild(agent)
            assert (await service.read(agent))["approved_count"] == 1

            edited = await db.write(
                lambda u: u.reflection_edit(agent, journal["id"], 1, {**BODY, "revision": "revised view"})
            )
            assert edited["revision"] == 2 and edited["candidate"] is None
            assert await db.read(lambda u: u.persona_growth_entries(agent)) == []
            with pytest.raises(BusinessError):
                await service.read(agent)
            empty = await service.rebuild(agent)
            assert empty["approved_count"] == 0 and SENTENCE not in str(empty["content"])

            # A revision replaces the old formal entry only through an explicit candidate.
            await approve(db, agent, run, edited, "revised view", replaces=edited["replaces"])
            built = await service.rebuild(agent)
            assert "revised view" in str(built["content"]) and built["approved_count"] == 1
            await db.write(lambda u: u.document_erase(agent, docs[0]["id"], 1))
            assert await db.read(lambda u: u.persona_growth_entries(agent)) == []
            with pytest.raises(BusinessError):
                await service.read(agent)
            rebuilt = await service.rebuild(agent)
            assert rebuilt["approved_count"] == 0 and "revised view" not in str(rebuilt["content"])

    asyncio.run(scenario())


def test_restart_keeps_a_valid_cache_and_requires_rebuild_after_a_change(tmp_path):
    async def first():
        async with evidence(tmp_path) as (db, agent, run, _, links, _):
            journal = await db.write(
                lambda u: u.reflection_journal_save(agent, run, {**BODY, "revision": SENTENCE}, links)
            )
            approved = await approve(db, agent, run, journal, SENTENCE)
            ready = await PersonaGrowthService(db, files(tmp_path)).rebuild(agent)
            return agent, str(approved["id"]), int(approved["revision"]), str(approved["digest"]), ready["sha256"]

    _agent, candidate, revision, digest, sha256 = asyncio.run(first())

    async def second():
        async with opened(tmp_path, Clock()) as (_, db, _, agent):
            service = PersonaGrowthService(db, files(tmp_path))
            again = await service.read(agent)
            assert again["sha256"] == sha256 and SENTENCE in str(again["content"])
            await db.write(lambda u: u.reflection_decide(agent, candidate, revision, digest, "retract"))
            with pytest.raises(BusinessError):
                await service.read(agent)
            rebuilt = await service.rebuild(agent)
            assert rebuilt["approved_count"] == 0 and SENTENCE not in str(rebuilt["content"])

    asyncio.run(second())


def test_reader_is_not_referenced_by_the_model_context():
    for relative in ("agent/src/omni/application/engine.py", "agent/src/omni/application/context.py"):
        text = (REPOSITORY_ROOT / relative).read_text(encoding="utf-8")
        assert "persona_growth" not in text and "PersonaGrowth" not in text
    routing = (REPOSITORY_ROOT / "agent/src/omni/domain/routing.py").read_text(encoding="utf-8")
    assert "persona_growth" not in routing and "PersonaGrowth" not in routing
