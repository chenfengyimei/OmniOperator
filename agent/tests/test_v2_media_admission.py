"""Media admission stays owned until its I/O and any newly spawned job finish."""

from __future__ import annotations

import asyncio

import pytest

from omni.application.media import MediaService
from omni.domain.contracts import BusinessError

VIDEO = {"bvid": "BV1xx411c7mD", "aid": "123", "cid": "456", "title": "Owned fixture", "duration_s": 120}


class Gate:
    def __init__(self):
        self.entered, self.release = asyncio.Event(), asyncio.Event()
        self.cancelled = False

    async def wait(self):
        self.entered.set()
        try:
            await self.release.wait()
        except asyncio.CancelledError:
            self.cancelled = True
            raise


class Database:
    def __init__(self):
        self.gate, self.calls, self.created = Gate(), [], []

    async def read(self, operation):
        self.calls.append("read")
        return operation(self)

    async def write(self, operation):
        self.calls.append("write")
        row = operation(self)
        if row.get("operation"):
            # The create was committed, but delivery to submit is still pending.
            await self.gate.wait()
        return row

    def bilibili_read_authorized(self, agent_id, config_id, revision_id=""):
        return {"value": {}, "revision_id": "revision", "authority": "fixture-authority"}

    def media_source_register(self, agent_id, row):
        self.created.append(row)
        return {"id": "source", **row}

    def media_job_create(self, agent_id, source_id, operation, plan):
        row = {"id": "job", "operation": operation, "params": plan}
        self.created.append(row)
        return row


class Gateway:
    def __init__(self):
        self.gate, self.calls = Gate(), []

    async def read(self, configuration, operation, target, limit=10):
        self.calls.append(operation)
        assert operation == "video"
        await self.gate.wait()
        return VIDEO


class Processor:
    def __init__(self):
        self.gate, self.calls, self.close_budgets = Gate(), [], []
        self.close_ok = True

    def status(self):
        return {"available": True}

    async def probe(self):
        self.calls.append("probe")
        await self.gate.wait()
        return {"ok": True}

    async def close(self, timeout_s=30):
        self.close_budgets.append(timeout_s)
        return self.close_ok


def fixture(tmp_path, operation):
    db, gateway, processor = Database(), Gateway(), Processor()
    service = MediaService(db, None, None, gateway, processor, None, tmp_path)
    jobs, job_gate = [], Gate()
    job_gate.release.set()

    async def execute(job_id):
        jobs.append(job_id)
        await job_gate.wait()

    service._execute = execute
    gate = {"register": gateway.gate, "submit": db.gate, "probe": processor.gate}[operation]

    async def invoke():
        if operation == "register":
            return await service.register("agent", "config", VIDEO["bvid"])
        if operation == "submit":
            return await service.submit("agent", "source", "subtitle", {})
        return await service.processor_probe()

    return service, db, gateway, processor, gate, invoke, jobs, job_gate


@pytest.mark.parametrize("operation", ["register", "submit", "probe"])
def test_stopped_admission_never_calls_a_port(tmp_path, operation):
    async def scenario():
        service, db, gateway, processor, gate, invoke, _, _ = fixture(tmp_path, operation)
        gate.release.set()
        service.stop_accepting()
        try:
            with pytest.raises(BusinessError) as error:
                await invoke()
            assert error.value.code == "media_closing" and error.value.status == 503
            assert not db.calls and not gateway.calls and not processor.calls
        finally:
            assert await service.close()

    asyncio.run(scenario())


@pytest.mark.parametrize("operation", ["register", "submit", "probe"])
def test_cancelled_caller_keeps_admission_io_owned(tmp_path, operation):
    async def scenario():
        service, db, _, processor, gate, invoke, jobs, _ = fixture(tmp_path, operation)
        caller = asyncio.create_task(invoke())
        await gate.entered.wait()
        caller.cancel()
        try:
            with pytest.raises(asyncio.CancelledError):
                await caller
            assert not gate.cancelled and service.health()["pending"] == 1
            assert not await service.close(0) and not processor.close_budgets
        finally:
            gate.release.set()
            await asyncio.gather(caller, return_exceptions=True)
            assert await service.close()
        assert service.health()["pending"] == 0
        assert len(db.created) == (operation != "probe")
        assert jobs == (["job"] if operation == "submit" else [])

    asyncio.run(scenario())


@pytest.mark.parametrize("operation", ["register", "submit", "probe"])
def test_close_waits_for_each_admitted_kind(tmp_path, operation):
    async def scenario():
        service, _, _, processor, gate, invoke, _, _ = fixture(tmp_path, operation)
        caller = asyncio.create_task(invoke())
        await gate.entered.wait()
        try:
            assert service.health()["pending"] == 1
            assert not await service.close(0) and not processor.close_budgets
            assert not caller.done() and not gate.cancelled
        finally:
            gate.release.set()
            await caller
            assert await service.close()

    asyncio.run(scenario())


def test_close_follows_job_spawned_by_an_already_admitted_submit(tmp_path):
    async def scenario():
        service, _, _, processor, gate, invoke, jobs, job_gate = fixture(tmp_path, "submit")
        job_gate.release.clear()
        caller = asyncio.create_task(invoke())
        await gate.entered.wait()
        closer = asyncio.create_task(service.close())
        await asyncio.sleep(0)
        gate.release.set()
        try:
            assert (await caller)["id"] == "job"
            await job_gate.entered.wait()
            assert jobs == ["job"] and service.health()["pending"] == 1
            assert not closer.done() and not processor.close_budgets
        finally:
            gate.release.set()
            job_gate.release.set()
            await asyncio.gather(caller, closer)
            assert await service.close()

    asyncio.run(scenario())


def test_cancelled_close_preserves_admission_and_its_later_child(tmp_path):
    async def scenario():
        service, _, _, processor, gate, invoke, jobs, job_gate = fixture(tmp_path, "submit")
        job_gate.release.clear()
        caller = asyncio.create_task(invoke())
        await gate.entered.wait()
        closer = asyncio.create_task(service.close())
        await asyncio.sleep(0)
        closer.cancel()
        try:
            with pytest.raises(asyncio.CancelledError):
                await closer
            assert service.health()["pending"] == 1 and not gate.cancelled
            assert not processor.close_budgets
            gate.release.set()
            await caller
            await job_gate.entered.wait()
            assert not await service.close(0) and jobs == ["job"]
        finally:
            gate.release.set()
            job_gate.release.set()
            await asyncio.gather(caller, closer, return_exceptions=True)
            assert await service.close()

    asyncio.run(scenario())


@pytest.mark.parametrize("elapsed", [11, 31])
def test_admission_wait_and_processor_reclamation_share_one_budget(tmp_path, monkeypatch, elapsed):
    async def scenario():
        service, _, _, processor, gate, _, _, _ = fixture(tmp_path, "probe")
        caller = asyncio.create_task(service._run_bounded(gate.wait()))
        await gate.entered.wait()
        loop = asyncio.get_running_loop()
        clock, original_wait = [loop.time()], asyncio.wait

        async def wait(tasks, *, timeout):
            assert timeout == 30
            clock[0] += elapsed
            gate.release.set()
            return await original_wait(tasks, timeout=timeout)

        try:
            with monkeypatch.context() as patch:
                patch.setattr(loop, "time", lambda: clock[0])
                patch.setattr(asyncio, "wait", wait)
                assert await service.close(30)
            assert processor.close_budgets == [max(0, 30 - elapsed)]
        finally:
            gate.release.set()
            await caller
            assert await service.close()

    asyncio.run(scenario())


def test_processor_cleanup_retry_does_not_replay_completed_admission(tmp_path):
    async def scenario():
        service, db, gateway, processor, gate, invoke, _, _ = fixture(tmp_path, "register")
        gate.release.set()
        assert (await invoke())["id"] == "source"
        processor.close_ok = False
        assert not await service.close()
        processor.close_ok = True
        assert await service.close()
        assert gateway.calls == ["video"] and len(db.created) == 1 and len(processor.close_budgets) == 2

    asyncio.run(scenario())


def test_committed_job_survives_cancelled_submit_and_keeps_database_owned(tmp_path):
    from test_v2_media import _media_instance, _seed, _settle

    async def scenario():
        instance = await _media_instance(tmp_path)
        gate, committed = Gate(), []
        original = instance.media._db
        caller = None

        class DelayedResult:
            async def read(self, operation):
                return await original.read(operation)

            async def write(self, operation):
                row = await original.write(operation)
                if isinstance(row, dict) and row.get("operation") == "subtitle" and row.get("state") == "queued":
                    committed.append(row)
                    await gate.wait()
                return row

        try:
            agent, config = await _seed(instance)
            source = await instance.media.register(agent, config["id"], VIDEO["bvid"])
            instance.media._db = DelayedResult()
            caller = asyncio.create_task(instance.media.submit(agent, source["id"], "subtitle", {}))
            await gate.entered.wait()
            caller.cancel()
            with pytest.raises(asyncio.CancelledError):
                await caller
            assert not gate.cancelled and len(committed) == 1
            await instance.close(0)
            result = await instance._lifetime._coordinator
            assert not result["complete"] and "media" in result["residual"]
            assert not instance._database.metrics["closed"]
            gate.release.set()
            job = await _settle(instance, agent, committed[0]["id"])
            assert job["state"] == "done"
            view = await instance.media.source(agent, source["id"])
            transcript = await original.read(
                lambda unit: unit.media_transcript_get(agent, view["latest_transcript_id"])
            )
            assert transcript["job_id"] == job["id"] and transcript["object_id"]
            assert [kind for kind, _ in instance.media._bilibili.reads].count("subtitle") == 1
            assert (await instance.close())["complete"]
        finally:
            gate.release.set()
            if caller is not None:
                await asyncio.gather(caller, return_exceptions=True)
            assert (await instance.close())["complete"]

    asyncio.run(scenario())
