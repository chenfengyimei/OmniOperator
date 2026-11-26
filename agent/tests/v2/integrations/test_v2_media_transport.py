"""Media requests preserve worker ownership until reclamation and respect closing admission."""

from __future__ import annotations

from omni_test_support.v2.media_transport import (
    BusinessError as BusinessError,
)
from omni_test_support.v2.media_transport import (
    MediaProcessor as MediaProcessor,
)
from omni_test_support.v2.media_transport import (
    OwnedProcess as OwnedProcess,
)
from omni_test_support.v2.media_transport import (
    SimpleNamespace as SimpleNamespace,
)
from omni_test_support.v2.media_transport import (
    Worker as Worker,
)
from omni_test_support.v2.media_transport import (
    asyncio as asyncio,
)
from omni_test_support.v2.media_transport import (
    dedent as dedent,
)
from omni_test_support.v2.media_transport import (
    empty_stderr as empty_stderr,
)
from omni_test_support.v2.media_transport import (
    json as json,
)
from omni_test_support.v2.media_transport import (
    processor_with as processor_with,
)
from omni_test_support.v2.media_transport import (
    pytest as pytest,
)
from omni_test_support.v2.media_transport import (
    sys as sys,
)


@pytest.mark.parametrize("closing", ["stop_accepting", "close"])
def test_queued_request_rechecks_closing_before_any_write(closing):
    async def scenario():
        gate = asyncio.Event()
        old = Worker(read_gate=gate)
        processor, _, spawns = processor_with(old)
        first = asyncio.create_task(processor._request("probe", {}, 20))
        await old.read_started.wait()
        queued = asyncio.create_task(processor._request("frames", {}, 180))
        await asyncio.sleep(0)
        close = None
        if closing == "close":
            close = asyncio.create_task(processor.close())
            await asyncio.sleep(0)
        else:
            processor.stop_accepting()
        gate.set()
        try:
            assert (await first)["worker"] == "old"
            with pytest.raises(BusinessError) as caught:
                await queued
            assert caught.value.code == "media_processor_closing"
            assert len(old.frames) == 1 and not spawns
        finally:
            gate.set()
            await asyncio.gather(first, queued, return_exceptions=True)
            if close is not None:
                assert await close
            else:
                assert await processor.close()

    asyncio.run(scenario())


@pytest.mark.parametrize("code", ["media_cleanup_pending", "media_processor_closing"])
def test_pre_dispatch_rejection_is_failed_without_artifact_or_replaying_the_old_job(tmp_path, code):
    from omni_test_support.v2.media import BV, FakeProcessor, _media_instance, _seed, _settle

    async def scenario():
        backend = FakeProcessor()
        instance = await _media_instance(tmp_path, processor=backend)
        try:
            agent, config = await _seed(instance)
            source = await instance.media.register(agent, str(config["id"]), BV)
            backend.failure = BusinessError(code, "Pre-dispatch rejection")
            submitted = await instance.media.submit(agent, str(source["id"]), "transcribe", {})
            failed = await _settle(instance, agent, submitted["id"], wait_for_cleanup=True)
            assert failed["state"] == "failed" and failed["error_code"] == code
            assert not (await instance.media.source(agent, str(source["id"])))["latest_transcript_id"]
            assert not (instance.paths.control / "media-staging" / str(submitted["id"])).exists()
            backend.failure = None
            fresh = await _settle(
                instance, agent, (await instance.media.submit(agent, str(source["id"]), "transcribe", {}))["id"]
            )
            assert fresh["state"] == "done"
            assert (await instance.media.job(agent, str(failed["id"])))["state"] == "failed"
        finally:
            assert (await instance.close())["complete"]

    asyncio.run(scenario())


@pytest.mark.parametrize("exited,pending", [(False, True), (True, False), (True, True)])
def test_unreclaimed_worker_blocks_new_request_without_losing_owner(exited, pending):
    async def scenario():
        old = Worker(exited=exited, reclaim=False)
        processor, _, spawns = processor_with(old)
        processor._cleanup_pending = pending
        for _ in range(2):
            with pytest.raises(BusinessError) as caught:
                await processor._request("probe", {}, 20)
            assert caught.value.code == "media_cleanup_pending"
            assert processor._owned is old and processor.status()["cleanup_pending"]
            assert not processor.status()["available"] and not old.frames and not spawns
        assert old.terminate_calls == 2
        old.reclaim = True
        assert (await processor._request("probe", {}, 20))["worker"] == "fresh"
        assert old.reclaimed and len(spawns) == 1
        assert await processor.close()

    asyncio.run(scenario())


@pytest.mark.parametrize("exited,pending", [(False, True), (True, False), (True, True)])
def test_new_request_finishes_old_reclamation_before_starting_fresh_worker(exited, pending):
    async def scenario():
        old = Worker(exited=exited)
        processor, fresh, spawns = processor_with(old)
        processor._cleanup_pending = pending
        result = await processor._request("probe", {}, 20)
        assert result["worker"] == "fresh" and old.reclaimed and old.terminate_calls == 1
        assert processor._owned is fresh and spawns == [fresh]
        assert not old.frames and not processor.status()["cleanup_pending"]
        assert await processor.close()

    asyncio.run(scenario())


@pytest.mark.parametrize("method", ["_terminate", "close"])
@pytest.mark.parametrize("failure", ["cancel", "exception"])
def test_interrupted_reclamation_retains_exact_owner_for_later_cleanup(method, failure):
    async def scenario():
        gate, error = asyncio.Event(), OSError("injected reclaim failure")
        old = Worker(cleanup_gate=gate, cleanup_error=error if failure == "exception" else None)
        processor, _, spawns = processor_with(old)
        task = asyncio.create_task(getattr(processor, method)())
        await old.cleanup_started.wait()
        if failure == "cancel":
            task.cancel()
            with pytest.raises(asyncio.CancelledError):
                await task
        else:
            gate.set()
            with pytest.raises(OSError) as caught:
                await task
            assert caught.value is error
        assert processor._owned is old and processor.status()["cleanup_pending"]
        assert not processor.status()["available"] and not spawns
        old.cleanup_error = None
        gate.set()
        assert await processor.close()
        assert old.reclaimed and processor._owned is None and not processor.status()["cleanup_pending"]

    asyncio.run(scenario())


def test_owner_remains_visible_while_reclamation_is_in_progress():
    async def scenario():
        gate = asyncio.Event()
        old = Worker(cleanup_gate=gate)
        processor, _, _ = processor_with(old)
        task = asyncio.create_task(processor._terminate())
        await old.cleanup_started.wait()
        try:
            assert processor._owned is old and processor.status()["cleanup_pending"]
            assert not processor.status()["available"]
        finally:
            gate.set()
            assert await task
        assert processor._owned is None and not processor.status()["cleanup_pending"]
        assert processor.status()["available"]

    asyncio.run(scenario())


def test_concurrent_close_calls_serialize_actual_reclamation():
    async def scenario():
        gate = asyncio.Event()
        old = Worker(cleanup_gate=gate)
        processor, _, _ = processor_with(old)
        first = asyncio.create_task(processor.close())
        await old.cleanup_started.wait()
        second = asyncio.create_task(processor.close())
        await asyncio.sleep(0)
        try:
            assert old.terminate_calls == 1 and processor._owned is old
        finally:
            gate.set()
            assert await first and await second
        assert old.terminate_calls == 1 and old.reclaimed

    asyncio.run(scenario())


def test_short_close_wait_does_not_cancel_an_already_admitted_request():
    async def scenario():
        gate = asyncio.Event()
        old = Worker(read_gate=gate)
        processor, _, _ = processor_with(old)
        active = asyncio.create_task(processor._request("probe", {}, 20))
        await old.read_started.wait()
        assert not await processor.close(0)
        assert not active.done() and processor._owned is old and old.terminate_calls == 0
        gate.set()
        assert (await active)["worker"] == "old"
        assert await processor.close()
        assert old.reclaimed

    asyncio.run(scenario())


def test_normal_requests_reuse_the_live_worker_and_close_is_idempotent():
    async def scenario():
        old = Worker()
        processor, _, spawns = processor_with(old)
        for operation in ("probe", "frames"):
            assert (await processor._request(operation, {}, 20))["worker"] == "old"
        assert not spawns and not old.terminate_calls and len(old.frames) == 2
        assert await processor.close() and await processor.close()
        assert old.terminate_calls == 1 and old.reclaimed

    asyncio.run(scenario())


@pytest.mark.parametrize("stage", ["reclaim", "spawn"])
def test_closing_during_reclaim_or_spawn_prevents_dispatch(stage):
    async def scenario():
        gate = asyncio.Event()
        old = Worker(cleanup_gate=gate, read_gate=gate)
        processor, fresh, spawns = processor_with(old)
        if stage == "reclaim":
            processor._cleanup_pending = True
        else:
            processor._owned = None

            async def spawn():
                spawns.append(fresh)
                await gate.wait()
                processor._owned = fresh
                return fresh

            processor._spawn = spawn
        task = asyncio.create_task(processor._request("probe", {}, 20))
        await asyncio.sleep(0)
        processor.stop_accepting()
        gate.set()
        try:
            with pytest.raises(BusinessError) as caught:
                await task
            assert caught.value.code == "media_processor_closing"
            assert not old.frames and not fresh.frames
            if stage == "reclaim":
                assert old.reclaimed and not spawns
            else:
                assert processor._owned is fresh
        finally:
            await asyncio.gather(task, return_exceptions=True)
            assert await processor.close()

    asyncio.run(scenario())


def test_real_worker_is_reclaimed_before_new_request_after_cancelled_cleanup(tmp_path, monkeypatch):
    marker, release, log = (tmp_path / name for name in ("started", "release", "requests.jsonl"))
    script = tmp_path / "owned_transport_worker.py"
    script.write_text(
        dedent("""\
            import json, os, sys, time
            from pathlib import Path
            marker, release, log = map(Path, sys.argv[1:])
            for line in sys.stdin:
                request = json.loads(line)
                with log.open('a', encoding='utf-8') as output:
                    output.write(json.dumps({'pid':os.getpid(), 'id':request['id']}) + '\\n')
                if not marker.exists():
                    marker.write_text('started', encoding='utf-8')
                    while not release.exists():
                        time.sleep(0.01)
                print(json.dumps({'version':1, 'id':request['id'], 'result':{'ok':True, 'pid':os.getpid()}}), flush=True)
            """),
        encoding="utf-8",
    )
    terminate = OwnedProcess.terminate
    calls = []

    async def fail_first_reclaim(owned, timeout_s=5):
        calls.append(owned.process.pid)
        if len(calls) == 1:
            return False
        return await terminate(owned, timeout_s)

    monkeypatch.setattr(OwnedProcess, "terminate", fail_first_reclaim)

    async def scenario():
        processor = MediaProcessor([sys.executable, str(script), str(marker), str(release), str(log)])
        task = asyncio.create_task(processor._request("probe", {}, 20))
        old = None
        try:
            async with asyncio.timeout(5):
                while not marker.exists():
                    await asyncio.sleep(0.01)
            old = processor._owned
            assert old is not None
            original_worker_pid = json.loads(log.read_text(encoding="utf-8").splitlines()[0])["pid"]
            task.cancel()
            with pytest.raises(asyncio.CancelledError):
                await task
            assert processor._owned is old and processor.status()["cleanup_pending"]
            release.write_text("release", encoding="utf-8")
            result = await processor._request("probe", {}, 20)
            assert old.reclaimed and result["pid"] != original_worker_pid
            rows = [json.loads(line) for line in log.read_text(encoding="utf-8").splitlines()]
            assert [row["pid"] for row in rows] == [original_worker_pid, result["pid"]]
        finally:
            release.touch()
            if not task.done():
                task.cancel()
            await asyncio.gather(task, return_exceptions=True)
            assert await processor.close(10)
            if old is not None:
                assert await terminate(old, 10)

    asyncio.run(scenario())
