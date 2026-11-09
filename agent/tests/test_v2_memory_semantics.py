"""Real SQLite, source races and owned local-vector work; no model or external service."""

from __future__ import annotations

import asyncio

import pytest
from sqlalchemy import delete, select, update
from test_v2_memory_organization import decision, request

from omni.adapters.persistence import schema as s
from omni.composition.instance import open_instance
from omni.domain.contracts import BusinessError


class Vectors:
    version = "memory-fixture-v1"
    dimension = 3

    def __init__(self):
        self.calls = []
        self.closed = 0
        self.invalid = False
        self.entered, self.release = asyncio.Event(), asyncio.Event()
        self.block = False

    def status(self):
        return {"available": not self.closed, "state": "fixture"}

    async def embed(self, texts, *, purpose="document"):
        self.calls.append((purpose, tuple(texts)))
        if self.block:
            self.entered.set()
            await self.release.wait()
        if self.invalid:
            return [[float("nan"), 0, 0] for _ in texts]
        return [
            [1, 0, 0] if any(w in t for w in ("tea", "茗")) else [0, 1, 0] if "bike" in t else [0, 0, 1] for t in texts
        ]

    async def close(self):
        self.closed += 1


class NoModel:
    async def complete(self, *args):
        pytest.fail("Memory local semantics must not call a model")


async def seed(instance):
    agent = (await instance.application.query("agents", ""))[0]["id"]
    rows = [
        await instance.application.remember(agent, text)
        for text in ("I prefer tea", "Tea is tea", "I drink tea daily", "bike ride")
    ]
    return agent, rows


def test_index_hybrid_recall_profile_sources_and_one_shared_vector_owner(tmp_path):
    async def scenario():
        vectors = Vectors()
        instance = await open_instance(tmp_path, model=NoModel(), vectorizer=vectors, capabilities=())
        try:
            agent, memories = await seed(instance)
            owner = await instance.people.owner(agent)
            profile = await instance.people.save(agent, owner["id"], "profile", "Personal tea preference", None, 0)
            service = instance.memory_semantics
            assert vectors.calls == []
            first = await service.index(agent)
            assert len(first["items"]) == 5 and all(r["state"] == "ready" for r in first["items"])
            calls = len(vectors.calls)
            assert all(r["state"] == "ready" for r in (await service.index(agent))["items"])
            assert len(vectors.calls) == calls
            result = await service.search(agent, "茗")
            assert result["mode"] == "hybrid" and not result["degraded_reason"]
            assert memories[0]["id"] in {r["id"] for r in result["items"]}
            assert profile["entry_id"] in {r["id"] for r in result["items"]}
            assert all(r["provenance"] == "anchored" for r in result["items"])
            assert vectors.calls[-1][0] == "query" and vectors.calls[0][0] == "document"
            assert not (await instance._database.integrity())["foreign_keys"]
        finally:
            assert (await instance.close())["complete"]
        assert vectors.closed == 1

    asyncio.run(scenario())


def test_related_candidates_are_explicit_reversible_and_never_suppress_facts(tmp_path):
    async def scenario():
        instance = await open_instance(tmp_path, model=NoModel(), vectorizer=Vectors(), capabilities=())
        try:
            agent, memories = await seed(instance)
            service = instance.memory_semantics
            await service.index(agent)
            batch = await service.propose(agent, request([memories[0], memories[2]]))
            assert batch["valid"] and len(batch["links"]) == 1
            link = batch["links"][0]
            assert link["state"] == "pending"
            approved = await service.decide(agent, link["id"], decision(batch, link))
            assert approved["links"][0]["state"] == "accepted"
            assert {r["id"] for r in (await service.search(agent, "tea"))["items"]} >= {
                memories[0]["id"],
                memories[2]["id"],
            }
            revoked = await service.decide(agent, link["id"], decision(approved, approved["links"][0], "undo"))
            assert revoked["links"][0]["state"] == "revoked"
            assert revoked["links"][0]["review_audit_id"] and revoked["links"][0]["undo_audit_id"]
            assert (await service.propose(agent, request([memories[0], memories[2]])))["id"] == batch["id"]
            with pytest.raises(BusinessError) as conflict:
                await service.propose(agent, request(memories[:2]))
            assert conflict.value.code == "idempotency_conflict"
            with pytest.raises(BusinessError) as conflict:
                await service.decide(agent, link["id"], decision(batch, link))
            assert conflict.value.code == "revision_conflict"
            for table in (s.memory_semantic_batches, s.memory_semantic_sources, s.memory_semantic_links):
                with pytest.raises(BusinessError):
                    await instance._database.write(lambda u, t=table: u.connection.execute(delete(t)))
            assert all(r["deleted_at"] is None for r in await instance.application.query("memory", agent))
        finally:
            await instance.close()

    asyncio.run(scenario())


@pytest.mark.parametrize("change", ["forget", "anchor", "model", "restore"])
def test_source_or_model_change_rejects_review_and_clears_unavailable_sources(tmp_path, change):
    async def scenario():
        vectors = Vectors()
        instance = await open_instance(tmp_path, model=NoModel(), vectorizer=vectors, capabilities=())
        try:
            agent, memories = await seed(instance)
            service = instance.memory_semantics
            await service.index(agent)
            batch = await service.propose(agent, request([memories[0], memories[2]]))
            link = batch["links"][0]
            if change == "forget":
                await instance.application.forget(agent, memories[0]["id"])
            elif change == "anchor":
                await instance._database.write(
                    lambda u: u.connection.execute(
                        update(s.source_anchors)
                        .where(s.source_anchors.c.memory_id == memories[0]["id"])
                        .values(quote="changed evidence")
                    )
                )
            elif change == "model":
                vectors.version = "memory-fixture-v2"
            else:
                await instance._database.write(
                    lambda u: u.connection.execute(update(s.history_identity).values(epoch=u.ids.new()))
                )
            result = await service.get(agent, batch["id"])
            assert not result["valid"] and all("content" not in r for r in result["sources"])
            with pytest.raises(BusinessError) as rejected:
                await service.decide(agent, link["id"], decision(batch, link))
            assert rejected.value.code in {"memory_semantic_stale", "revision_conflict"}
            if change in {"forget", "anchor"}:
                rows = await instance._database.read(
                    lambda u: list(
                        u.connection.execute(
                            select(s.memory_embeddings).where(s.memory_embeddings.c.memory_id == memories[0]["id"])
                        ).mappings()
                    )
                )
                assert all(r["embedding"] is None and r["state"] == "invalidated" for r in rows)
            if change == "model":
                report = await service.search(agent, "茗")
                assert report["mode"] == "full_text" and report["degraded_reason"] == "vector_index_missing_or_stale"
        finally:
            await instance.close()

    asyncio.run(scenario())


def test_stale_index_publication_and_concurrent_page_keep_exact_ownership(tmp_path):
    async def scenario():
        vectors = Vectors()
        instance = await open_instance(tmp_path, model=NoModel(), vectorizer=vectors, capabilities=())
        try:
            agent, memories = await seed(instance)
            vectors.block = True
            task = asyncio.create_task(instance.memory_semantics.index(agent))
            await asyncio.wait_for(vectors.entered.wait(), 5)
            competing = await instance.memory_semantics.index(agent)
            assert all(r["state"] == "building" for r in competing["items"]) and len(vectors.calls) == 1
            await instance.application.forget(agent, memories[0]["id"])
            vectors.release.set()
            report = await task
            assert any(r["state"] == "invalidated" for r in report["items"])
            assert memories[0]["id"] not in {
                r["id"] for r in (await instance.memory_semantics.search(agent, "茗"))["items"]
            }
        finally:
            vectors.release.set()
            await instance.close()

    asyncio.run(scenario())


def test_caller_cancel_does_not_release_actual_index_or_close_shared_worker(tmp_path):
    async def scenario():
        vectors = Vectors()
        instance = await open_instance(tmp_path, model=NoModel(), vectorizer=vectors, capabilities=())
        try:
            agent, _ = await seed(instance)
            vectors.block = True
            task = asyncio.create_task(instance.memory_semantics.index(agent))
            await asyncio.wait_for(vectors.entered.wait(), 5)
            task.cancel()
            with pytest.raises(asyncio.CancelledError):
                await task
            assert instance.memory_semantics.health()["pending"] == 1
            assert not await instance.memory_semantics.close(0) and not vectors.closed
            vectors.release.set()
            assert await instance.memory_semantics.close(5) and not vectors.closed
        finally:
            vectors.release.set()
            assert (await instance.close())["complete"]
        assert vectors.closed == 1

    asyncio.run(scenario())


@pytest.mark.parametrize("installed", [False, True])
def test_missing_or_failed_vectorizer_honestly_uses_full_text(tmp_path, installed):
    async def scenario():
        vectors = Vectors() if installed else None
        if vectors:
            vectors.invalid = True
        instance = await open_instance(tmp_path, model=NoModel(), vectorizer=vectors, capabilities=())
        try:
            agent, memories = await seed(instance)
            if vectors:
                with pytest.raises(BusinessError):
                    await instance.memory_semantics.index(agent)
            result = await instance.memory_semantics.search(agent, "tea")
            assert result["mode"] == "full_text" and result["degraded_reason"]
            assert memories[0]["id"] in {r["id"] for r in result["items"]}
            with pytest.raises(BusinessError):
                await instance.memory_semantics.propose(agent, request(memories[:2]))
            other = (await instance._database.write(lambda u: u.create_agent("Other")))["id"]
            assert not (await instance.memory_semantics.search(other, "tea"))["items"]
        finally:
            await instance.close()

    asyncio.run(scenario())


def test_erasure_during_query_never_returns_previously_loaded_text(tmp_path):
    async def scenario():
        vectors = Vectors()
        instance = await open_instance(tmp_path, model=NoModel(), vectorizer=vectors, capabilities=())
        try:
            agent, memories = await seed(instance)
            await instance.memory_semantics.index(agent)
            vectors.block = True
            task = asyncio.create_task(instance.memory_semantics.search(agent, "tea"))
            await asyncio.wait_for(vectors.entered.wait(), 5)
            await instance.application.forget(agent, memories[0]["id"])
            vectors.release.set()
            with pytest.raises(BusinessError):
                await task
        finally:
            vectors.release.set()
            await instance.close()

    asyncio.run(scenario())


def test_model_tool_uses_hybrid_recall_and_captures_exact_memory_dependencies(tmp_path):
    from test_v2_execution import wait_state
    from test_v2_routing import Model
    from v2_configuration_helpers import model_instance

    from omni.domain.contracts import Invocation, ModelReply

    async def scenario():
        model = Model([ModelReply("recall", (Invocation("recall", "memory_search", {"query": "茗"}),), cost_micro=7)])
        instance = await open_instance(tmp_path, model=model, vectorizer=Vectors())
        try:
            agent, memories = await seed(instance)
            await model_instance(instance, agent, model="fixture")
            await instance.memory_semantics.index(agent)
            turn = await instance.application.submit_turn(agent, None, "model-recall", "hello", mode="chat")
            run = await wait_state(instance.application, agent, turn["run_id"], {"succeeded", "failed"})
            assert run["state"] == "succeeded", run
            rows = await instance._database.read(
                lambda u: list(u.connection.execute(select(s.context_memories.c.memory_id)).scalars())
            )
            assert memories[0]["id"] in rows
            assert "I prefer tea" in str(model.requests[-1]["messages"])
            await instance.application.forget(agent, memories[0]["id"])
            with pytest.raises(BusinessError):
                await instance._database.read(lambda u: u.assert_context(agent, turn["run_id"]))
        finally:
            await instance.close()

    asyncio.run(scenario())


def test_semantic_candidates_never_cross_person_scope_and_identical_text_uses_exact_organizer(tmp_path):
    async def scenario():
        instance = await open_instance(tmp_path, model=NoModel(), vectorizer=Vectors(), capabilities=())
        try:
            agent, memories = await seed(instance)
            person = await instance.people.create(agent, "Other")
            scoped = await instance._database.write(
                lambda u: u.remember(agent, "Other likes tea", person_id=person["id"])
            )
            duplicate = await instance.application.remember(agent, memories[0]["content"])
            await instance.memory_semantics.index(agent)
            result = await instance.memory_semantics.propose(agent, request([memories[0], scoped]))
            assert result["candidate_count"] == 0
            result = await instance.memory_semantics.propose(agent, request([memories[0], duplicate], "exact"))
            assert result["candidate_count"] == 0
        finally:
            await instance.close()

    asyncio.run(scenario())


def test_restart_reconciles_incomplete_index_without_reusing_its_owner(tmp_path):
    async def scenario():
        instance = await open_instance(tmp_path, model=NoModel(), vectorizer=Vectors(), capabilities=())
        try:
            agent, memories = await seed(instance)

            def begin(u):
                source = u.memory_semantic_capture(agent, memories[0]["id"], 1)
                return u.memory_embedding_begin(agent, source, "memory-fixture-v1", 3)

            row = await instance._database.write(begin)
        finally:
            assert (await instance.close())["complete"]
        reopened = await open_instance(tmp_path, model=NoModel(), vectorizer=Vectors(), capabilities=())
        try:
            old = await reopened._database.read(lambda u: u._one(s.memory_embeddings, agent, row["id"]))
            assert old["state"] == "failed" and old["error_code"] == "index_interrupted" and old["embedding"] is None
            assert all(r["state"] == "ready" for r in (await reopened.memory_semantics.index(agent))["items"])
        finally:
            await reopened.close()

    asyncio.run(scenario())


def test_consistent_restore_discards_derived_vectors_and_old_semantic_authority(tmp_path):
    from omni.adapters.persistence.maintenance import backup, restore

    async def scenario():
        source = tmp_path / "source"
        instance = await open_instance(source, model=NoModel(), vectorizer=Vectors(), capabilities=())
        try:
            agent, memories = await seed(instance)
            await instance.memory_semantics.index(agent)
            batch = await instance.memory_semantics.propose(agent, request([memories[0], memories[2]]))
        finally:
            assert (await instance.close())["complete"]
        snapshot, destination = tmp_path / "snapshot", tmp_path / "restored"
        backup(instance.paths, snapshot)
        restore(snapshot, destination)
        restored = await open_instance(destination, model=NoModel(), vectorizer=Vectors(), capabilities=())
        try:
            report = await restored.memory_semantics.search(agent, "tea")
            assert report["mode"] == "full_text" and report["degraded_reason"] == "vector_index_missing_or_stale"
            assert not (await restored.memory_semantics.get(agent, batch["id"]))["valid"]
            assert memories[0]["id"] in {r["id"] for r in report["items"]}
            assert all(r["state"] == "ready" for r in (await restored.memory_semantics.index(agent))["items"])
        finally:
            await restored.close()

    asyncio.run(scenario())
