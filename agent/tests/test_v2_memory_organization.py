"""Real proposals, reversible recall organization and owned shutdown; never contacts a model or account."""

import asyncio
import threading

import pytest
from sqlalchemy import select

from omni.adapters.persistence import schema as s
from omni.adapters.platform.paths import InstanceLock
from omni.composition.instance import open_instance
from omni.domain.contracts import BusinessError


class NoModel:
    async def complete(self, *args):
        pytest.fail("Deterministic fact organization must not call a model")


async def ready(service, agent, batch_id):
    async with asyncio.timeout(10):
        while True:
            result = await service.get(agent, batch_id)
            if result["state"] != "queued":
                return result
            await asyncio.sleep(0.01)


def request(memories, key="organization"):
    return {"key": key, "sources": [{"memory_id": memory["id"], "revision": memory["revision"]} for memory in memories]}


def decision(batch, candidate, action="apply"):
    return {
        "revision": candidate["revision"],
        "fingerprint": candidate["fingerprint"],
        "epoch": batch["history_epoch"],
        "decision": action,
    }


async def seed(instance, content="exact duplicate canary"):
    agent = (await instance.application.query("agents", ""))[0]["id"]
    memories = [await instance.application.remember(agent, content) for _ in range(2)]
    return agent, memories


def test_proposals_preserve_fact_truth_and_owner_apply_undo_only_changes_recall(tmp_path):
    async def scenario():
        instance = await open_instance(tmp_path, capabilities=(), model=NoModel())
        try:
            agent, memories = await seed(instance)

            def original_truth(unit):
                return {
                    table.name: [dict(row) for row in unit.connection.execute(select(table)).mappings()]
                    for table in (s.memory_entries, s.memory_revisions, s.source_anchors)
                }

            original = await instance._database.read(original_truth)
            queued = await instance.memory_organization.submit(agent, request(memories))
            assert queued["state"] == "queued" and queued["budget_micro"] == 0
            assert not {"request_key", "request_sha256"}.intersection(queued)
            batch = await ready(instance.memory_organization, agent, queued["id"])
            assert batch["state"] == "ready" and batch["valid"] and len(batch["candidates"]) == 1
            candidate = batch["candidates"][0]
            assert len(await instance.application.search_memory(agent, "canary")) == 2
            assert await instance._database.read(original_truth) == original
            applied = await instance.memory_organization.decide(agent, candidate["id"], decision(batch, candidate))
            assert applied["state"] == "complete" and applied["candidates"][0]["state"] == "applied"
            assert len(await instance.application.search_memory(agent, "canary")) == 1
            assert len(await instance.application.query("memory", agent)) == 2
            assert await instance._database.read(original_truth) == original
            with pytest.raises(BusinessError) as repeated:
                await instance.memory_organization.decide(agent, candidate["id"], decision(batch, candidate))
            assert repeated.value.code == "revision_conflict"
            assert (await instance.memory_organization.get(agent, batch["id"]))["revision"] == applied["revision"]
            restored = await instance.memory_organization.decide(
                agent, candidate["id"], decision(applied, applied["candidates"][0], "undo")
            )
            assert restored["candidates"][0]["state"] == "revoked"
            assert len(await instance.application.search_memory(agent, "canary")) == 2
            assert await instance._database.read(original_truth) == original
            assert not await instance.application.query("calls", agent)
            assert not await instance._database.read(lambda u: u.connection.execute(select(s.tombstones)).all())
        finally:
            assert (await instance.close())["complete"]

    asyncio.run(scenario())


def test_source_forgetting_revokes_organization_without_retaining_or_resurrecting_erased_content(tmp_path):
    async def scenario():
        instance = await open_instance(tmp_path, capabilities=(), model=NoModel())
        try:
            agent, memories = await seed(instance, "private-organization-erasure-canary")
            queued = await instance.memory_organization.submit(agent, request(memories))
            batch = await ready(instance.memory_organization, agent, queued["id"])
            candidate = batch["candidates"][0]
            applied = await instance.memory_organization.decide(agent, candidate["id"], decision(batch, candidate))
            kept = next(row for row in applied["sources"] if row["id"] == candidate["keep_source_id"])
            await instance.application.forget(agent, kept["memory_id"], kept["revision"])
            invalid = await instance.memory_organization.get(agent, batch["id"])
            assert invalid["state"] == "invalidated" and not invalid["valid"]
            assert all("content" not in row for row in invalid["sources"])
            assert invalid["candidates"][0]["state"] == "invalidated"
            remaining = await instance.application.search_memory(agent, "canary")
            assert len(remaining) == 1 and remaining[0]["id"] != kept["memory_id"]
            with pytest.raises(BusinessError):
                await instance.memory_organization.decide(agent, candidate["id"], decision(batch, candidate))
            persisted = await instance._database.read(
                lambda u: [
                    str(list(u.connection.execute(select(table)).mappings()))
                    for table in (
                        s.memory_organization_batches,
                        s.memory_organization_sources,
                        s.memory_organization_candidates,
                    )
                ]
            )
            assert "private-organization-erasure-canary" not in "".join(persisted)
        finally:
            assert (await instance.close())["complete"]

    asyncio.run(scenario())


def test_source_drift_before_processing_and_real_local_restart_never_expands_selected_revision(tmp_path):
    async def scenario():
        first = await open_instance(tmp_path, capabilities=(), model=NoModel())
        agent, memories = await seed(first)
        # Pause only the shared worker admission, leaving the real durable command path intact.
        first.maintenance.stop_accepting()
        queued = await first.memory_organization.submit(agent, request(memories))
        assert queued["state"] == "queued"
        await first._database.write(lambda u: u.revise_memory(agent, memories[0]["id"], "revised fact", 1))
        assert (await first.memory_organization.get(agent, queued["id"]))["state"] == "invalidated"
        assert (await first.close())["complete"]
        second = await open_instance(tmp_path, capabilities=(), model=NoModel())
        try:
            current = await second.memory_organization.get(agent, queued["id"])
            assert current["state"] == "invalidated" and not current["candidates"]
            assert current["sources"][0]["revision"] == 1
            replay = await second.memory_organization.submit(agent, request(memories))
            assert replay["id"] == queued["id"] and replay["state"] == "invalidated"
        finally:
            assert (await second.close())["complete"]

    asyncio.run(scenario())


def test_cancelled_waiter_does_not_release_running_transaction_or_instance_ownership(tmp_path, monkeypatch):
    from omni.adapters.persistence.memory_organization_repository import MemoryOrganizationRepositories

    entered, release = threading.Event(), threading.Event()
    original = MemoryOrganizationRepositories.memory_organization_process

    def held(self, *args):
        entered.set()
        if not release.wait(10):
            raise AssertionError("Isolated transaction barrier was not released")
        return original(self, *args)

    monkeypatch.setattr(MemoryOrganizationRepositories, "memory_organization_process", held)

    async def scenario():
        instance = await open_instance(tmp_path, capabilities=(), model=NoModel())
        try:
            agent, memories = await seed(instance)
            instance.maintenance.stop_accepting()
            queued = await instance.memory_organization.submit(agent, request(memories))
            caller = asyncio.create_task(instance.memory_organization.process_one())
            async with asyncio.timeout(10):
                while not entered.is_set():
                    await asyncio.sleep(0.01)
            caller.cancel()
            with pytest.raises(asyncio.CancelledError):
                await caller
            assert instance.memory_organization.health()["pending"] == 1
            result = await instance.close(0.02)
            assert not result["complete"] and "memory_organization" in result["residual"]
            assert not instance._database.metrics["closed"]
            with pytest.raises(BusinessError, match="already running"):
                InstanceLock(instance.paths).acquire()
        finally:
            release.set()
            assert (await instance.close(10))["complete"]
        reopened = await open_instance(tmp_path, capabilities=(), model=NoModel())
        try:
            final = await reopened.memory_organization.get(agent, queued["id"])
            assert final["state"] == "ready" and len(final["candidates"]) == 1
        finally:
            assert (await reopened.close())["complete"]

    asyncio.run(scenario())
