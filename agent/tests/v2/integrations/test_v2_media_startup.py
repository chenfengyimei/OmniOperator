"""Cancelled media callers cannot discard a still-owned process-creation result."""

from __future__ import annotations

import asyncio
import contextvars
import sys
from textwrap import dedent

import pytest
from omni_test_support.v2.media_transport import Worker

from omni.adapters.media import MediaProcessor
from omni.adapters.platform.runtime.processes import OwnedProcess
from omni.domain.contracts import BusinessError


@pytest.fixture
def delayed_spawn(monkeypatch):
    def configure(*, failure=False):
        entered, release = asyncio.Event(), asyncio.Event()
        old, fresh = Worker("old"), Worker("fresh")
        calls, cancellations = [], []
        error = OSError("injected process creation failure")

        async def spawn(argv, **options):
            assert options["env"]["HF_HUB_OFFLINE"] == "1"
            calls.append(options)
            if len(calls) == 1:
                entered.set()
                try:
                    await release.wait()
                except asyncio.CancelledError:
                    cancellations.append(True)
                    raise
                if failure:
                    raise error
                return old
            return fresh

        monkeypatch.setattr(OwnedProcess, "spawn", spawn)
        return entered, release, old, fresh, calls, cancellations, error

    return configure


@pytest.mark.parametrize("failure", [False, True])
def test_cancelled_request_keeps_native_creation_until_close_observes_its_result(delayed_spawn, failure):
    async def scenario():
        entered, release, old, _, calls, cancellations, _ = delayed_spawn(failure=failure)
        processor = MediaProcessor()
        task = asyncio.create_task(processor._request("probe", {}, 20))
        await entered.wait()
        task.cancel()
        with pytest.raises(asyncio.CancelledError):
            await task
        try:
            assert not cancellations
            assert processor.status().get("starting") and processor.status()["cleanup_pending"]
            assert not processor.status()["available"] and not old.frames
            assert not await processor.close(0)
        finally:
            release.set()
            assert await processor.close(5)
        assert len(calls) == 1 and not cancellations and not processor.status().get("starting")
        assert not processor.status()["cleanup_pending"]
        assert old.reclaimed == (not failure)

    asyncio.run(scenario())


def test_next_request_reclaims_late_start_before_launching_and_dispatching_new_worker(delayed_spawn):
    async def scenario():
        entered, release, old, fresh, calls, cancellations, _ = delayed_spawn()
        processor = MediaProcessor()
        task = asyncio.create_task(processor._request("probe", {}, 20))
        await entered.wait()
        task.cancel()
        with pytest.raises(asyncio.CancelledError):
            await task
        release.set()
        try:
            assert (await processor._request("probe", {}, 20))["worker"] == "fresh"
            assert old.reclaimed and not old.frames and len(fresh.frames) == 1
            assert len(calls) == 2 and not cancellations
            assert processor._owned is fresh and not processor.status()["cleanup_pending"]
        finally:
            assert await processor.close()

    asyncio.run(scenario())


def test_cancelled_close_does_not_cancel_or_forget_pending_creation(delayed_spawn):
    async def scenario():
        entered, release, old, _, calls, cancellations, _ = delayed_spawn()
        processor = MediaProcessor()
        request = asyncio.create_task(processor._request("probe", {}, 20))
        await entered.wait()
        request.cancel()
        with pytest.raises(asyncio.CancelledError):
            await request
        close = asyncio.create_task(processor.close())
        await asyncio.sleep(0)
        close.cancel()
        try:
            with pytest.raises(asyncio.CancelledError):
                await close
            assert processor.status().get("starting") and processor.status()["cleanup_pending"]
            assert not cancellations
        finally:
            release.set()
            assert await processor.close()
        assert old.reclaimed and len(calls) == 1

    asyncio.run(scenario())


def test_successful_start_is_reused_and_leaves_no_pending_creation(delayed_spawn):
    async def scenario():
        _, release, old, _, calls, cancellations, _ = delayed_spawn()
        release.set()
        processor = MediaProcessor()
        try:
            for _ in range(2):
                assert (await processor._request("probe", {}, 20))["worker"] == "old"
            assert len(calls) == 1 and not cancellations and len(old.frames) == 2
            assert not processor.status().get("starting") and not processor.status()["cleanup_pending"]
        finally:
            assert await processor.close()
        assert old.reclaimed

    asyncio.run(scenario())


def test_failed_start_preserves_original_exception_and_explicit_new_request_can_retry(delayed_spawn):
    async def scenario():
        _, release, old, fresh, calls, _, error = delayed_spawn(failure=True)
        release.set()
        processor = MediaProcessor()
        try:
            with pytest.raises(OSError) as caught:
                await processor._request("probe", {}, 20)
            assert caught.value is error and processor._owned is None
            assert not processor.status().get("starting") and not processor.status()["cleanup_pending"]
            assert (await processor._request("probe", {}, 20))["worker"] == "fresh"
            assert len(calls) == 2 and not old.frames and len(fresh.frames) == 1
        finally:
            assert await processor.close()

    asyncio.run(scenario())


def test_pending_start_rejects_followup_without_cancelling_creation_or_launching_twice(delayed_spawn, monkeypatch):
    async def scenario():
        entered, release, old, _, calls, cancellations, _ = delayed_spawn()
        processor = MediaProcessor()
        first = asyncio.create_task(processor._request("probe", {}, 20))
        await entered.wait()
        first.cancel()
        with pytest.raises(asyncio.CancelledError):
            await first
        loop, offset = asyncio.get_running_loop(), [0.0]
        original_time = loop.time
        monkeypatch.setattr(loop, "time", lambda: original_time() + offset[0])
        loop.call_soon(lambda: offset.__setitem__(0, 6.0))
        try:
            with pytest.raises(BusinessError) as caught:
                await processor._request("probe", {}, 20)
            assert caught.value.code == "media_cleanup_pending"
            assert len(calls) == 1 and not cancellations and not old.frames
            assert processor.status()["cleanup_pending"] and processor.status().get("starting")
        finally:
            monkeypatch.setattr(loop, "time", original_time)
            release.set()
            assert await processor.close()

    asyncio.run(scenario())


def test_reclamation_uses_budget_remaining_after_waiting_for_creation(delayed_spawn, monkeypatch):
    async def scenario():
        entered, release, old, _, _, _, _ = delayed_spawn()
        processor = MediaProcessor()
        first = asyncio.create_task(processor._request("probe", {}, 20))
        await entered.wait()
        first.cancel()
        with pytest.raises(asyncio.CancelledError):
            await first
        remaining, terminate = [], old.terminate

        async def reclaim(timeout_s):
            remaining.append(timeout_s)
            return await terminate(timeout_s)

        monkeypatch.setattr(old, "terminate", reclaim)
        loop, offset = asyncio.get_running_loop(), [0.0]
        original_time = loop.time
        monkeypatch.setattr(loop, "time", lambda: original_time() + offset[0])
        cleanup = asyncio.create_task(processor._terminate(10))
        await asyncio.sleep(0)
        offset[0] = 7.0
        release.set()
        try:
            assert await cleanup
            assert len(remaining) == 1 and 0 < remaining[0] <= 3.0
            assert old.reclaimed and not processor.status()["cleanup_pending"]
        finally:
            monkeypatch.setattr(loop, "time", original_time)
            release.set()
            assert await processor.close()

    asyncio.run(scenario())


def test_process_creation_task_does_not_keep_cancelled_request_context(monkeypatch):
    async def scenario():
        request_context = contextvars.ContextVar("owned-request-context", default="")
        token = request_context.set("request-private-value")
        seen, old = [], Worker()

        async def spawn(argv, **options):
            seen.append(request_context.get())
            return old

        monkeypatch.setattr(OwnedProcess, "spawn", spawn)
        processor = MediaProcessor()
        try:
            await processor._request("probe", {}, 20)
            assert seen == [""]
        finally:
            request_context.reset(token)
            assert await processor.close()

    asyncio.run(scenario())


def test_real_late_spawn_is_not_misreported_closed_and_is_eventually_reclaimed(tmp_path, monkeypatch):
    script = tmp_path / "owned_startup_worker.py"
    script.write_text(
        dedent("""\
            import json, sys
            for line in sys.stdin:
                request = json.loads(line)
                print(json.dumps({'version':1, 'id':request['id'], 'result':{'ok':True}}), flush=True)
            """),
        encoding="utf-8",
    )
    spawn = OwnedProcess.spawn
    terminate = OwnedProcess.terminate

    async def scenario():
        created, release, workers = asyncio.Event(), asyncio.Event(), []

        async def delayed(argv, **options):
            owned = await spawn(argv, **options)
            workers.append(owned)
            created.set()
            await release.wait()
            return owned

        monkeypatch.setattr(OwnedProcess, "spawn", delayed)
        processor = MediaProcessor([sys.executable, str(script)])
        request = asyncio.create_task(processor._request("probe", {}, 20))
        try:
            await asyncio.wait_for(created.wait(), 5)
            assert len(workers) == 1 and workers[0].process.returncode is None
            request.cancel()
            with pytest.raises(asyncio.CancelledError):
                await request
            assert not await processor.close(0)
            release.set()
            assert await processor.close(10)
            assert workers[0].reclaimed and processor._owned is None
            assert not processor.status().get("starting") and not processor.status()["cleanup_pending"]
        finally:
            release.set()
            if not request.done():
                request.cancel()
            await asyncio.gather(request, return_exceptions=True)
            assert await processor.close(10)
            for owned in workers:
                assert await terminate(owned, 10)

    asyncio.run(scenario())
