"""Source objects, exact revisions, local indexes and erasure dependency regressions."""

import asyncio

import pytest
from sqlalchemy import select, update
from test_v2_objects import Clock, opened

from omni.adapters.persistence import schema as s
from omni.application.documents import DocumentService
from omni.domain.contracts import ActionResult, BusinessError, InvocationContext, Outcome, ToolSpec


class LocalVectors:
    version = "toy-local-v1"
    dimension = 2

    def __init__(self):
        self.calls, self.closed, self.invalid = [], False, False

    def status(self):
        return {"available": True, "state": "fixture"}

    async def embed(self, texts, *, purpose="document"):
        self.calls.append(tuple(texts))
        if self.invalid:
            return [[float("nan"), 0] for _ in texts]
        return [[1, 0] if any(word in t for word in ("journey", "旅途")) else [0, 1] for t in texts]

    async def close(self):
        self.closed = True


def read_action(u, agent, tool="document_read", turn=None):
    if turn is None:
        turn = u.accept_turn(agent, None, "read", "read source", {})
        u.transition(agent, turn["run_id"], "running")
        u.prepare_context(agent, turn["run_id"])
    action = u.prepare_action(agent, turn["run_id"], ToolSpec(tool, "source", {}, False), {}, 1)
    u.authorize_action(agent, action["id"])
    return turn, action


def test_source_document_revisions_objects_cas_and_isolation(tmp_path):
    async def scenario():
        async with opened(tmp_path, Clock()) as (_, db, objects, agent):
            service = DocumentService(db, objects)
            first = await service.save(agent, "First", "明确的来源原文\nhello source")
            assert (await service.get(agent, first["id"]))["content"] == "明确的来源原文\nhello source"
            edited = await service.save(agent, "Second", "new source text", first["id"], 1)
            assert edited["revision"] == 2
            assert (await service.get(agent, first["id"], first["revision_id"]))["content"].startswith("明确")
            with pytest.raises(BusinessError) as error:
                await service.save(agent, "stale", "never overwrite", first["id"], 1)
            assert error.value.code == "revision_conflict"
            assert len((await service.history(agent, first["id"]))["items"]) == 2
            other = (await db.write(lambda u: u.create_agent("other")))["id"]
            with pytest.raises(BusinessError) as error:
                await service.get(other, first["id"])
            assert error.value.status == 404
            assert (await objects.describe(agent, first["object_id"]))["references"] == 1
            with pytest.raises(BusinessError, match="constraint"):
                await db.write(
                    lambda u: u.connection.execute(
                        update(s.document_revisions)
                        .where(s.document_revisions.c.id == first["revision_id"])
                        .values(sha256="0" * 64)
                    )
                )
            assert (await db.integrity())["foreign_keys"] == 0
            assert await service.close()

    asyncio.run(scenario())


def test_local_vectors_are_explicit_versioned_and_stale_revisions_do_not_resurface(tmp_path):
    async def scenario():
        async with opened(tmp_path, Clock()) as (_, db, objects, agent):
            vectorizer = LocalVectors()
            service = DocumentService(db, objects, vectorizer)
            journey = await service.save(agent, "Travel", "A journey remembered")
            await service.save(agent, "Other", "An unrelated source")
            assert not vectorizer.calls
            assert (await service.rebuild(agent, journey["id"], "vector"))["state"] == "ready"
            result = await service.search(agent, "旅途")
            assert result["mode"] == "hybrid" and result["items"][0]["document_id"] == journey["id"]
            vectorizer.version = "toy-local-v2"
            result = await service.search(agent, "旅途")
            assert result["mode"] == "full_text" and result["degraded_reason"] == "vector_index_missing_or_stale"
            assert (await service.rebuild(agent, journey["id"], "vector"))["state"] == "ready"
            vectorizer.invalid = True
            with pytest.raises(BusinessError, match="computation failed"):
                await service.rebuild(agent, journey["id"], "vector")
            assert (await service.index_status(agent, journey["id"]))["items"]
            result = await service.search(agent, "journey")
            assert result["mode"] == "full_text" and result["degraded_reason"] == "vector_unavailable"
            vectorizer.invalid = False
            await service.save(agent, "Travel edited", "no previous material", journey["id"], 1)
            assert not (await service.search(agent, "旅途"))["items"]
            assert max(len(batch) for batch in vectorizer.calls) <= 8
            assert await service.close() and vectorizer.closed

    asyncio.run(scenario())


def test_document_anchor_requires_exact_revision_quote_and_observed_model_source(tmp_path):
    async def scenario():
        async with opened(tmp_path, Clock()) as (_, db, objects, agent):
            service = DocumentService(db, objects)
            content = "a" * 950 + "明确的跨块来源" * 30
            source = await service.save(agent, "Evidence", content)
            quote = content[970:1100]
            fact = await db.write(
                lambda u: u.remember(agent, "Anchored fact", document_revision_id=source["revision_id"], quote=quote)
            )
            evidence = (await db.read(lambda u: u.memory_history(agent, fact["id"])))[0]["sources"][0]
            assert evidence["source_start"] == content.index(quote) and evidence["source_end"] == content.index(
                quote
            ) + len(quote)
            with pytest.raises(BusinessError) as error:
                await db.write(
                    lambda u: u.remember(
                        agent, "forged", document_revision_id=source["revision_id"], quote="not in source"
                    )
                )
            assert error.value.code == "invalid_evidence"
            with pytest.raises(BusinessError) as error:
                await db.write(
                    lambda u: u.remember(
                        agent,
                        "unobserved",
                        document_revision_id=source["revision_id"],
                        quote=quote,
                        model_authored=True,
                    )
                )
            assert error.value.code == "evidence_required"
            turn, action = await db.write(lambda u: read_action(u, agent))
            context = InvocationContext(agent, turn["run_id"], turn["session_id"], 1)
            result = await service.invoke(
                "document_read", {"document_id": source["id"], "start": 900}, operation_id=action["id"], context=context
            )
            assert result.status == Outcome.OK
            await db.write(lambda u: u.settle_action(agent, action["id"], result))
            with pytest.raises(BusinessError) as error:
                await db.write(
                    lambda u: u.remember(
                        agent,
                        "unseen quote",
                        document_revision_id=source["revision_id"],
                        quote="a" * 100,
                        model_authored=True,
                        source_run_id=turn["run_id"],
                    )
                )
            assert error.value.code == "evidence_required"
            model_fact = await db.write(
                lambda u: u.remember(
                    agent,
                    "Model fact",
                    document_revision_id=source["revision_id"],
                    quote=quote,
                    model_authored=True,
                    source_run_id=turn["run_id"],
                )
            )
            assert model_fact["content"] == "Model fact"
            other = (await db.write(lambda u: u.create_agent("Other")))["id"]
            with pytest.raises(BusinessError):
                await db.write(
                    lambda u: u.remember(other, "leak", document_revision_id=source["revision_id"], quote=quote)
                )
            assert (await db.integrity())["foreign_keys"] == 0
            assert await service.close()

    asyncio.run(scenario())


def test_erasure_invalidates_registered_context_and_late_tool_settlement_cannot_restore_text(tmp_path):
    async def scenario():
        async with opened(tmp_path, Clock()) as (_, db, objects, agent):
            service = DocumentService(db, objects)
            source = await service.save(agent, "Private source", "document-erasure-canary")
            turn, action = await db.write(lambda u: read_action(u, agent))
            result = await service.invoke(
                "document_search",
                {"query": "document-erasure-canary"},
                operation_id=action["id"],
                context=InvocationContext(agent, turn["run_id"], turn["session_id"], 1),
            )
            assert result.status == Outcome.OK and "document-erasure-canary" in result.text
            memory = await db.write(
                lambda u: u.remember(
                    agent, "derived", document_revision_id=source["revision_id"], quote="document-erasure-canary"
                )
            )
            await service.erase(agent, source["id"], 1)
            await db.write(lambda u: u.settle_action(agent, action["id"], result))
            settled = await db.read(lambda u: u.action(agent, action["id"]))
            assert settled["state"] == "error" and "canary" not in settled["result"]["text"]
            with pytest.raises(BusinessError) as error:
                await db.read(lambda u: u.assert_context(agent, turn["run_id"]))
            assert error.value.code == "context_invalidated"
            assert not (await db.read(lambda u: u.memory_history(agent, memory["id"])))[0]["content"]
            assert not (await service.search(agent, "canary"))["items"]
            assert (await db.read(lambda u: u.document_erasure_status(agent, source["id"])))["state"] == "done"
            await service.erase(agent, source["id"], 1)
            assert await service.close()

    asyncio.run(scenario())


def test_failed_erasure_rolls_back_all_content_and_records_retryable_job(tmp_path, monkeypatch):
    from omni.adapters.persistence.memory_repository import MemoryRepositories

    original = MemoryRepositories._forget_one

    def cut(self, *args, **kwargs):
        original(self, *args, **kwargs)
        raise RuntimeError("fault after derived erasure")

    async def scenario():
        async with opened(tmp_path, Clock()) as (_, db, objects, agent):
            service = DocumentService(db, objects)
            source = await service.save(agent, "Source", "original material")
            await db.write(
                lambda u: u.remember(
                    agent, "fact", document_revision_id=source["revision_id"], quote="original material"
                )
            )
            with monkeypatch.context() as patch:
                patch.setattr(MemoryRepositories, "_forget_one", cut)
                with pytest.raises(RuntimeError, match="fault"):
                    await service.erase(agent, source["id"], 1)
            assert (await service.get(agent, source["id"]))["content"] == "original material"
            assert (await service.search(agent, "material"))["items"]
            job = await db.read(lambda u: u.document_erasure_status(agent, source["id"]))
            assert job["state"] == "failed" and job["error_code"] == "erasure_interrupted"
            await service.erase(agent, source["id"], 1)
            job = await db.read(lambda u: u.document_erasure_status(agent, source["id"]))
            assert job["state"] == "done" and job["attempt"] == 2
            assert (await objects.describe(agent, source["object_id"]))["references"] == 0
            assert await service.close()

    asyncio.run(scenario())


def test_tool_result_evidence_requires_success_and_current_observation(tmp_path):
    async def scenario():
        async with opened(tmp_path, Clock()) as (_, db, _, agent):
            turn, action = await db.write(lambda u: read_action(u, agent, "files_read"))
            with pytest.raises(BusinessError):
                await db.write(
                    lambda u: u.remember(agent, "premature", action_id=action["id"], quote="verified tool quote")
                )
            await db.write(
                lambda u: u.settle_action(agent, action["id"], ActionResult(Outcome.OK, "verified tool quote"))
            )
            fact = await db.write(
                lambda u: u.remember(
                    agent,
                    "tool fact",
                    action_id=action["id"],
                    quote="tool quote",
                    model_authored=True,
                    source_run_id=turn["run_id"],
                )
            )
            assert fact["content"] == "tool fact"
            with pytest.raises(BusinessError):
                await db.write(
                    lambda u: u.remember(
                        agent,
                        "other run",
                        action_id=action["id"],
                        quote="tool quote",
                        model_authored=True,
                        source_run_id="unobserved",
                    )
                )

    asyncio.run(scenario())


def test_document_erasure_during_model_request_discards_output_and_keeps_known_cost(tmp_path):
    from v2_configuration_helpers import model_instance

    from omni.composition.instance import open_instance
    from omni.domain.contracts import Invocation, ModelReply

    class Model:
        def __init__(self):
            self.started, self.release = asyncio.Event(), asyncio.Event()
            self.calls = 0

        async def complete(self, messages, tools, settings):
            self.calls += 1
            if self.calls == 1:
                return ModelReply(
                    "", (Invocation("source-call", "document_search", {"query": "live-source-canary"}),), cost_micro=7
                )
            assert "live-source-canary" in messages[-1]["content"]
            self.started.set()
            await self.release.wait()
            return ModelReply("stale source answer", cost_micro=7)

    async def scenario():
        model = Model()
        instance = await open_instance(tmp_path, model=model)
        try:
            app = instance.application
            agent = (await app.query("agents", ""))[0]["id"]
            await model_instance(instance, agent)
            document = await instance.documents.save(agent, "Private", "live-source-canary")
            turn = await app.submit_turn(agent, None, "source-run", "retrieve source")
            async with asyncio.timeout(15):
                await model.started.wait()
            await instance.documents.erase(agent, document["id"], 1)
            model.release.set()
            async with asyncio.timeout(15):
                while (run := await app.get_run(agent, turn["run_id"]))["state"] not in {"failed", "succeeded"}:
                    await asyncio.sleep(0.01)
            assert run["state"] == "failed" and run["error_code"] == "context_invalidated"
            calls = await app.query("calls", agent)
            assert len(calls) == 2 and all(c["state"] == "settled" and c["cost_micro"] == 7 for c in calls)
            actions = await app.query("actions", agent)
            assert "canary" not in str(actions[0]["result"])
            assert not any(m["content"] == "stale source answer" for m in await app.messages(agent, turn["session_id"]))
            report = await app.context_report(agent, turn["run_id"])
            assert report["snapshots"][0]["documents"][0]["redacted_at"]
        finally:
            model.release.set()
            assert (await instance.close())["complete"]

    asyncio.run(scenario())


def test_document_indexes_and_object_references_survive_consistent_backup_restore(tmp_path):
    from omni.adapters.persistence.maintenance import backup, restore

    async def setup():
        async with opened(tmp_path / "source", Clock()) as (paths, db, objects, agent):
            service = DocumentService(db, objects, LocalVectors())
            row = await service.save(agent, "Source", "A journey with exact provenance")
            await service.rebuild(agent, row["id"], "vector")
            await db.write(
                lambda u: u.remember(agent, "fact", document_revision_id=row["revision_id"], quote="exact provenance")
            )
            await service.close()
            return paths, agent, row

    paths, agent, row = asyncio.run(setup())
    manifest = backup(paths, tmp_path / "snapshot")
    assert len(manifest["files"]) == 2
    assert restore(tmp_path / "snapshot", tmp_path / "restored")["database"]["integrity"] == "ok"

    async def verify():
        async with opened(tmp_path / "restored", Clock()) as (_, db, objects, _):
            service = DocumentService(db, objects, LocalVectors())
            assert (await service.get(agent, row["id"]))["content"] == "A journey with exact provenance"
            assert (await service.search(agent, "旅途"))["mode"] == "hybrid"
            assert (await objects.describe(agent, row["object_id"]))["references"] == 1
            assert (await db.integrity())["foreign_keys"] == 0
            assert await service.close()

    asyncio.run(verify())


def test_chunk_damage_requires_verified_rebuild_and_failed_rebuild_preserves_old_index(tmp_path, monkeypatch):
    from omni.adapters.persistence.document_repository import DocumentRepositories

    original = DocumentRepositories._document_chunks

    def cut(self, *args, **kwargs):
        original(self, *args, **kwargs)
        raise RuntimeError("replace fault")

    async def scenario():
        async with opened(tmp_path, Clock()) as (_, db, objects, agent):
            service = DocumentService(db, objects)
            row = await service.save(agent, "Source", "original source")
            with monkeypatch.context() as patch:
                patch.setattr(DocumentRepositories, "_document_chunks", cut)
                with pytest.raises(RuntimeError):
                    await service.rebuild(agent, row["id"], "full_text")
            assert (await service.search(agent, "original"))["items"]
            await db.write(
                lambda u: u.connection.execute(
                    update(s.document_chunks)
                    .where(s.document_chunks.c.revision_id == row["revision_id"])
                    .values(content="tampered source")
                )
            )
            with pytest.raises(BusinessError) as error:
                await service.search(agent, "tampered")
            assert error.value.code == "document_index_invalid"
            assert (await service.rebuild(agent, row["id"], "full_text"))["state"] == "ready"
            assert (await service.search(agent, "original"))["items"]
            assert not (await service.search(agent, "tampered"))["items"]
            assert await service.close()

    asyncio.run(scenario())


def test_document_queue_cancellation_keeps_work_charged_and_shutdown_reports_residue(tmp_path):
    class Delayed(LocalVectors):
        def __init__(self):
            super().__init__()
            self.started, self.release = asyncio.Event(), asyncio.Event()

        async def embed(self, texts, *, purpose="document"):
            self.started.set()
            await self.release.wait()
            return await super().embed(texts)

    async def scenario():
        async with opened(tmp_path, Clock()) as (_, db, objects, agent):
            vectors = Delayed()
            service = DocumentService(db, objects, vectors)
            clients = [asyncio.create_task(service.search(agent, "query")) for _ in range(8)]
            async with asyncio.timeout(10):
                await vectors.started.wait()
            with pytest.raises(BusinessError) as error:
                await service.search(agent, "extra")
            assert error.value.code == "documents_busy"
            clients[0].cancel()
            with pytest.raises(asyncio.CancelledError):
                await clients[0]
            assert service.health()["pending"] == 8
            assert not await service.close(timeout_s=0)
            vectors.release.set()
            await asyncio.gather(*clients[1:])
            assert await service.close() and vectors.closed

    asyncio.run(scenario())


@pytest.mark.parametrize("operation", ["edit", "erase"])
def test_vector_rebuild_cannot_publish_against_replaced_or_erased_source(tmp_path, operation):
    class Delayed(LocalVectors):
        def __init__(self):
            super().__init__()
            self.started, self.release = asyncio.Event(), asyncio.Event()

        async def embed(self, texts, *, purpose="document"):
            self.started.set()
            await self.release.wait()
            return await super().embed(texts)

    async def scenario():
        async with opened(tmp_path, Clock()) as (_, db, objects, agent):
            vector = Delayed()
            service = DocumentService(db, objects, vector)
            source = await service.save(agent, "Source", "journey original")
            task = asyncio.create_task(service.rebuild(agent, source["id"], "vector"))
            async with asyncio.timeout(10):
                await vector.started.wait()
            if operation == "edit":
                await service.save(agent, "Changed", "replacement", source["id"], 1)
            else:
                await service.erase(agent, source["id"], 1)
            vector.release.set()
            assert (await task)["state"] == "invalidated"
            assert not (await service.search(agent, "journey"))["items"]
            assert await service.close()

    asyncio.run(scenario())


def test_vector_scan_is_bounded_and_missing_text_indexes_are_visible(tmp_path):
    async def scenario():
        async with opened(tmp_path, Clock()) as (_, db, objects, agent):
            service = DocumentService(db, objects, LocalVectors())
            rows = []
            for i in range(6):
                row = await service.save(agent, f"Source {i}", ("journey " * 12500))
                rows.append(row)
                await service.rebuild(agent, row["id"], "vector")
            result = await service.search(agent, "旅途")
            assert result["mode"] == "hybrid" and result["vector_candidates_limited"]
            assert len(result["items"]) <= 20
            await db.write(
                lambda u: u.connection.execute(
                    update(s.document_revisions)
                    .where(s.document_revisions.c.id == rows[0]["revision_id"])
                    .values(text_index_version="older-version")
                )
            )
            result = await service.search(agent, "journey")
            assert result["unindexed_documents"] == 1
            assert (await service.rebuild(agent, rows[0]["id"], "full_text"))["state"] == "ready"
            assert (await service.search(agent, "journey"))["unindexed_documents"] == 0
            assert await service.close()

    asyncio.run(scenario())


def test_newer_erasure_resolves_old_failed_requests_and_late_error_cannot_downgrade_done(tmp_path, monkeypatch):
    from omni.adapters.persistence.maintenance import inspect_database
    from omni.adapters.persistence.memory_repository import MemoryRepositories

    original = MemoryRepositories._forget_one

    def cut(self, *args, **kwargs):
        original(self, *args, **kwargs)
        raise RuntimeError("cleanup fault")

    async def scenario():
        async with opened(tmp_path, Clock()) as (paths, db, objects, agent):
            service = DocumentService(db, objects)
            source = await service.save(agent, "Source", "original source")
            await db.write(
                lambda u: u.remember(agent, "fact", document_revision_id=source["revision_id"], quote="original source")
            )
            with monkeypatch.context() as patch:
                patch.setattr(MemoryRepositories, "_forget_one", cut)
                with pytest.raises(RuntimeError):
                    await service.erase(agent, source["id"], 1)
            await service.save(agent, "Changed", "updated source", source["id"], 1)
            await service.erase(agent, source["id"], 2)
            jobs = await db.read(
                lambda u: [
                    dict(r)
                    for r in u.connection.execute(
                        select(s.document_erasure_jobs).where(s.document_erasure_jobs.c.document_id == source["id"])
                    ).mappings()
                ]
            )
            assert len(jobs) == 2 and all(r["state"] == "done" for r in jobs)
            await db.write(lambda u: u.document_erasure_finish(agent, jobs[-1]["id"], "erasure_interrupted"))
            assert (await db.read(lambda u: u.document_erasure_status(agent, source["id"])))["state"] == "done"
            events = await db.read(lambda u: u.query("audit", agent, limit=200))
            assert any(e["kind"] == "document.erasure_failed" for e in events)
            assert any(e["kind"] == "document.erasure_resolved" and e["data"]["resolved_requests"] == 2 for e in events)
            assert await service.close()
            return paths

    paths = asyncio.run(scenario())
    assert inspect_database(paths.database)["document_erasure_attention"] == 0


def test_fulltext_rebuild_from_verified_object_and_default_vector_degradation(tmp_path):
    async def scenario():
        async with opened(tmp_path, Clock()) as (_, db, objects, agent):
            service = DocumentService(db, objects)
            first = await service.save(agent, "Source", "prefix 中文子串检索 suffix")
            result = await service.search(agent, "子串检索")
            assert result["mode"] == "full_text" and result["degraded_reason"] == "local_vectorizer_not_installed"
            assert result["items"][0]["document_revision_id"] == first["revision_id"]
            with pytest.raises(BusinessError) as error:
                await service.rebuild(agent, first["id"], "vector")
            assert error.value.code == "vector_unavailable"
            result = await service.rebuild(agent, first["id"], "full_text")
            assert result["state"] == "ready"
            assert (await service.search(agent, "子串检索"))["items"]
            await service.erase(agent, first["id"], 1)
            assert not (await service.search(agent, "子串检索"))["items"]
            assert (await objects.describe(agent, first["object_id"]))["references"] == 0
            revisions = (await service.history(agent, first["id"]))["items"]
            assert revisions[0]["object_id"] is None and revisions[0]["redacted_at"]
            assert not await db.read(lambda u: list(u.connection.execute(select(s.document_chunks)).all()))
            assert await service.close()

    asyncio.run(scenario())
