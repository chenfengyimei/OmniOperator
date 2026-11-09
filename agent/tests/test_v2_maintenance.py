"""Real document work, atomic publication, cancellation and retained settlement ownership."""

import asyncio

import pytest
from sqlalchemy import select
from test_v2_objects import Clock, opened
from v2_delivery_helpers import Observation

from omni.adapters.persistence import schema as s
from omni.adapters.persistence.maintenance_repository import MaintenanceRepositories
from omni.application.documents import DocumentService
from omni.application.maintenance import MaintenanceService
from omni.domain.contracts import BusinessError


async def settled(service, agent, job_id):
    async with asyncio.timeout(10):
        while True:
            row = await service.get(agent, job_id)
            if row["state"] in {"succeeded", "failed", "cancelled"}:
                return row
            await asyncio.sleep(0.01)


def rebuild(source, key="rebuild"):
    return {"kind": "document_index_rebuild", "key": key, "document_id": source["id"], "revision": source["revision"]}


def test_real_full_text_result_and_job_commit_together_and_replay_is_metadata_only(tmp_path):
    async def scenario():
        clock = Clock()
        async with opened(tmp_path, clock) as (_, db, objects, agent):
            documents = DocumentService(db, objects)
            source = await documents.save(agent, "Evidence", "persisted maintenance evidence")
            service = MaintenanceService(db, documents, clock, Observation())
            job = await service.submit(agent, rebuild(source))
            assert job["state"] == "queued" and job["attempt"] is None
            assert await service.tick() == 1
            final = await settled(service, agent, job["id"])
            assert final["state"] == "succeeded" and final["attempt_count"] == 1
            assert final["attempt"]["build_id"] == final["result_build_id"]
            assert not {"request_key", "request_sha256"}.intersection(final)
            assert not {"token", "boot_id", "fence", "lease_until"}.intersection(final["attempt"])

            def consistent(unit):
                persisted = unit.maintenance_get(agent, job["id"])
                build = (
                    unit.connection.execute(
                        select(s.document_index_builds).where(
                            s.document_index_builds.c.id == persisted["result_build_id"]
                        )
                    )
                    .mappings()
                    .one()
                )
                return persisted["state"], build["state"], build["revision_id"]

            assert await db.read(consistent) == ("succeeded", "ready", source["revision_id"])
            assert (await documents.search(agent, "evidence"))["items"]
            await documents.save(agent, "Changed", "new content", source["id"], 1)
            assert (await service.submit(agent, rebuild(source)))["id"] == job["id"]
            assert (await service.cancel(agent, job["id"], final["revision"]))["state"] == "succeeded"
            assert await service.tick() == 0
            assert await service.close() and await documents.close()

    asyncio.run(scenario())


def test_cancel_waits_for_actual_source_work_and_prevents_publication(tmp_path, monkeypatch):
    async def scenario():
        clock = Clock()
        async with opened(tmp_path, clock) as (_, db, objects, agent):
            documents = DocumentService(db, objects)
            source = await documents.save(agent, "Evidence", "cancelled rebuild source")
            entered, release = asyncio.Event(), asyncio.Event()
            original = documents._source

            async def paused(*args):
                entered.set()
                await release.wait()
                return await original(*args)

            monkeypatch.setattr(documents, "_source", paused)
            service = MaintenanceService(db, documents, clock, Observation())
            job = await service.submit(agent, rebuild(source))
            await service.tick()
            try:
                async with asyncio.timeout(10):
                    await entered.wait()
                running = await service.get(agent, job["id"])
                assert running["state"] == "running"
                cancelled = await service.cancel(agent, job["id"], running["revision"])
                assert cancelled["state"] == "cancelling" and service.health()["active"] == 1
                assert not await service.close(0)
                assert documents.health()["pending"] == 1
            finally:
                release.set()
                assert await service.close()
                assert await documents.close()
            final = await service.get(agent, job["id"])
            assert final["state"] == "cancelled" and final["result_build_id"] is None
            build_id = final["attempt"]["build_id"]
            rows = await db.read(lambda u: u.document_builds(agent, source["id"]))
            assert next(row for row in rows if row["id"] == build_id)["state"] == "failed"

    asyncio.run(scenario())


def test_queued_job_never_rebuilds_a_newer_unconfirmed_revision(tmp_path):
    async def scenario():
        clock = Clock()
        async with opened(tmp_path, clock) as (_, db, objects, agent):
            documents = DocumentService(db, objects)
            source = await documents.save(agent, "Evidence", "first source")
            service = MaintenanceService(db, documents, clock, Observation())
            job = await service.submit(agent, rebuild(source))
            changed = await documents.save(agent, "Changed", "second source", source["id"], 1)
            await service.tick()
            final = await settled(service, agent, job["id"])
            assert final["state"] == "failed" and final["error_code"] == "maintenance_source_changed"
            assert final["revision_id"] == source["revision_id"]
            assert final["attempt"]["build_id"] is None
            assert (await documents.get(agent, source["id"]))["revision_id"] == changed["revision_id"]
            assert await service.close() and await documents.close()

    asyncio.run(scenario())


def test_failed_job_settlement_rolls_back_ready_index_publication(tmp_path, monkeypatch):
    def failed_receipt(*args, **kwargs):
        raise BusinessError("injected_settlement_failure", "Injected receipt failure")

    monkeypatch.setattr(MaintenanceRepositories, "maintenance_finish", failed_receipt)

    async def scenario():
        clock = Clock()
        async with opened(tmp_path, clock) as (_, db, objects, agent):
            documents = DocumentService(db, objects)
            source = await documents.save(agent, "Evidence", "atomic ready receipt")
            service = MaintenanceService(db, documents, clock, Observation())
            job = await service.submit(agent, rebuild(source))
            await service.tick()
            final = await settled(service, agent, job["id"])
            assert final["state"] == "failed" and final["result_build_id"] is None
            assert final["error_code"] == "injected_settlement_failure"
            builds = await db.read(lambda u: u.document_builds(agent, source["id"]))
            receipt = next(row for row in builds if row["id"] == final["attempt"]["build_id"])
            assert receipt["state"] == "failed"
            assert sum(row["state"] == "ready" for row in builds) == 1  # Only the original save remains published.
            assert (await documents.search(agent, "atomic"))["items"]
            assert await service.close() and await documents.close()

    asyncio.run(scenario())


def test_resume_only_original_erasure_and_forget_derived_memory_in_same_commit(tmp_path):
    async def scenario():
        clock = Clock()
        async with opened(tmp_path, clock) as (_, db, objects, agent):
            documents = DocumentService(db, objects)
            source = await documents.save(agent, "Private", "private maintenance evidence")
            memory = await db.write(
                lambda u: u.remember(agent, "derived fact", document_revision_id=source["revision_id"], quote="private")
            )
            erasure = await db.write(lambda u: u.document_erasure_begin(agent, source["id"], 1))
            await db.write(lambda u: u.document_erasure_finish(agent, erasure["id"], "injected_failure"))
            service = MaintenanceService(db, documents, clock, Observation())
            job = await service.submit(
                agent, {"kind": "document_erasure_resume", "key": "resume", "erasure_job_id": erasure["id"]}
            )
            await service.tick()
            final = await settled(service, agent, job["id"])
            assert final["state"] == "succeeded" and final["erasure_job_id"] == erasure["id"]
            assert (await documents.index_status(agent, source["id"]))["erasure_job"]["state"] == "done"
            assert not (await documents.search(agent, "private"))["items"]
            memory_row = await db.read(
                lambda u: dict(
                    u.connection.execute(select(s.memory_entries).where(s.memory_entries.c.id == memory["id"]))
                    .mappings()
                    .one()
                )
            )
            assert memory_row["deleted_at"]
            assert await service.close() and await documents.close()

    asyncio.run(scenario())


def test_instance_drains_maintenance_before_closing_documents_database_and_lock(tmp_path, monkeypatch):
    from omni.adapters.platform.paths import InstanceLock
    from omni.composition.instance import open_instance

    async def scenario():
        instance = await open_instance(tmp_path, capabilities=())
        agent = (await instance._database.read(lambda u: u.query("agents", "")))[0]["id"]
        source = await instance.documents.save(agent, "Owned", "running source")
        original = instance.documents._source
        entered, release = asyncio.Event(), asyncio.Event()

        async def paused(*args):
            entered.set()
            await release.wait()
            return await original(*args)

        monkeypatch.setattr(instance.documents, "_source", paused)
        job = await instance.maintenance.submit(agent, rebuild(source))
        try:
            async with asyncio.timeout(10):
                await entered.wait()
            result = await instance.close(0.02)
            assert not result["complete"] and "maintenance" in result["residual"]
            assert not instance._database.metrics["closed"]
            assert not instance.documents.health()["closing"]
            with pytest.raises(BusinessError, match="already running"):
                InstanceLock(instance.paths).acquire()
            assert (await instance.maintenance.get(agent, job["id"]))["state"] == "running"
        finally:
            release.set()
            assert (await instance.close(10))["complete"]
        lock = InstanceLock(instance.paths)
        lock.acquire()
        lock.close()

    asyncio.run(scenario())


def test_owned_maintenance_reports_fixed_cardinality_measurement_and_auditable_identity(tmp_path):
    from omni.composition.instance import open_instance

    async def scenario():
        instance = await open_instance(tmp_path, capabilities=())
        try:
            agent = (await instance._database.read(lambda u: u.query("agents", "")))[0]["id"]
            source = await instance.documents.save(agent, "Private title", "private-maintenance-body")
            job = await instance.maintenance.submit(agent, rebuild(source, "private-maintenance-key"))
            assert (await settled(instance.maintenance, agent, job["id"]))["state"] == "succeeded"
            # The persisted receipt may precede task return; metrics finalize when the owned span exits.
            async with asyncio.timeout(10):
                while instance.maintenance.health()["active"]:
                    await asyncio.sleep(0.01)
            series = instance._observation._metrics.snapshot()["series"]
            rows = [row for row in series if row["labels"]["operation"] == "maintenance"]
            assert len(rows) == 1 and rows[0]["labels"] == {"operation": "maintenance", "outcome": "ok"}
            assert rows[0]["count"] == 1
            records = await instance._database.read(
                lambda u: list(
                    u.connection.execute(select(s.audit).where(s.audit.c.operation_id == job["id"])).mappings()
                )
            )
            assert {row["kind"] for row in records} == {
                "maintenance.queued",
                "maintenance.claimed",
                "maintenance.started",
                "maintenance.build_bound",
                "maintenance.succeeded",
            }
            assert all(row["agent_id"] == agent for row in records)
            assert "private-maintenance-body" not in str(records) and "private-maintenance-key" not in str(records)
        finally:
            assert (await instance.close())["complete"]

    asyncio.run(scenario())


def test_unavailable_settlement_keeps_actual_task_owned_until_receipt_is_durable(tmp_path, monkeypatch):
    async def scenario():
        clock = Clock()
        async with opened(tmp_path, clock) as (_, db, objects, agent):
            documents = DocumentService(db, objects)
            source = await documents.save(agent, "Evidence", "settlement recovery source")
            original_fail = MaintenanceRepositories.maintenance_fail

            async def failed_source(*args):
                raise BusinessError("object_io_failed", "Injected source failure")

            def blocked_settlement(*args):
                raise OSError("Injected database unavailability")

            monkeypatch.setattr(documents, "_source", failed_source)
            monkeypatch.setattr(MaintenanceRepositories, "maintenance_fail", blocked_settlement)
            service = MaintenanceService(db, documents, clock, Observation())
            job = await service.submit(agent, rebuild(source))
            await service.tick()
            try:
                async with asyncio.timeout(10):
                    while service.health()["error_code"] != "maintenance_settlement_pending":
                        await asyncio.sleep(0.01)
                assert service.health()["active"] == service.health()["settlement_pending"] == 1
                assert not await service.close(0)
                assert (await service.get(agent, job["id"]))["state"] == "running"
            finally:
                monkeypatch.setattr(MaintenanceRepositories, "maintenance_fail", original_fail)
                assert await service.close()
                assert await documents.close()
            final = await service.get(agent, job["id"])
            assert final["state"] == "failed" and final["error_code"] == "object_io_failed"
            assert service.health()["active"] == service.health()["settlement_pending"] == 0

    asyncio.run(scenario())
