"""Exact bounded proposals, owner CAS and reversible recall use isolated real storage, without a model."""

from __future__ import annotations

from omni_test_support.v2.memory_organization_repository import (
    BusinessError as BusinessError,
)
from omni_test_support.v2.memory_organization_repository import (
    Clock as Clock,
)
from omni_test_support.v2.memory_organization_repository import (
    asyncio as asyncio,
)
from omni_test_support.v2.memory_organization_repository import (
    candidate_memories as candidate_memories,
)
from omni_test_support.v2.memory_organization_repository import (
    document_request as document_request,
)
from omni_test_support.v2.memory_organization_repository import (
    environment as environment,
)
from omni_test_support.v2.memory_organization_repository import (
    facts as facts,
)
from omni_test_support.v2.memory_organization_repository import (
    json as json,
)
from omni_test_support.v2.memory_organization_repository import (
    memory_truth as memory_truth,
)
from omni_test_support.v2.memory_organization_repository import (
    opened as opened,
)
from omni_test_support.v2.memory_organization_repository import (
    organization_audits as organization_audits,
)
from omni_test_support.v2.memory_organization_repository import (
    organization_request as organization_request,
)
from omni_test_support.v2.memory_organization_repository import (
    owner_decision as owner_decision,
)
from omni_test_support.v2.memory_organization_repository import (
    proposal as proposal,
)
from omni_test_support.v2.memory_organization_repository import (
    pytest as pytest,
)
from omni_test_support.v2.memory_organization_repository import (
    s as s,
)
from omni_test_support.v2.memory_organization_repository import (
    select as select,
)
from omni_test_support.v2.memory_organization_repository import (
    text as text,
)
from omni_test_support.v2.memory_organization_repository import (
    update as update,
)


def test_grouping_uses_original_bytes_and_exact_person_and_session_scope(tmp_path):
    async def scenario():
        async with opened(tmp_path, Clock()) as (_, db, _, agent):

            def seed(unit):
                person = unit.person_create(agent, "Scoped person")
                turn = unit.accept_turn(agent, None, "scope", "scope input", {})
                rows = facts(unit, agent, "ＡＢＣ") + facts(unit, agent, "ABC", 1)
                rows += facts(unit, agent, "ＡＢＣ", 2, person_id=person["id"])
                session_rows = facts(unit, agent, "ＡＢＣ")
                # These are valid session-scoped facts before proposal capture, not fabricated execution states.
                for row in session_rows:
                    unit.connection.execute(
                        update(s.memory_entries)
                        .where(s.memory_entries.c.id == row["id"])
                        .values(session_id=turn["session_id"])
                    )
                return rows + session_rows

            memories = await db.write(seed)
            batch = await proposal(db, agent, list(reversed(memories)))
            assert batch["state"] == "ready" and batch["valid"] and len(batch["candidates"]) == 3
            selected = {row["id"]: row for row in batch["sources"]}
            pairs = []
            for candidate in batch["candidates"]:
                keep, duplicate = selected[candidate["keep_source_id"]], selected[candidate["duplicate_source_id"]]
                assert keep["content"].encode() == duplicate["content"].encode() == "ＡＢＣ".encode()
                assert (keep["person_id"], keep["session_id"]) == (duplicate["person_id"], duplicate["session_id"])
                assert keep["position"] < duplicate["position"]
                pairs.append((keep["person_id"], keep["session_id"]))
            assert len(set(pairs)) == 3
            assert [source["memory_id"] for source in batch["sources"]] == sorted(row["id"] for row in memories)
            assert batch["budget_micro"] == 0 and not await db.read(lambda unit: unit.query("calls", agent))

    asyncio.run(scenario())


@pytest.mark.parametrize("excess", ["bytes", "anchors"])
def test_capture_rejects_oversized_utf8_or_too_many_anchors_without_publishing_a_batch(tmp_path, excess):
    async def scenario():
        async with opened(tmp_path, Clock()) as (_, db, _, agent):

            def seed(unit):
                rows = facts(unit, agent, "界" * 20_000, 5) if excess == "bytes" else facts(unit, agent)
                if excess == "anchors":
                    revision = unit.connection.execute(
                        select(s.memory_revisions.c.id).where(s.memory_revisions.c.memory_id == rows[0]["id"])
                    ).scalar_one()
                    for _ in range(8):
                        unit._evidence(agent, rows[0]["id"], revision)
                return rows

            memories = await db.write(seed)
            with pytest.raises(BusinessError) as error:
                await db.write(lambda unit: unit.memory_organization_enqueue(agent, organization_request(memories)))
            assert error.value.code == (
                "memory_organization_budget" if excess == "bytes" else "memory_organization_sources"
            )
            assert await db.read(lambda unit: unit.memory_organization_page(agent)) == []
            assert await db.read(lambda unit: organization_audits(unit, agent, "memory.organization.queued")) == []
            assert (await db.integrity())["foreign_keys"] == 0

    asyncio.run(scenario())


def test_shared_capacity_counts_ready_reviews_and_documents_and_replay_precedes_source_revalidation(tmp_path):
    async def scenario():
        async with environment(tmp_path) as (_, db, documents, agent):
            source = await documents.save(agent, "Capacity source", "document content")
            memories = await db.write(lambda unit: facts(unit, agent))
            batch = await proposal(db, agent, memories)
            assert batch["state"] == "ready"

            def fill(unit):
                return [
                    unit.maintenance_enqueue(agent, document_request(source, f"document-{index}"))
                    for index in range(63)
                ]

            jobs = await db.write(fill)
            assert len(jobs) == 63
            for operation in (
                lambda unit: unit.maintenance_enqueue(agent, document_request(source, "overflow-document")),
                lambda unit: unit.memory_organization_enqueue(agent, organization_request(memories, "overflow-memory")),
            ):
                with pytest.raises(BusinessError) as error:
                    await db.write(operation)
                assert error.value.code == "maintenance_busy"
            assert (
                await db.write(lambda unit: unit.memory_organization_enqueue(agent, organization_request(memories)))
            )["id"] == batch["id"]
            await db.write(lambda unit: unit.revise_memory(agent, memories[0]["id"], "source changed", 1))
            replay = await db.write(
                lambda unit: unit.memory_organization_enqueue(agent, organization_request(memories))
            )
            assert replay["id"] == batch["id"] and replay["state"] == "invalidated" and not replay["valid"]
            assert all("content" not in row for row in replay["sources"])
            with pytest.raises(BusinessError) as error:
                await db.write(
                    lambda unit: unit.memory_organization_enqueue(
                        agent, organization_request(memories, "stale-new-key")
                    )
                )
            assert error.value.code == "memory_organization_stale"
            changed = [{**memories[0], "revision": 2}, memories[1]]
            with pytest.raises(BusinessError) as error:
                await db.write(lambda unit: unit.memory_organization_enqueue(agent, organization_request(changed)))
            assert error.value.code == "idempotency_conflict"
            assert (
                await db.write(lambda unit: unit.maintenance_enqueue(agent, document_request(source, "document-0")))
            )["id"] == jobs[0]["id"]

    asyncio.run(scenario())


def test_concurrent_owner_decisions_have_one_cas_winner_and_one_review_audit(tmp_path):
    async def scenario():
        async with opened(tmp_path, Clock()) as (_, db, _, agent):
            memories = await db.write(lambda unit: facts(unit, agent))
            batch = await proposal(db, agent, memories)
            candidate = batch["candidates"][0]
            results = await asyncio.gather(
                *[
                    db.write(
                        lambda unit, action=action: unit.memory_organization_decide(
                            agent, candidate["id"], owner_decision(batch, candidate, action)
                        )
                    )
                    for action in ("apply", "reject")
                ],
                return_exceptions=True,
            )
            wins = [result for result in results if isinstance(result, dict)]
            failures = [result for result in results if isinstance(result, BusinessError)]
            assert len(wins) == len(failures) == 1 and failures[0].code == "revision_conflict"
            final = wins[0]
            assert final["state"] == "complete" and final["revision"] == batch["revision"] + 1
            assert final["candidates"][0]["revision"] == candidate["revision"] + 1
            audits = await db.read(
                lambda unit: (
                    organization_audits(unit, agent, "memory.organization.applied")
                    + organization_audits(unit, agent, "memory.organization.rejected")
                )
            )
            assert len(audits) == 1 and audits[0]["id"] == final["candidates"][0]["review_audit_id"]
            with pytest.raises(BusinessError) as error:
                await db.write(
                    lambda unit: unit.memory_organization_decide(
                        agent,
                        candidate["id"],
                        {
                            **owner_decision(final, final["candidates"][0]),
                            "epoch": "00000000-0000-0000-0000-000000000000",
                        },
                    )
                )
            assert error.value.code == "memory_organization_stale"

    asyncio.run(scenario())


@pytest.mark.parametrize(("existing", "proposed"), [((0, 1), (1, 2)), ((1, 2), (0, 1)), ((0, 2), (1, 2))])
def test_applied_keeper_duplicate_and_chain_edges_cannot_be_suppressed_twice(tmp_path, existing, proposed):
    async def scenario():
        async with opened(tmp_path, Clock()) as (_, db, _, agent):
            memories = await db.write(lambda unit: facts(unit, agent, count=3))
            first = await proposal(db, agent, [memories[index] for index in existing], "first")
            await db.write(
                lambda unit: unit.memory_organization_decide(
                    agent, first["candidates"][0]["id"], owner_decision(first, first["candidates"][0])
                )
            )
            second = await proposal(db, agent, [memories[index] for index in proposed], "second")
            candidate = second["candidates"][0]
            with pytest.raises(BusinessError) as error:
                await db.write(
                    lambda unit: unit.memory_organization_decide(
                        agent, candidate["id"], owner_decision(second, candidate)
                    )
                )
            assert error.value.code == "memory_organization_conflict"
            assert (await db.read(lambda unit: unit.memory_organization_get(agent, second["id"]))) == second
            assert len(await db.read(lambda unit: organization_audits(unit, agent, "memory.organization.applied"))) == 1

    asyncio.run(scenario())


def test_same_keeper_can_suppress_multiple_duplicates_and_complete_undo_preserves_all_truth(tmp_path):
    async def scenario():
        async with opened(tmp_path, Clock()) as (_, db, _, agent):
            memories = await db.write(lambda unit: facts(unit, agent, count=3))
            original = await db.read(lambda unit: memory_truth(unit, agent))
            batches = []
            for index in (1, 2):
                batch = await proposal(db, agent, [memories[0], memories[index]], f"same-keeper-{index}")
                candidate = batch["candidates"][0]
                applied = await db.write(
                    lambda unit: unit.memory_organization_decide(
                        agent, candidate["id"], owner_decision(batch, candidate)
                    )
                )
                assert applied["state"] == "complete"
                batches.append(applied)
            assert [row["id"] for row in await db.read(lambda unit: unit.search_memory(agent, "organization"))] == [
                memories[0]["id"]
            ]
            batch = batches[0]
            candidate = batch["candidates"][0]
            revoked = await db.write(
                lambda unit: unit.memory_organization_decide(
                    agent, candidate["id"], owner_decision(batch, candidate, "undo")
                )
            )
            assert revoked["state"] == "complete" and revoked["candidates"][0]["state"] == "revoked"
            assert len(await db.read(lambda unit: unit.search_memory(agent, "organization"))) == 2
            assert await db.read(lambda unit: memory_truth(unit, agent)) == original
            assert len(await db.read(lambda unit: unit.query("memory", agent))) == 3

    asyncio.run(scenario())


def test_partial_cancel_keeps_applied_relationship_but_allows_owner_undo(tmp_path):
    async def scenario():
        async with opened(tmp_path, Clock()) as (_, db, _, agent):
            memories = await db.write(lambda unit: facts(unit, agent, count=3))
            batch = await proposal(db, agent, memories)
            candidate = batch["candidates"][0]
            partial = await db.write(
                lambda unit: unit.memory_organization_decide(agent, candidate["id"], owner_decision(batch, candidate))
            )
            assert partial["state"] == "ready"
            with pytest.raises(BusinessError) as error:
                await db.write(lambda unit: unit.memory_organization_cancel(agent, batch["id"], batch["revision"]))
            assert error.value.code == "revision_conflict"
            cancelled = await db.write(
                lambda unit: unit.memory_organization_cancel(agent, batch["id"], partial["revision"])
            )
            assert cancelled["state"] == "cancelled" and cancelled["valid"]
            assert sorted(row["state"] for row in cancelled["candidates"]) == ["applied", "cancelled"]
            assert len(await db.read(lambda unit: unit.search_memory(agent, "organization"))) == 2
            applied = next(row for row in cancelled["candidates"] if row["state"] == "applied")
            restored = await db.write(
                lambda unit: unit.memory_organization_decide(
                    agent, applied["id"], owner_decision(cancelled, applied, "undo")
                )
            )
            assert restored["state"] == "cancelled" and restored["revision"] == cancelled["revision"] + 1
            assert len(await db.read(lambda unit: unit.search_memory(agent, "organization"))) == 3
            assert await db.write(lambda unit: unit.memory_organization_cancel(agent, batch["id"], 1)) == restored

    asyncio.run(scenario())


def test_fts_and_chinese_fallback_share_suppression_and_new_context_only_rejects_duplicate(tmp_path):
    async def scenario():
        async with opened(tmp_path, Clock()) as (_, db, _, agent):
            memories = await db.write(lambda unit: facts(unit, agent, "organization 完全重复事实 !!!"))
            turn = await db.write(lambda unit: unit.accept_turn(agent, None, "context", "new request", {}))
            snapshot = await db.write(
                lambda unit: unit.snapshot(agent, turn["run_id"], [], [row["id"] for row in memories])
            )
            historical = await db.read(lambda unit: unit.context_memories(agent, snapshot))
            batch = await proposal(db, agent, memories)
            candidate = batch["candidates"][0]
            keep, duplicate = candidate_memories(batch, candidate)
            await db.write(
                lambda unit: unit.memory_organization_decide(agent, candidate["id"], owner_decision(batch, candidate))
            )
            fts_hits = await db.read(
                lambda unit: (
                    unit.connection.execute(
                        text("SELECT id FROM memory_fts WHERE memory_fts MATCH :query"), {"query": '"重复"'}
                    )
                    .scalars()
                    .all()
                )
            )
            assert fts_hits == [], "The Chinese substring must exercise fallback rather than full-token FTS"
            for query in ("organization", "重复", "!!!"):
                assert [
                    row["id"] for row in await db.read(lambda unit, query=query: unit.search_memory(agent, query))
                ] == [keep]
            await db.write(lambda unit: unit._capture_memories(agent, snapshot, [duplicate]))
            assert await db.read(lambda unit: unit.context_memories(agent, snapshot)) == historical
            with pytest.raises(BusinessError) as error:
                await db.write(lambda unit: unit.snapshot(agent, turn["run_id"], [], [duplicate]))
            assert error.value.code == "memory_organized"
            snapshots = await db.read(
                lambda unit: (
                    unit.connection.execute(
                        select(s.context_snapshots.c.id).where(s.context_snapshots.c.run_id == turn["run_id"])
                    )
                    .scalars()
                    .all()
                )
            )
            assert snapshots == [snapshot], "Rejected capture must roll back the new snapshot too"

    asyncio.run(scenario())


@pytest.mark.parametrize("change", ["revision", "anchor", "person"])
def test_source_changes_invalidate_review_relationship_and_remove_all_preview_content(tmp_path, change):
    async def scenario():
        async with opened(tmp_path, Clock()) as (_, db, _, agent):
            person = await db.write(lambda unit: unit.person_create(agent, "Source person"))
            memories = await db.write(
                lambda unit: facts(unit, agent, "private exact organization fact", person_id=person["id"])
            )
            batch = await proposal(db, agent, memories)
            candidate = batch["candidates"][0]
            await db.write(
                lambda unit: unit.memory_organization_decide(agent, candidate["id"], owner_decision(batch, candidate))
            )
            if change == "revision":
                await db.write(lambda unit: unit.revise_memory(agent, memories[0]["id"], "changed fact", 1))
            elif change == "anchor":
                revision = await db.read(
                    lambda unit: unit.connection.execute(
                        select(s.memory_revisions.c.id).where(s.memory_revisions.c.memory_id == memories[0]["id"])
                    ).scalar_one()
                )
                await db.write(lambda unit: unit._evidence(agent, memories[0]["id"], revision))
            else:
                current = await db.read(lambda unit: unit.person_get(agent, person["id"]))
                await db.write(lambda unit: unit.person_delete(agent, person["id"], current["revision"]))
            invalid = await db.read(lambda unit: unit.memory_organization_get(agent, batch["id"]))
            assert invalid["state"] == "invalidated" and not invalid["valid"]
            assert all(not row["valid"] and "content" not in row for row in invalid["sources"])
            assert invalid["candidates"][0]["state"] == "invalidated" and not invalid["candidates"][0]["valid"]
            stored = await db.read(
                lambda unit: [
                    dict(row)
                    for table in (
                        s.memory_organization_batches,
                        s.memory_organization_sources,
                        s.memory_organization_candidates,
                    )
                    for row in unit.connection.execute(select(table)).mappings()
                ]
            )
            assert "private exact organization fact" not in json.dumps(stored)
            with pytest.raises(BusinessError):
                await db.write(
                    lambda unit: unit.memory_organization_decide(
                        agent, candidate["id"], owner_decision(batch, candidate)
                    )
                )
            assert (await db.integrity())["foreign_keys"] == 0

    asyncio.run(scenario())


def test_cross_agent_identities_and_detail_paging_are_isolated(tmp_path):
    async def scenario():
        async with opened(tmp_path, Clock()) as (_, db, _, agent):
            memories = await db.write(lambda unit: facts(unit, agent))
            batch = await proposal(db, agent, memories)
            stranger = (await db.write(lambda unit: unit.create_agent("Other Agent")))["id"]
            for operation in (
                lambda unit: unit.memory_organization_get(stranger, batch["id"]),
                lambda unit: unit.memory_organization_enqueue(stranger, organization_request(memories)),
                lambda unit: unit.memory_organization_cancel(stranger, batch["id"], batch["revision"]),
                lambda unit: unit.memory_organization_decide(
                    stranger, batch["candidates"][0]["id"], owner_decision(batch, batch["candidates"][0])
                ),
            ):
                with pytest.raises(BusinessError) as error:
                    await db.write(operation)
                assert error.value.status == 404
            assert await db.read(lambda unit: unit.memory_organization_page(stranger)) == []
            rows = await db.read(lambda unit: unit.memory_organization_page(agent, limit=1))
            assert rows[0]["id"] == batch["id"] and "sources" not in rows[0] and "candidates" not in rows[0]
            assert "content" not in rows[0] and rows[0]["valid"]
            assert await db.read(lambda unit: unit.memory_organization_page(agent, after=rows[0]["sequence"])) == []

    asyncio.run(scenario())
