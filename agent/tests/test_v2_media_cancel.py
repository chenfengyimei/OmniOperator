"""Owner cancellation joins actual cleanup without cancelling it a second time."""

from __future__ import annotations

import asyncio

import pytest

from omni.application.media import MediaService
from omni.domain.contracts import BusinessError


class Database:
    def __init__(self, state="running", *, promote=False):
        self.row = {"id": "job", "agent_id": "agent", "state": state}
        self.promote, self.cancel_calls = promote, 0

    async def read(self, callback):
        return callback(self)

    async def write(self, callback):
        return callback(self)

    def media_job_get(self, job_id):
        return dict(self.row)

    def media_job_cancel(self, job_id):
        self.cancel_calls += 1
        if self.promote:
            self.row["state"] = "running"
        if self.row["state"] == "queued":
            self.row["state"] = "cancelled"
        return dict(self.row)


class Processor:
    async def close(self, timeout_s=30):
        return True


def fixture(tmp_path, state="running", *, promote=False):
    database = Database(state, promote=promote)
    service = MediaService(database, None, None, None, Processor(), None, tmp_path)
    started, work, cleanup, release, interrupted = (asyncio.Event() for _ in range(5))

    async def worker():
        started.set()
        try:
            await work.wait()
        except asyncio.CancelledError:
            cleanup.set()
            try:
                await release.wait()
            except asyncio.CancelledError:
                interrupted.set()
                raise
            database.row["state"] = "cancelled"
            raise

    task = asyncio.create_task(worker(), name="media-job-job")
    service._tasks.add(task)
    task.add_done_callback(service._retire)
    return service, database, task, started, work, cleanup, release, interrupted


def test_repeated_cancel_joins_one_cleanup_instead_of_interrupting_it(tmp_path):
    async def scenario():
        service, database, task, started, work, cleanup, release, interrupted = fixture(tmp_path)
        await started.wait()
        first = asyncio.create_task(service.cancel("agent", "job"))
        await cleanup.wait()
        second = asyncio.create_task(service.cancel("agent", "job"))
        await asyncio.sleep(0)
        try:
            assert task.cancelling() == 1 and not task.done() and task in service._tasks
            assert not first.done() and not second.done() and not interrupted.is_set()
            assert not await service.close(0)
        finally:
            release.set()
            work.set()
            await asyncio.gather(first, second, task, return_exceptions=True)
            assert await service.close()
        assert database.row["state"] == "cancelled" and not interrupted.is_set()

    asyncio.run(scenario())


def test_cancelled_waiter_propagates_cancellation_while_job_stays_owned(tmp_path):
    async def scenario():
        service, database, task, started, work, cleanup, release, interrupted = fixture(tmp_path)
        await started.wait()
        caller = asyncio.create_task(service.cancel("agent", "job"))
        await cleanup.wait()
        caller.cancel()
        try:
            with pytest.raises(asyncio.CancelledError):
                await caller
            assert task in service._tasks and not task.done() and task.cancelling() == 1
            assert not interrupted.is_set() and database.row["state"] == "running"
            assert not await service.close(0)
        finally:
            release.set()
            work.set()
            await asyncio.gather(caller, task, return_exceptions=True)
            assert await service.close()
        assert database.row["state"] == "cancelled"

    asyncio.run(scenario())


@pytest.mark.parametrize("state", ["done", "failed", "cancelled", "unknown"])
def test_terminal_job_does_not_interrupt_a_still_owned_task(tmp_path, state):
    async def scenario():
        service, database, task, started, work, _, release, _ = fixture(tmp_path, state)
        await started.wait()
        caller = asyncio.create_task(service.cancel("agent", "job"))
        await asyncio.sleep(0)
        try:
            assert task.cancelling() == 0 and not task.done()
            assert caller.done() and (await caller)["state"] == state and database.cancel_calls == 0
        finally:
            release.set()
            work.set()
            await asyncio.gather(caller, task, return_exceptions=True)
            assert await service.close()
        assert database.row["state"] == state

    asyncio.run(scenario())


def test_queued_to_running_race_continues_with_cancellation_of_the_owner(tmp_path):
    async def scenario():
        service, database, task, started, work, cleanup, release, _ = fixture(tmp_path, "queued", promote=True)
        await started.wait()
        caller = asyncio.create_task(service.cancel("agent", "job"))
        await asyncio.sleep(0)
        try:
            assert database.cancel_calls == 1 and task.cancelling() == 1
            await cleanup.wait()
            assert not caller.done() and not task.done()
        finally:
            release.set()
            work.set()
            await asyncio.gather(caller, task, return_exceptions=True)
            assert await service.close()
        assert database.row["state"] == "cancelled"

    asyncio.run(scenario())


def test_queued_cancel_keeps_its_terminal_state_without_cancelling_the_waiting_task(tmp_path):
    async def scenario():
        service, database, task, started, work, _, release, _ = fixture(tmp_path, "queued")
        await started.wait()
        try:
            assert (await service.cancel("agent", "job"))["state"] == "cancelled"
            assert task.cancelling() == 0 and database.cancel_calls == 1
        finally:
            work.set()
            release.set()
            await task
            assert await service.close()

    asyncio.run(scenario())


def test_other_agent_cannot_cancel_the_owner_task(tmp_path):
    async def scenario():
        service, database, task, started, work, _, release, _ = fixture(tmp_path)
        await started.wait()
        try:
            with pytest.raises(BusinessError) as error:
                await service.cancel("other-agent", "job")
            assert error.value.code == "media_job_not_found" and error.value.status == 404
            assert task.cancelling() == 0 and database.cancel_calls == 0
        finally:
            work.set()
            release.set()
            await task
            assert await service.close()

    asyncio.run(scenario())


def test_running_job_without_an_owned_task_is_not_falsely_marked_cancelled(tmp_path):
    async def scenario():
        database = Database()
        service = MediaService(database, None, None, None, Processor(), None, tmp_path)
        assert (await service.cancel("agent", "job"))["state"] == "running"
        assert database.cancel_calls == 0 and await service.close()

    asyncio.run(scenario())


@pytest.mark.parametrize("operation", ["transcribe", "frames"])
def test_real_job_repeated_cancel_preserves_processor_cleanup_until_it_finishes(tmp_path, operation):
    from test_v2_media import BV, FakeProcessor, _media_instance, _seed

    async def scenario():
        entered, cleanup, release, observed = (asyncio.Event() for _ in range(4))

        class SlowCleanup(FakeProcessor):
            interrupted = False

            async def perform(self):
                entered.set()
                try:
                    await asyncio.Event().wait()
                except asyncio.CancelledError:
                    cleanup.set()
                    try:
                        await release.wait()
                    except asyncio.CancelledError:
                        self.interrupted = True
                        raise
                    raise

            async def transcribe(self, *args):
                await self.perform()

            async def frames(self, *args):
                await self.perform()

        processor = SlowCleanup()
        instance = await _media_instance(tmp_path, processor=processor)
        callers = []
        try:
            agent, config = await _seed(instance)
            source = await instance.media.register(agent, str(config["id"]), BV)
            job = await instance.media.submit(agent, str(source["id"]), operation, {})
            await entered.wait()
            owner = next(task for task in instance.media._tasks if task.get_name() == f"media-job-{job['id']}")
            callers.append(asyncio.create_task(instance.media.cancel(agent, str(job["id"]))))
            await cleanup.wait()
            original = instance.media._db

            class ObserveRead:
                async def read(self, callback):
                    row = await original.read(callback)
                    if isinstance(row, dict) and row.get("id") == job["id"] and row.get("state") == "running":
                        observed.set()
                    return row

                async def write(self, callback):
                    return await original.write(callback)

            instance.media._db = ObserveRead()
            callers.append(asyncio.create_task(instance.media.cancel(agent, str(job["id"]))))
            await observed.wait()
            assert owner.cancelling() == 1 and not owner.done() and owner in instance.media._tasks
            assert not processor.interrupted and not await instance.media.close(0)
            release.set()
            rows = await asyncio.gather(*callers)
            assert all(row["state"] == "cancelled" for row in rows)
            assert not processor.interrupted and not (instance.media._staging / str(job["id"])).exists()
            assert not instance._database.metrics["closed"]
        finally:
            release.set()
            await asyncio.gather(*callers, return_exceptions=True)
            assert (await instance.close())["complete"]

    asyncio.run(scenario())


def test_real_queue_promotion_before_cancel_write_still_cancels_the_started_job(tmp_path):
    from test_v2_media import BV, FakeProcessor, _media_instance, _seed

    async def scenario():
        entered = asyncio.Event()

        class Waiting(FakeProcessor):
            async def transcribe(self, *args):
                entered.set()
                await asyncio.Event().wait()

        instance = await _media_instance(tmp_path, processor=Waiting())
        held, job_id = 0, None
        try:
            agent, config = await _seed(instance)
            source = await instance.media.register(agent, str(config["id"]), BV)
            for _ in range(2):
                await instance.media._slots.acquire()
                held += 1
            job = await instance.media.submit(agent, str(source["id"]), "transcribe", {})
            job_id, original, armed = str(job["id"]), instance.media._db, [True]

            class StartBeforeCancel:
                async def read(self, callback):
                    return await original.read(callback)

                async def write(self, callback):
                    nonlocal held
                    if armed:
                        armed.clear()
                        instance.media._slots.release()
                        held -= 1
                        await entered.wait()
                    return await original.write(callback)

            instance.media._db = StartBeforeCancel()
            row = await instance.media.cancel(agent, job_id)
            assert row["state"] == "cancelled"
            assert not (instance.media._staging / job_id).exists()
            assert not (await instance.media.source(agent, str(source["id"])))["latest_transcript_id"]
        finally:
            for _ in range(held):
                instance.media._slots.release()
            if job_id is not None:
                await instance.media.cancel(agent, job_id)
            assert (await instance.close())["complete"]

    asyncio.run(scenario())
