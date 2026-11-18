"""Committed maintenance attempts and atomic index settlement survive real process exits without stale owners."""

from __future__ import annotations

import asyncio
import subprocess
import sys

import pytest
from omni_test_support.v2.maintenance_ownership import PRIVATE, environment, identity, request
from omni_test_support.v2.objects import Clock
from sqlalchemy import select

from omni.adapters.persistence import schema as s
from omni.domain.contracts import BusinessError

CRASH_HELPER = 'import asyncio\nimport datetime as dt\nimport os\nimport sys\nimport time\nfrom pathlib import Path\nfrom omni_test_support.v2.maintenance_ownership import environment, identity\nfrom omni.domain.documents import DocumentText\nfrom omni.domain.maintenance import INDEX_KIND, INDEX_VERSION\n\nclass Clock:\n    def now(self):\n        return sys.argv[4]\n    def monotonic(self):\n        return time.monotonic()\n\nasync def execute():\n    async with environment(Path(sys.argv[1]), Clock()) as (_, database, documents, agent):\n        claims = await database.write(lambda unit: unit.maintenance_claim(1))\n        assert len(claims) == 1 and claims[0]["id"] == sys.argv[2]\n        claim = claims[0]\n        await database.write(lambda unit: unit.maintenance_start(claim))\n        def begin(unit):\n            unit.maintenance_guard(*identity(claim))\n            build = unit.document_build_begin(\n                agent, claim["document_id"], INDEX_KIND, INDEX_VERSION, None,\n                expected_revision_id=claim["revision_id"]\n            )\n            unit.maintenance_bind_build(claim, build["id"])\n            return build\n        build = await database.write(begin)\n        if sys.argv[3] == "bound":\n            os._exit(101)\n        assert sys.argv[3] == "published"\n        source = await documents.get(agent, claim["document_id"], claim["revision_id"])\n        content = DocumentText.from_text(source["content"])\n        def publish(unit):\n            unit.maintenance_guard(*identity(claim))\n            result = unit.document_build_finish(agent, build["id"], content)\n            assert result["state"] == "ready"\n            unit.maintenance_finish(claim, build["id"])\n        await database.write(publish)\n        os._exit(102)\n\nasyncio.run(execute())\nraise RuntimeError("The requested process-exit hook was not reached")\n'


def build_row(unit, build_id):
    return dict(
        unit.connection.execute(select(s.document_index_builds).where(s.document_index_builds.c.id == build_id))
        .mappings()
        .one()
    )


@pytest.mark.parametrize(("phase", "code"), [("bound", 101), ("published", 102)])
def test_real_exit_recovery_fences_unfinished_owner_and_preserves_committed_success(tmp_path, phase, code):
    home, clock = tmp_path / "home", Clock()

    async def prepare():
        async with environment(home, clock) as (_, db, documents, agent):
            source = await documents.save(agent, "Recoverable source", PRIVATE)
            job = await db.write(lambda unit: unit.maintenance_enqueue(agent, request(source)))
            return agent, source, job

    agent, source, job = asyncio.run(prepare())
    script = tmp_path / "exit-owned-maintenance.py"
    script.write_text(CRASH_HELPER, encoding="utf-8")
    child = subprocess.run(
        [sys.executable, str(script), str(home), job["id"], phase, clock.now()],
        capture_output=True,
        text=True,
        timeout=30,
        check=False,
    )
    assert child.returncode == code, child.stderr

    async def recover():
        async with environment(home, clock) as (_, db, documents, _):
            before = await db.read(lambda unit: unit.maintenance_get(agent, job["id"]))
            old_build_id = before["attempt"]["build_id"]
            old_build = await db.read(lambda unit: build_row(unit, old_build_id))
            assert before["attempt"]["attempt_no"] == before["attempt"]["fence"] == 1
            assert old_build["revision_id"] == source["revision_id"]
            assert before["state"] == ("running" if phase == "bound" else "succeeded")
            assert old_build["state"] == ("building" if phase == "bound" else "ready")
            audit_before = await db.read(
                lambda unit: [
                    dict(row)
                    for row in unit.connection.execute(
                        select(s.audit).where(s.audit.c.operation_id == job["id"]).order_by(s.audit.c.sequence)
                    ).mappings()
                ]
            )
            # Match Application.start's business recovery before starting the
            # maintenance dispatcher, so the old building record cannot revive.
            await db.write(lambda unit: unit.recover())
            recovery = await db.write(lambda unit: unit.maintenance_recover())
            current = await db.read(lambda unit: unit.maintenance_get(agent, job["id"]))
            if phase == "bound":
                assert recovery["interrupted"] == recovery["requeued"] == 1
                assert current["state"] == "queued" and current["attempt"]["state"] == "interrupted"
                failed_build = await db.read(lambda unit: build_row(unit, old_build_id))
                assert failed_build["state"] == "failed" and failed_build["error_code"] == "process_interrupted"
                claim = (await db.write(lambda unit: unit.maintenance_claim(1)))[0]
                assert claim["attempt"]["attempt_no"] == claim["attempt"]["fence"] == 2
                assert claim["attempt"]["token"] != before["attempt"]["token"]
                assert claim["attempt"]["boot_id"] != before["attempt"]["boot_id"]
                await db.write(lambda unit: unit.maintenance_start(claim))
                rebuilt = await documents.rebuild(
                    agent,
                    source["id"],
                    "full_text",
                    expected_revision_id=source["revision_id"],
                    guard=lambda unit: unit.maintenance_guard(*identity(claim)),
                    on_build=lambda unit, build_id: unit.maintenance_bind_build(claim, build_id),
                    on_finish=lambda unit, result: unit.maintenance_finish(claim, result["id"]),
                )
                assert rebuilt["id"] != old_build_id and rebuilt["state"] == "ready"
                assert await db.read(lambda unit: build_row(unit, old_build_id)) == failed_build
                settled = await db.read(lambda unit: unit.maintenance_get(agent, job["id"]))
                assert settled["state"] == "succeeded" and settled["result_build_id"] == rebuilt["id"]
            else:
                assert recovery["interrupted"] == recovery["requeued"] == 0
                assert current == before and current["result_build_id"] == old_build_id
                assert await db.read(lambda unit: build_row(unit, old_build_id)) == old_build
                assert not await db.write(lambda unit: unit.maintenance_claim(2))
                audit_after = await db.read(
                    lambda unit: [
                        dict(row)
                        for row in unit.connection.execute(
                            select(s.audit).where(s.audit.c.operation_id == job["id"]).order_by(s.audit.c.sequence)
                        ).mappings()
                    ]
                )
                assert audit_after == audit_before
            final = await db.read(lambda unit: unit.maintenance_get(agent, job["id"]))
            for operation in (
                lambda unit: unit.maintenance_guard(*identity(before)),
                lambda unit: unit.maintenance_heartbeat(before),
                lambda unit: unit.maintenance_fail(before, "late_old_owner"),
            ):
                with pytest.raises(BusinessError) as error:
                    await db.write(operation)
                assert error.value.code == "maintenance_claim_lost"
            assert await db.read(lambda unit: unit.maintenance_get(agent, job["id"])) == final
            attempts = await db.read(
                lambda unit: [
                    dict(row)
                    for row in unit.connection.execute(
                        select(s.maintenance_attempts)
                        .where(s.maintenance_attempts.c.job_id == job["id"])
                        .order_by(s.maintenance_attempts.c.attempt_no)
                    ).mappings()
                ]
            )
            assert [row["state"] for row in attempts] == (
                ["interrupted", "succeeded"] if phase == "bound" else ["succeeded"]
            )
            assert (await db.integrity())["foreign_keys"] == 0

    asyncio.run(recover())
