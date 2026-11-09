"""Maintenance admission, ownership and restart recovery use real isolated repositories and fake time."""

from __future__ import annotations

import asyncio
import copy
import datetime as dt
import json
from contextlib import asynccontextmanager

import pytest
from sqlalchemy import select
from test_v2_objects import Clock, opened

from omni.adapters.persistence import schema as s
from omni.adapters.primitives import UUIDs
from omni.application.documents import DocumentService
from omni.domain.contracts import BusinessError
from omni.domain.maintenance import INDEX_KIND, INDEX_VERSION, Claim

PRIVATE = "maintenance-owned-source-body-is-private"


@asynccontextmanager
async def environment(home, clock=None):
    clock = clock or Clock()
    async with opened(home, clock) as (_, db, objects, agent):
        documents = DocumentService(db, objects)
        try:
            yield clock, db, documents, agent
        finally:
            assert await documents.close()


def request(source, key="maintenance"):
    return {
        "kind": "document_index_rebuild",
        "key": key,
        "document_id": source["id"],
        "revision": source["head_revision"],
    }


def identity(row):
    attempt = row["attempt"]
    return row["agent_id"], row["id"], attempt["id"], attempt["token"], attempt["fence"]


def captured(row):
    attempt = row["attempt"]
    return Claim(
        agent_id=row["agent_id"],
        job_id=row["id"],
        attempt_id=attempt["id"],
        token=attempt["token"],
        boot_id=attempt["boot_id"],
        fence=attempt["fence"],
        kind=row["kind"],
        document_id=row["document_id"],
        revision_id=row["revision_id"],
        source_revision=row["source_revision"],
        erasure_job_id=row["erasure_job_id"],
        index_version=row["index_version"],
    )


def test_idempotency_precedes_full_capacity_and_changed_source_validation(tmp_path):
    async def scenario():
        async with environment(tmp_path) as (_, db, documents, agent):
            source = await documents.save(agent, "Source", PRIVATE)
            original = request(source, "first")
            untouched = copy.deepcopy(original)
            first = await db.write(lambda u: u.maintenance_enqueue(agent, original))

            def fill(u):
                for index in range(63):
                    u.maintenance_enqueue(agent, request(source, f"queued-{index}"))

            await db.write(fill)
            assert original == untouched
            assert await db.write(lambda u: u.maintenance_enqueue(agent, original)) == first
            with pytest.raises(BusinessError) as error:
                await db.write(lambda u: u.maintenance_enqueue(agent, {**original, "revision": 2}))
            assert error.value.code == "idempotency_conflict"
            with pytest.raises(BusinessError) as error:
                await db.write(lambda u: u.maintenance_enqueue(agent, request(source, "overflow")))
            assert error.value.code == "maintenance_busy"
            rows = await db.read(lambda u: u.maintenance_page(agent, limit=100))
            assert len(rows) == 64 and all(row["state"] == "queued" and row["attempt"] is None for row in rows)
            assert PRIVATE not in json.dumps(rows)
            following = await db.read(lambda u: u.maintenance_page(agent, after=rows[0]["sequence"], limit=100))
            assert following == rows[1:]
            stranger = (await db.write(lambda u: u.create_agent("Other")))["id"]
            assert await db.read(lambda u: u.maintenance_page(stranger)) == []
            with pytest.raises(BusinessError) as error:
                await db.read(lambda u: u.maintenance_get(stranger, first["id"]))
            assert error.value.status == 404

    asyncio.run(scenario())


def test_global_two_attempts_and_same_document_fifo_survive_expired_live_lease(tmp_path):
    async def scenario():
        async with environment(tmp_path) as (clock, db, documents, agent):
            first = await documents.save(agent, "First", PRIVATE)
            second = await documents.save(agent, "Second", "second source")
            third = await documents.save(agent, "Third", "third source")
            queued = []
            for source, key in ((first, "first-a"), (first, "first-b"), (second, "second"), (third, "third")):
                queued.append(
                    await db.write(lambda u, source=source, key=key: u.maintenance_enqueue(agent, request(source, key)))
                )
            claims = await db.write(lambda u: u.maintenance_claim(2))
            assert [row["id"] for row in claims] == [queued[0]["id"], queued[2]["id"]]
            assert all(row["attempt"]["attempt_no"] == row["attempt"]["fence"] == 1 for row in claims)
            assert not await db.write(lambda u: u.maintenance_claim(2))
            clock.current += dt.timedelta(seconds=61)
            assert not await db.write(lambda u: u.maintenance_claim(2)), "A lease timeout cannot steal live-boot work"
            await db.write(lambda u: u.maintenance_fail(claims[0], "fixture_failure"))
            resumed = await db.write(lambda u: u.maintenance_claim(2))
            assert len(resumed) == 1 and resumed[0]["id"] == queued[1]["id"]
            assert not await db.write(lambda u: u.maintenance_claim(1))

    asyncio.run(scenario())


def test_cancellation_is_compare_and_swap_and_retains_attempt_until_real_settlement(tmp_path):
    async def scenario():
        async with environment(tmp_path) as (clock, db, documents, agent):
            source = await documents.save(agent, "Source", PRIVATE)
            job = await db.write(lambda u: u.maintenance_enqueue(agent, request(source)))
            with pytest.raises(BusinessError) as error:
                await db.write(lambda u: u.maintenance_cancel(agent, job["id"], job["revision"] + 1))
            assert error.value.code == "revision_conflict"
            claim = (await db.write(lambda u: u.maintenance_claim(1)))[0]
            current = await db.write(lambda u: u.maintenance_start(captured(claim)))
            cancelled = await db.write(lambda u: u.maintenance_cancel(agent, job["id"], current["revision"]))
            assert cancelled["state"] == "cancelling" and cancelled["attempt"]["state"] == "running"
            assert cancelled["finished_at"] is None and cancelled["cancel_requested_at"]
            assert await db.write(lambda u: u.maintenance_cancel(agent, job["id"], 1)) == cancelled
            with pytest.raises(BusinessError) as error:
                await db.read(lambda u: u.maintenance_guard(*identity(claim)))
            assert error.value.code == "maintenance_cancelled"
            assert (await db.write(lambda u: u.maintenance_heartbeat(claim)))["state"] == "cancelling"
            final = await db.write(lambda u: u.maintenance_fail(claim, "maintenance_cancelled"))
            assert final["state"] == final["attempt"]["state"] == "cancelled"
            assert final["finished_at"] and final["attempt"]["ended_at"]
            clock.current += dt.timedelta(minutes=10)
            assert await db.write(lambda u: u.maintenance_heartbeat(claim)) == final
            assert await db.write(lambda u: u.maintenance_cancel(agent, job["id"], 1)) == final
            queued = await db.write(lambda u: u.maintenance_enqueue(agent, request(source, "queued-cancel")))
            final_queued = await db.write(lambda u: u.maintenance_cancel(agent, queued["id"], queued["revision"]))
            assert final_queued["state"] == "cancelled" and final_queued["attempt"] is None

    asyncio.run(scenario())


def test_wrong_owner_fields_and_expired_active_lease_cannot_renew_or_start(tmp_path):
    async def scenario():
        async with environment(tmp_path) as (clock, db, documents, agent):
            source = await documents.save(agent, "Source", PRIVATE)
            await db.write(lambda u: u.maintenance_enqueue(agent, request(source)))
            claim = (await db.write(lambda u: u.maintenance_claim(1)))[0]
            args = identity(claim)
            for position, replacement in ((0, UUIDs().new()), (2, UUIDs().new()), (3, UUIDs().new()), (4, 2)):
                wrong = tuple(replacement if index == position else value for index, value in enumerate(args))
                with pytest.raises(BusinessError) as error:
                    await db.read(lambda u, wrong=wrong: u.maintenance_guard(*wrong))
                assert error.value.code == "maintenance_claim_lost"
            foreign_boot = copy.deepcopy(claim)
            foreign_boot["attempt"]["boot_id"] = UUIDs().new()
            with pytest.raises(BusinessError) as error:
                await db.write(lambda u: u.maintenance_heartbeat(foreign_boot))
            assert error.value.code == "maintenance_claim_lost"
            clock.current += dt.timedelta(seconds=61)
            for operation in (
                lambda u: u.maintenance_guard(*args),
                lambda u: u.maintenance_start(claim),
                lambda u: u.maintenance_heartbeat(claim),
            ):
                with pytest.raises(BusinessError) as error:
                    await db.write(operation)
                assert error.value.code == "maintenance_claim_lost"
            failed = await db.write(lambda u: u.maintenance_fail(claim, "fixture_timeout"))
            assert failed["state"] == "failed" and failed["attempt"]["error_code"] == "fixture_timeout"
            assert await db.write(lambda u: u.maintenance_fail(claim, "different_late_error")) == failed
            assert await db.write(lambda u: u.maintenance_heartbeat(claim)) == failed

    asyncio.run(scenario())


def test_committed_success_heartbeat_and_late_cancel_do_not_change_receipt(tmp_path):
    async def scenario():
        async with environment(tmp_path) as (clock, db, documents, agent):
            source = await documents.save(agent, "Source", PRIVATE)
            await db.write(lambda u: u.maintenance_enqueue(agent, request(source)))
            claim = (await db.write(lambda u: u.maintenance_claim(1)))[0]
            await db.write(lambda u: u.maintenance_start(claim))
            build = await documents.rebuild(
                agent,
                source["id"],
                "full_text",
                expected_revision_id=source["revision_id"],
                guard=lambda u: u.maintenance_guard(*identity(claim)),
                on_build=lambda u, build_id: u.maintenance_bind_build(claim, build_id),
                on_finish=lambda u, result: u.maintenance_finish(claim, result["id"]),
            )
            final = await db.read(lambda u: u.maintenance_get(agent, claim["id"]))
            assert final["state"] == final["attempt"]["state"] == "succeeded"
            assert final["result_build_id"] == final["attempt"]["build_id"] == build["id"]
            audits = await db.read(lambda u: u.query("audit", agent))
            clock.current += dt.timedelta(minutes=10)
            assert await db.write(lambda u: u.maintenance_heartbeat(claim)) == final
            assert await db.write(lambda u: u.maintenance_finish(claim, build["id"])) == final
            assert await db.write(lambda u: u.maintenance_fail(claim, "late_error")) == final
            assert await db.write(lambda u: u.maintenance_cancel(agent, claim["id"], 1)) == final
            assert await db.read(lambda u: u.query("audit", agent)) == audits

    asyncio.run(scenario())


def test_restart_interrupts_old_attempt_and_fences_its_late_callbacks(tmp_path):
    async def scenario():
        clock = Clock()
        async with environment(tmp_path, clock) as (_, db, documents, agent):
            source = await documents.save(agent, "Source", PRIVATE)
            await db.write(lambda u: u.maintenance_enqueue(agent, request(source)))
            original = (await db.write(lambda u: u.maintenance_claim(1)))[0]
            await db.write(lambda u: u.maintenance_start(original))

            def begin(u):
                build = u.document_build_begin(agent, source["id"], INDEX_KIND, INDEX_VERSION, None)
                u.maintenance_bind_build(original, build["id"])
                return build

            build = await db.write(begin)
        async with environment(tmp_path, clock) as (_, db, _, _):
            await db.write(lambda u: u.recover())
            recovered = await db.write(lambda u: u.maintenance_recover())
            assert recovered["interrupted"] == recovered["requeued"] == 1
            row = await db.read(lambda u: u.maintenance_get(agent, original["id"]))
            assert row["state"] == "queued" and row["attempt"]["state"] == "interrupted"
            stored_build = await db.read(
                lambda u: dict(
                    u.connection.execute(
                        select(s.document_index_builds).where(s.document_index_builds.c.id == build["id"])
                    )
                    .mappings()
                    .one()
                )
            )
            assert stored_build["state"] == "failed" and stored_build["error_code"] == "process_interrupted"
            current = (await db.write(lambda u: u.maintenance_claim(1)))[0]
            assert current["attempt"]["attempt_no"] == current["attempt"]["fence"] == 2
            assert current["attempt"]["token"] != original["attempt"]["token"]
            for operation in (
                lambda u: u.maintenance_heartbeat(original),
                lambda u: u.maintenance_fail(original, "late_old_owner"),
                lambda u: u.maintenance_guard(*identity(original)),
            ):
                with pytest.raises(BusinessError) as error:
                    await db.write(operation)
                assert error.value.code == "maintenance_claim_lost"
            clock.current += dt.timedelta(minutes=10)
            assert (await db.write(lambda u: u.maintenance_recover()))["interrupted"] == 0
            assert (await db.read(lambda u: u.maintenance_get(agent, current["id"])))["attempt"] == current["attempt"]

    asyncio.run(scenario())


def test_recovery_stops_after_three_attempts_and_never_retargets_new_source(tmp_path):
    async def scenario():
        clock = Clock()
        for index in range(3):
            async with environment(tmp_path / "bounded", clock) as (_, db, documents, agent):
                if index == 0:
                    source = await documents.save(agent, "Source", PRIVATE)
                    job = await db.write(lambda u: u.maintenance_enqueue(agent, request(source)))
                else:
                    await db.write(lambda u: u.maintenance_recover())
                claim = (await db.write(lambda u: u.maintenance_claim(1)))[0]
                assert claim["attempt"]["fence"] == index + 1
                await db.write(lambda u: u.maintenance_start(claim))
        async with environment(tmp_path / "bounded", clock) as (_, db, _, _):
            assert (await db.write(lambda u: u.maintenance_recover()))["failed"] == 1
            final = await db.read(lambda u: u.maintenance_get(agent, job["id"]))
            assert final["state"] == "failed" and final["error_code"] == "maintenance_attempt_limit"
            assert final["attempt_count"] == 3 and final["attempt"]["state"] == "interrupted"
            assert not await db.write(lambda u: u.maintenance_claim(1))
        async with environment(tmp_path / "changed", clock) as (_, db, documents, agent):
            source = await documents.save(agent, "Source", PRIVATE)
            job = await db.write(lambda u: u.maintenance_enqueue(agent, request(source)))
            claim = (await db.write(lambda u: u.maintenance_claim(1)))[0]
            await db.write(lambda u: u.maintenance_start(claim))
            newer = await documents.save(agent, "New source", "new content", source["id"], 1)
        async with environment(tmp_path / "changed", clock) as (_, db, _, _):
            assert (await db.write(lambda u: u.maintenance_recover()))["failed"] == 1
            final = await db.read(lambda u: u.maintenance_get(agent, job["id"]))
            assert final["state"] == "failed" and final["error_code"] == "maintenance_source_changed"
            assert final["revision_id"] == source["revision_id"] != newer["revision_id"]
            assert await db.write(lambda u: u.maintenance_enqueue(agent, request(source))) == final

    asyncio.run(scenario())


def test_erasure_requires_existing_confirmation_and_done_request_can_reconcile(tmp_path):
    async def scenario():
        async with environment(tmp_path) as (_, db, documents, agent):
            source = await documents.save(agent, "Source", PRIVATE)
            with pytest.raises(BusinessError) as error:
                await db.write(
                    lambda u: u.maintenance_enqueue(
                        agent, {"kind": "document_erasure_resume", "key": "missing", "erasure_job_id": UUIDs().new()}
                    )
                )
            assert error.value.status == 404
            erasure = await db.write(lambda u: u.document_erasure_begin(agent, source["id"], 1))
            await documents.erase(agent, source["id"], 1)
            job = await db.write(
                lambda u: u.maintenance_enqueue(
                    agent, {"kind": "document_erasure_resume", "key": "confirmed", "erasure_job_id": erasure["id"]}
                )
            )
            assert job["source_revision"] == 1 and job["revision_id"] == source["revision_id"]
            claim = (await db.write(lambda u: u.maintenance_claim(1)))[0]
            await db.write(lambda u: u.maintenance_start(claim))
            final = await db.write(lambda u: u.maintenance_finish(claim))
            assert final["state"] == "succeeded" and final["result_build_id"] is None

    asyncio.run(scenario())
