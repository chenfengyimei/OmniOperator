"""Transcript read workflows own every lookup and finish admitted reads while draining."""

from __future__ import annotations

import asyncio

import pytest

from omni.application.media import MediaService
from omni.domain.contracts import BusinessError
from omni.domain.media import transcript_payload

ENTRIES = ["full", "window", "latest", "window_for"]
FINAL_READ = {"full": 2, "window": 3, "latest": 3, "window_for": 4}


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
    def __init__(self, gate, hold):
        self.gate, self.hold, self.calls, self.exists = gate, hold, 0, True

    async def read(self, operation):
        self.calls += 1
        row = operation(self)
        if self.calls == self.hold:
            await self.gate.wait()
        return row

    async def write(self, operation):
        raise AssertionError("Transcript reads cannot write")

    def media_transcript_get(self, agent_id, transcript_id):
        if not self.exists or agent_id != "agent" or transcript_id != "transcript":
            raise BusinessError("media_transcript_not_found", "No such transcript", status=404)
        return {"id": "transcript", "source_id": "source", "object_id": "object", "kind": "asr", "language": "zh"}

    def media_transcript_latest(self, agent_id, source_id):
        if source_id != "source":
            raise BusinessError("media_transcript_not_found", "No transcript in this source", status=404)
        return self.media_transcript_get(agent_id, "transcript")


class Objects:
    def __init__(self, gate, hold):
        self.gate, self.hold, self.calls = gate, hold, 0

    async def get(self, agent_id, object_id):
        assert (agent_id, object_id) == ("agent", "object")
        self.calls += 1
        if self.hold:
            await self.gate.wait()
        return {}, transcript_payload("asr", "zh", [[0.0, 1.0, "hello"]])


class Processor:
    def __init__(self):
        self.close_calls = []

    def status(self):
        return {"available": True}

    async def close(self, timeout_s=30):
        self.close_calls.append(timeout_s)
        return True


def fixture(tmp_path, entry, stage="first"):
    gate = Gate()
    hold = 1 if stage == "first" else FINAL_READ[entry] if stage == "final" else 0
    database, objects, processor = Database(gate, hold), Objects(gate, stage == "object"), Processor()
    service = MediaService(database, objects, None, None, processor, None, tmp_path)

    async def invoke(agent_id="agent", source_id="source"):
        if entry == "full":
            return await service.transcript(agent_id, "transcript")
        if entry == "window_for":
            return await service.transcript_window_for(agent_id, "transcript", 0, 12000)
        return await service.transcript_window(
            agent_id, source_id, "transcript" if entry == "window" else None, 0, 12000
        )

    return service, database, objects, processor, gate, invoke


def assert_result(entry, result):
    if entry == "full":
        assert result["segments"] == [[0.0, 1.0, "hello"]]
    else:
        assert result["text"] == "[00:00-00:01] hello" and result["start"] == 0
        assert result["end"] == result["total_chars"] == len(result["text"])


@pytest.mark.parametrize("entry", ENTRIES)
@pytest.mark.parametrize("stage", ["first", "object", "final"])
def test_read_is_owned_from_first_lookup_through_final_check_and_finishes_after_close(tmp_path, entry, stage):
    async def scenario():
        service, database, objects, processor, gate, invoke = fixture(tmp_path, entry, stage)
        caller = asyncio.create_task(invoke())
        try:
            await asyncio.wait_for(gate.entered.wait(), 8)
            assert service.health()["pending"] == 1
            assert not await service.close(0) and not processor.close_calls
            assert not caller.done() and not gate.cancelled
            gate.release.set()
            assert_result(entry, await caller)
            assert database.calls == FINAL_READ[entry] and objects.calls == 1
            assert service.health()["pending"] == 0
        finally:
            gate.release.set()
            await asyncio.gather(caller, return_exceptions=True)
            assert await service.close()

    asyncio.run(scenario())


@pytest.mark.parametrize("entry", ENTRIES)
def test_closed_read_entry_rejects_before_any_database_or_object_call(tmp_path, entry):
    async def scenario():
        service, database, objects, _, gate, invoke = fixture(tmp_path, entry)
        gate.release.set()
        service.stop_accepting()
        try:
            with pytest.raises(BusinessError) as caught:
                await invoke()
            assert caught.value.code == "media_closing"
            assert database.calls == objects.calls == 0 and not service._tasks
        finally:
            assert await service.close()

    asyncio.run(scenario())


@pytest.mark.parametrize("entry", ENTRIES)
def test_cancelled_initial_lookup_waiter_keeps_the_complete_read_owned(tmp_path, entry):
    async def scenario():
        service, database, objects, _, gate, invoke = fixture(tmp_path, entry)
        caller = asyncio.create_task(invoke())
        try:
            await asyncio.wait_for(gate.entered.wait(), 8)
            caller.cancel()
            with pytest.raises(asyncio.CancelledError):
                await caller
            assert not gate.cancelled and service.health()["pending"] == 1
            assert not await service.close(0)
            gate.release.set()
            results = await asyncio.gather(*service._tasks)
            assert len(results) == 1
            assert_result(entry, results[0])
            assert database.calls == FINAL_READ[entry] and objects.calls == 1 and not service._tasks
        finally:
            gate.release.set()
            await asyncio.gather(caller, return_exceptions=True)
            assert await service.close()

    asyncio.run(scenario())


@pytest.mark.parametrize("entry", ENTRIES)
def test_cancelled_close_waiter_cannot_cancel_or_reject_the_already_admitted_read(tmp_path, entry):
    async def scenario():
        service, _, _, _, gate, invoke = fixture(tmp_path, entry)
        caller, closer = asyncio.create_task(invoke()), None
        try:
            await asyncio.wait_for(gate.entered.wait(), 8)
            closer = asyncio.create_task(service.close())
            await asyncio.sleep(0)
            closer.cancel()
            with pytest.raises(asyncio.CancelledError):
                await closer
            assert not gate.cancelled and not caller.done() and service.health()["pending"] == 1
            gate.release.set()
            assert_result(entry, await caller)
        finally:
            gate.release.set()
            await asyncio.gather(caller, *([closer] if closer is not None else []), return_exceptions=True)
            assert await service.close()

    asyncio.run(scenario())


@pytest.mark.parametrize("entry", ENTRIES)
def test_source_deletion_during_object_read_still_rejects_loaded_body(tmp_path, entry):
    async def scenario():
        service, database, _, _, gate, invoke = fixture(tmp_path, entry, "object")
        caller = asyncio.create_task(invoke())
        try:
            await asyncio.wait_for(gate.entered.wait(), 8)
            database.exists = False
            gate.release.set()
            with pytest.raises(BusinessError) as caught:
                await caller
            assert caught.value.code == "media_transcript_not_found" and caught.value.status == 404
            assert not service._tasks
        finally:
            gate.release.set()
            await asyncio.gather(caller, return_exceptions=True)
            assert await service.close()

    asyncio.run(scenario())


@pytest.mark.parametrize("entry", ENTRIES)
def test_other_agent_never_reads_the_transcript_object(tmp_path, entry):
    async def scenario():
        service, _, objects, _, gate, invoke = fixture(tmp_path, entry)
        gate.release.set()
        try:
            with pytest.raises(BusinessError) as caught:
                await invoke(agent_id="other-agent")
            assert caught.value.code == "media_transcript_not_found" and caught.value.status == 404
            assert not objects.calls and not service._tasks
        finally:
            assert await service.close()

    asyncio.run(scenario())


def test_source_pairing_is_checked_before_object_read(tmp_path):
    async def scenario():
        service, _, objects, _, gate, invoke = fixture(tmp_path, "window")
        gate.release.set()
        try:
            with pytest.raises(BusinessError) as caught:
                await invoke(source_id="other-source")
            assert caught.value.code == "media_transcript_not_found" and not objects.calls
        finally:
            assert await service.close()

    asyncio.run(scenario())


@pytest.mark.parametrize("entry", ["window", "window_for"])
@pytest.mark.parametrize("disconnect", [False, True])
def test_real_window_lookup_keeps_database_and_objects_until_admitted_read_finishes(tmp_path, entry, disconnect):
    from test_v2_media import BV, _media_instance, _seed, _settle

    async def scenario():
        instance = await _media_instance(tmp_path)
        gate, caller = Gate(), None
        try:
            agent, config = await _seed(instance)
            source = await instance.media.register(agent, str(config["id"]), BV)
            job = await instance.media.submit(agent, str(source["id"]), "subtitle", {})
            assert (await _settle(instance, agent, job["id"]))["state"] == "done"
            transcript_id = str((await instance.media.source(agent, str(source["id"])))["latest_transcript_id"])
            original, armed = instance.media._db, [True]

            class HoldFirstLookup:
                async def read(self, operation):
                    row = await original.read(operation)
                    if armed and isinstance(row, dict) and row.get("id") == transcript_id:
                        armed.clear()
                        await gate.wait()
                    return row

                async def write(self, operation):
                    raise AssertionError("Reading cannot write")

            instance.media._db = HoldFirstLookup()
            if entry == "window":
                read = instance.media.transcript_window(agent, str(source["id"]), transcript_id, 0, 12000)
            else:
                read = instance.media.transcript_window_for(agent, transcript_id, 0, 12000)
            caller = asyncio.create_task(read)
            await asyncio.wait_for(gate.entered.wait(), 8)
            if disconnect:
                caller.cancel()
                with pytest.raises(asyncio.CancelledError):
                    await caller
            assert not gate.cancelled and instance.media.health()["pending"] == 1
            await instance.close(0)
            status = await instance._lifetime._coordinator
            assert not status["complete"] and "media" in status["residual"]
            assert not instance._database.metrics["closed"]
            owners = tuple(instance.media._tasks)
            gate.release.set()
            results = await asyncio.gather(*owners)
            assert len(results) == 1 and results[0]["text"] == "第一行字幕\n第二行字幕"
            if not disconnect:
                assert await caller == results[0]
            assert not instance.media._tasks
            assert (await original.read(lambda u: u.media_job_get(str(job["id"]))))["state"] == "done"
        finally:
            gate.release.set()
            if caller is not None:
                await asyncio.gather(caller, return_exceptions=True)
            assert (await instance.close())["complete"]

    asyncio.run(scenario())
