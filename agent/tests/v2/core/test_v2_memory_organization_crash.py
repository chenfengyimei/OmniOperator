"""Real process termination proves proposal publication and owner review have one SQLite transaction."""

from __future__ import annotations

import asyncio
import json
import subprocess
import sys

import pytest
from omni_test_support.v2.memory_organization_repository import (
    candidate_memories,
    facts,
    memory_truth,
    organization_audits,
    organization_request,
    owner_decision,
)
from omni_test_support.v2.objects import Clock, opened

from omni.domain.contracts import BusinessError

CRASH_HELPER = 'import asyncio\nimport os\nimport sys\nimport time\nfrom pathlib import Path\nfrom omni_test_support.v2.objects import opened\nfrom omni_test_support.v2.memory_organization_repository import owner_decision\nfrom omni.adapters.persistence import schema as s\nfrom omni.adapters.persistence.memory_organization_repository import MemoryOrganizationRepositories\n\nclass Clock:\n    def now(self):\n        return sys.argv[4]\n    def monotonic(self):\n        return time.monotonic()\n\nasync def execute():\n    phase, batch_id = sys.argv[3], sys.argv[2]\n    async with opened(Path(sys.argv[1]), Clock()) as (_, database, _, agent):\n        if phase == "proposal":\n            original = MemoryOrganizationRepositories._record\n            def interrupted(unit, table, *args, **kwargs):\n                result = original(unit, table, *args, **kwargs)\n                if table is s.memory_organization_candidates:\n                    os._exit(141)\n                return result\n            MemoryOrganizationRepositories._record = interrupted\n            await database.write(lambda unit: unit.memory_organization_process(agent, batch_id))\n        else:\n            batch = await database.read(lambda unit: unit.memory_organization_get(agent, batch_id))\n            candidate = batch["candidates"][0]\n            if phase == "review":\n                original = MemoryOrganizationRepositories._organization_change\n                def interrupted(unit, *args, **kwargs):\n                    result = original(unit, *args, **kwargs)\n                    # The candidate UPDATE and review audit already executed;\n                    # neither this batch UPDATE nor any preceding write has committed.\n                    os._exit(142)\n                MemoryOrganizationRepositories._organization_change = interrupted\n            await database.write(lambda unit: unit.memory_organization_decide(\n                agent, candidate["id"], owner_decision(batch, candidate)))\n            if phase == "committed":\n                os._exit(143)\n\nasyncio.run(execute())\nraise RuntimeError("Requested real process-exit hook was not reached")\n'


@pytest.mark.parametrize(("phase", "exit_code"), [("proposal", 141), ("review", 142), ("committed", 143)])
def test_real_exit_rolls_back_partial_proposals_and_reviews_but_preserves_committed_relationship(
    tmp_path, phase, exit_code
):
    home, clock = tmp_path / "home", Clock()

    async def prepare():
        async with opened(home, clock) as (_, db, _, agent):
            memories = await db.write(lambda unit: facts(unit, agent, count=3 if phase == "proposal" else 2))
            batch = await db.write(lambda unit: unit.memory_organization_enqueue(agent, organization_request(memories)))
            if phase != "proposal":
                batch = await db.write(lambda unit: unit.memory_organization_process(agent, batch["id"]))
            truth = await db.read(lambda unit: memory_truth(unit, agent))
            return agent, memories, batch, truth

    agent, memories, before, original_truth = asyncio.run(prepare())
    script = tmp_path / "exit-memory-organization.py"
    script.write_text(CRASH_HELPER, encoding="utf-8")
    process = subprocess.run(
        [sys.executable, str(script), str(home), before["id"], phase, clock.now()],
        capture_output=True,
        text=True,
        timeout=30,
        check=False,
    )
    assert process.returncode == exit_code, process.stderr

    async def inspect():
        async with opened(home, clock) as (_, db, _, current_agent):
            assert current_agent == agent
            batch = await db.read(lambda unit: unit.memory_organization_get(agent, before["id"]))
            assert await db.read(lambda unit: memory_truth(unit, agent)) == original_truth
            applied = await db.read(lambda unit: organization_audits(unit, agent, "memory.organization.applied"))
            if phase == "proposal":
                assert batch == before and batch["state"] == "queued"
                assert not batch["candidates"] and batch["candidate_count"] == 0
                assert not await db.read(lambda unit: organization_audits(unit, agent, "memory.organization.prepared"))
                assert not applied
                ready = await db.write(lambda unit: unit.memory_organization_process(agent, batch["id"]))
                assert ready["state"] == "ready" and ready["candidate_count"] == len(ready["candidates"]) == 2
                assert len({row["duplicate_source_id"] for row in ready["candidates"]}) == 2
                assert (
                    len(await db.read(lambda unit: organization_audits(unit, agent, "memory.organization.prepared")))
                    == 1
                )
                assert await db.write(lambda unit: unit.memory_organization_process(agent, batch["id"])) == ready
            elif phase == "review":
                assert batch == before and batch["state"] == "ready" and not applied
                assert batch["candidates"][0]["state"] == "pending"
                assert len(await db.read(lambda unit: unit.search_memory(agent, "organization"))) == 2
                candidate = batch["candidates"][0]
                committed = await db.write(
                    lambda unit: unit.memory_organization_decide(
                        agent, candidate["id"], owner_decision(batch, candidate)
                    )
                )
                assert committed["state"] == "complete" and committed["candidates"][0]["state"] == "applied"
                assert (
                    len(await db.read(lambda unit: organization_audits(unit, agent, "memory.organization.applied")))
                    == 1
                )
                keep, _ = candidate_memories(batch, candidate)
                assert [row["id"] for row in await db.read(lambda unit: unit.search_memory(agent, "organization"))] == [
                    keep
                ]
            else:
                assert batch["state"] == "complete" and batch["candidates"][0]["state"] == "applied"
                assert len(applied) == 1 and applied[0]["id"] == batch["candidates"][0]["review_audit_id"]
                assert batch["revision"] == before["revision"] + 1
                assert batch["candidates"][0]["revision"] == before["candidates"][0]["revision"] + 1
                keep, _ = candidate_memories(batch, batch["candidates"][0])
                assert [row["id"] for row in await db.read(lambda unit: unit.search_memory(agent, "organization"))] == [
                    keep
                ]
                with pytest.raises(BusinessError) as error:
                    await db.write(
                        lambda unit: unit.memory_organization_decide(
                            agent, before["candidates"][0]["id"], owner_decision(before, before["candidates"][0])
                        )
                    )
                assert error.value.code == "revision_conflict"
                assert await db.write(lambda unit: unit.memory_organization_process(agent, batch["id"])) == batch
                assert (
                    await db.read(lambda unit: organization_audits(unit, agent, "memory.organization.applied"))
                    == applied
                )
            replay = await db.write(
                lambda unit: unit.memory_organization_enqueue(agent, organization_request(memories))
            )
            assert replay["id"] == batch["id"]
            assert await db.read(lambda unit: memory_truth(unit, agent)) == original_truth
            assert "organization exact fact" not in json.dumps(
                await db.read(lambda unit: organization_audits(unit, agent, "memory.organization.applied"))
            )
            assert (await db.integrity())["foreign_keys"] == 0

    asyncio.run(inspect())
