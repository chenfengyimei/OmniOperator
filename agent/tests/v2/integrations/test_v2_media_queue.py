"""Cancelled queue waiters release ownership without interrupting active cleanup."""

from __future__ import annotations

import asyncio

import pytest

from omni.application.media import MediaService


class Database:
    def __init__(self):
        self.rows = {}
        self.cancelled, self.deliver = asyncio.Event(), asyncio.Event()
        self.deliver.set()

    async def read(self, callback):
        return callback(self)

    async def write(self, callback):
        row = callback(self)
        if row["state"] == "cancelled":
            self.cancelled.set()
            await self.deliver.wait()
        return row

    def media_job_create(self, agent, source, operation, plan):
        job_id = f"job-{len(self.rows) + 1}"
        self.rows[job_id] = {"id": job_id, "agent_id": agent, "state": "queued", "operation": operation}
        return dict(self.rows[job_id])

    def media_job_get(self, job_id):
        return dict(self.rows[job_id])

    def media_job_cancel(self, job_id):
        if self.rows[job_id]["state"] == "queued":
            self.rows[job_id]["state"] = "cancelled"
        return dict(self.rows[job_id])


class Processor:
    def status(self):
        return {"available": True}

    async def close(self, timeout_s=30):
        return True


def fixture(tmp_path):
    database = Database()
    service = MediaService(database, None, None, None, Processor(), None, tmp_path)
    entered, finish = asyncio.Event(), asyncio.Event()
    executions = []

    async def execute(job_id):
        row = database.rows[job_id]
        if row["state"] == "cancelled":
            return
        row["state"] = "running"
        executions.append(job_id)
        entered.set()
        try:
            await finish.wait()
        except asyncio.CancelledError:
            row["state"] = "cancelled"
            raise
        row["state"] = "done"

    service._execute = execute
    return service, database, entered, finish, executions


@pytest.mark.parametrize("operation", ["subtitle", "transcribe", "frames"])
def test_cancelled_queue_job_retires_while_both_slots_remain_occupied(tmp_path, operation):
    async def scenario():
        service, database, _, finish, executions = fixture(tmp_path)
        for _ in range(2):
            await service._slots.acquire()
        try:
            job = await service.submit("agent", "source", operation, {})
            assert (await service.cancel("agent", job["id"]))["state"] == "cancelled"
            assert not service._tasks and not executions
            assert service.health()["queued_jobs"] == 0
            # An artificial external permit holder cannot prevent this service from closing.
            assert await service.close(0)
            assert database.rows[job["id"]]["state"] == "cancelled"
        finally:
            finish.set()
            for _ in range(2):
                service._slots.release()
            assert await service.close()

    asyncio.run(scenario())


def test_repeated_submit_cancel_does_not_accumulate_waiters(tmp_path):
    async def scenario():
        service, database, _, finish, executions = fixture(tmp_path)
        for _ in range(2):
            await service._slots.acquire()
        try:
            for _ in range(24):
                job = await service.submit("agent", "source", "subtitle", {})
                assert (await service.cancel("agent", job["id"]))["state"] == "cancelled"
                assert service.health()["pending"] == 0
            assert len(database.rows) == 24 and not executions
        finally:
            finish.set()
            for _ in range(2):
                service._slots.release()
            assert await service.close()

    asyncio.run(scenario())


def test_cancellation_before_the_first_coroutine_step_retires_queue_ownership(tmp_path):
    async def scenario():
        service, _, entered, _, _ = fixture(tmp_path)

        async def never_started():
            entered.set()
            await asyncio.Event().wait()

        task = asyncio.create_task(never_started(), name="media-job-unstarted")
        # Inject ownership as admission would, including on the clean old-source control.
        if not hasattr(service, "_queued_jobs"):
            service._queued_jobs = set()
        service._tasks.add(task)
        service._queued_jobs.add(task)
        task.add_done_callback(service._retire)
        task.cancel()
        await asyncio.gather(task, return_exceptions=True)
        try:
            assert not entered.is_set() and task.cancelled()
            assert not service._tasks and not service._queued_jobs
        finally:
            assert await service.close()

    asyncio.run(scenario())


def test_repeated_cancel_retires_a_waiter_whose_database_row_is_already_cancelled(tmp_path):
    async def scenario():
        service, database, _, finish, executions = fixture(tmp_path)
        for _ in range(2):
            await service._slots.acquire()
        try:
            job = await service.submit("agent", "source", "subtitle", {})
            database.rows[job["id"]]["state"] = "cancelled"
            assert (await service.cancel("agent", job["id"]))["state"] == "cancelled"
            assert not service._tasks and not executions
        finally:
            finish.set()
            for _ in range(2):
                service._slots.release()
            assert await service.close()

    asyncio.run(scenario())


@pytest.mark.parametrize("eager", [False, True])
def test_started_job_is_not_reported_as_queued_and_still_cancels(tmp_path, eager):
    async def scenario():
        service, database, entered, finish, executions = fixture(tmp_path)
        loop = asyncio.get_running_loop()
        previous = loop.get_task_factory()
        if eager:
            loop.set_task_factory(asyncio.eager_task_factory)
        try:
            job = await service.submit("agent", "source", "subtitle", {})
            await entered.wait()
            assert service.health()["queued_jobs"] == 0
            assert len(executions) == 1 and database.rows[job["id"]]["state"] == "running"
            assert (await service.cancel("agent", job["id"]))["state"] == "cancelled"
            assert not service._tasks
        finally:
            finish.set()
            assert await service.close()
            loop.set_task_factory(previous)

    asyncio.run(scenario())


@pytest.mark.parametrize("eager", [False, True])
def test_cancelled_waiter_does_not_consume_a_released_slot_for_the_next_job(tmp_path, eager):
    async def scenario():
        service, database, entered, finish, executions = fixture(tmp_path)
        loop, held = asyncio.get_running_loop(), 0
        previous = loop.get_task_factory()
        if eager:
            loop.set_task_factory(asyncio.eager_task_factory)
        try:
            for _ in range(2):
                await service._slots.acquire()
                held += 1
            cancelled = await service.submit("agent", "source", "subtitle", {})
            await service.cancel("agent", cancelled["id"])
            assert not service._tasks
            service._slots.release()
            held -= 1
            fresh = await service.submit("agent", "source", "subtitle", {})
            await asyncio.wait_for(entered.wait(), 8)
            assert executions == [fresh["id"]]
            finish.set()
            await asyncio.gather(*service._tasks)
            assert database.rows[fresh["id"]]["state"] == "done"
            assert database.rows[cancelled["id"]]["state"] == "cancelled"
        finally:
            finish.set()
            for _ in range(held):
                service._slots.release()
            assert await service.close()
            loop.set_task_factory(previous)

    asyncio.run(scenario())


def test_disconnected_cancel_caller_does_not_lose_queue_retirement_after_commit(tmp_path):
    async def scenario():
        service, database, _, finish, executions = fixture(tmp_path)
        caller = None
        for _ in range(2):
            await service._slots.acquire()
        try:
            job = await service.submit("agent", "source", "subtitle", {})
            database.deliver.clear()
            caller = asyncio.create_task(service.cancel("agent", job["id"]))
            await asyncio.wait_for(database.cancelled.wait(), 8)
            caller.cancel()
            with pytest.raises(asyncio.CancelledError):
                await caller
            assert service.health()["pending"] == 2
            assert not await service.close(0)
            database.deliver.set()
            await asyncio.gather(*service._tasks, return_exceptions=True)
            assert not service._tasks and not executions
            assert database.rows[job["id"]]["state"] == "cancelled"
        finally:
            database.deliver.set()
            finish.set()
            for _ in range(2):
                service._slots.release()
            if caller is not None:
                await asyncio.gather(caller, return_exceptions=True)
            assert await service.close()

    asyncio.run(scenario())


def test_real_database_repeated_queued_cancels_free_admission_without_running_work(tmp_path):
    from omni_test_support.v2.media import BV, FakeGateway, _media_instance, _seed, _settle

    async def scenario():
        gateway = FakeGateway()
        instance = await _media_instance(tmp_path, gateway=gateway)
        held = 0
        try:
            agent, config = await _seed(instance)
            source = await instance.media.register(agent, str(config["id"]), BV)
            for _ in range(2):
                await instance.media._slots.acquire()
                held += 1
            cancelled = []
            for _ in range(12):
                job = await instance.media.submit(agent, str(source["id"]), "transcribe", {})
                cancelled.append(job["id"])
                row = await instance.media.cancel(agent, str(job["id"]))
                assert row["state"] == "cancelled" and row["error_code"] == "media_cancelled"
                assert not instance.media._tasks
            assert gateway.reads == [("video", BV)] and not gateway.downloads
            assert not (await instance.media.source(agent, str(source["id"])))["active_jobs"]
            assert not any((instance.media._staging / str(job_id)).exists() for job_id in cancelled)
            instance.media._slots.release()
            held -= 1
            fresh = await instance.media.submit(agent, str(source["id"]), "subtitle", {})
            assert (await _settle(instance, agent, fresh["id"]))["state"] == "done"
            for job_id in cancelled:
                assert (await instance.media.job(agent, str(job_id)))["state"] == "cancelled"
        finally:
            for _ in range(held):
                instance.media._slots.release()
            assert (await instance.close())["complete"]

    asyncio.run(scenario())


def test_real_two_active_jobs_continue_when_the_third_queued_job_is_cancelled(tmp_path):
    from omni_test_support.v2.media import BV, FakeProcessor, _media_instance, _seed, _settle

    async def scenario():
        both_started, finish = asyncio.Event(), asyncio.Event()

        class Active(FakeProcessor):
            started = 0

            async def transcribe(self, *args):
                self.started += 1
                if self.started == 2:
                    both_started.set()
                await finish.wait()
                return await super().transcribe(*args)

        processor = Active()
        instance = await _media_instance(tmp_path, processor=processor)
        try:
            agent, config = await _seed(instance)
            source = await instance.media.register(agent, str(config["id"]), BV)
            active = [await instance.media.submit(agent, str(source["id"]), "transcribe", {}) for _ in range(2)]
            await asyncio.wait_for(both_started.wait(), 8)
            owners = set(instance.media._tasks)
            queued = await instance.media.submit(agent, str(source["id"]), "frames", {})
            assert (await instance.media.cancel(agent, str(queued["id"])))["state"] == "cancelled"
            assert instance.media._tasks == owners
            assert all(not task.cancelling() and not task.done() for task in owners)
            assert not await instance.media.close(0) and not instance._database.metrics["closed"]
            finish.set()
            for job in active:
                assert (await _settle(instance, agent, job["id"]))["state"] == "done"
            assert len(processor.calls) == 2
            assert not (instance.media._staging / str(queued["id"])).exists()
        finally:
            finish.set()
            assert (await instance.close())["complete"]

    asyncio.run(scenario())


def test_real_cancel_commit_remains_owned_after_caller_disconnect(tmp_path):
    from omni_test_support.v2.media import BV, FakeGateway, _media_instance, _seed

    async def scenario():
        committed, deliver = asyncio.Event(), asyncio.Event()
        gateway = FakeGateway()
        instance = await _media_instance(tmp_path, gateway=gateway)
        held, caller = 0, None
        try:
            agent, config = await _seed(instance)
            source = await instance.media.register(agent, str(config["id"]), BV)
            for _ in range(2):
                await instance.media._slots.acquire()
                held += 1
            job = await instance.media.submit(agent, str(source["id"]), "transcribe", {})
            original, armed = instance.media._db, [True]

            class HoldCommittedResult:
                async def read(self, callback):
                    return await original.read(callback)

                async def write(self, callback):
                    row = await original.write(callback)
                    if (
                        armed
                        and isinstance(row, dict)
                        and row.get("id") == job["id"]
                        and row.get("state") == "cancelled"
                    ):
                        armed.clear()
                        committed.set()
                        await deliver.wait()
                    return row

            instance.media._db = HoldCommittedResult()
            caller = asyncio.create_task(instance.media.cancel(agent, str(job["id"])))
            await asyncio.wait_for(committed.wait(), 8)
            caller.cancel()
            with pytest.raises(asyncio.CancelledError):
                await caller
            assert instance.media.health()["pending"] == 2
            assert not await instance.media.close(0) and not instance._database.metrics["closed"]
            deliver.set()
            await asyncio.gather(*instance.media._tasks, return_exceptions=True)
            assert not instance.media._tasks and not gateway.downloads
            assert (await instance.media.job(agent, str(job["id"])))["state"] == "cancelled"
            assert not (await instance.media.source(agent, str(source["id"])))["active_jobs"]
        finally:
            deliver.set()
            for _ in range(held):
                instance.media._slots.release()
            if caller is not None:
                await asyncio.gather(caller, return_exceptions=True)
            assert (await instance.close())["complete"]

    asyncio.run(scenario())
