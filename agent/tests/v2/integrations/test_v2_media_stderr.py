"""Media diagnostics are consumed without retaining text or abandoning drain ownership."""

from __future__ import annotations

import asyncio
import sys
from textwrap import dedent

import pytest
from omni_test_support.v2.media_transport import Worker, processor_with

from omni.adapters import media


def test_stderr_is_consumed_in_bounded_chunks_and_logs_only_byte_counts(caplog):
    async def scenario():
        secret = b"PRIVATE_MEDIA_DIAGNOSTIC_42"
        data, sizes = secret * 1000, []
        reader = asyncio.StreamReader()
        reader.feed_data(data)
        reader.feed_eof()

        class Observed:
            async def read(self, size):
                sizes.append(size)
                return await reader.read(size)

        processor = media.MediaProcessor()
        await processor._drain_stderr(Observed())
        assert sizes and all(0 < size <= 4096 for size in sizes)
        assert processor.status()["stderr_bytes"] == len(data)
        assert secret.decode() not in caplog.text
        records = [row for row in caplog.records if getattr(row, "event", "") == "media.stderr"]
        assert len(records) == 1 and records[0].bytes == len(data) and records[0].exc_info is None

    asyncio.run(scenario())


def test_stderr_counter_saturates_without_retaining_output():
    async def scenario():
        processor = media.MediaProcessor()
        processor._stderr_bytes = (1 << 53) - 3
        reader = asyncio.StreamReader()
        reader.feed_data(b"discarded diagnostic bytes")
        reader.feed_eof()
        await processor._drain_stderr(reader)
        assert processor.status()["stderr_bytes"] == (1 << 53) - 1

    asyncio.run(scenario())


@pytest.mark.parametrize("failure", ["error", "cancel"])
def test_failed_drain_is_visible_without_logging_native_error_body(caplog, failure):
    async def scenario():
        processor = media.MediaProcessor()

        async def drain():
            if failure == "error":
                raise OSError("PRIVATE_STDERR_READ_FAILURE")
            await asyncio.Event().wait()

        task = asyncio.create_task(drain())
        processor._stderr = task
        if failure == "cancel":
            task.cancel()
        await asyncio.gather(task, return_exceptions=True)
        status = processor.status()
        assert status.get("stderr_failed") and status["cleanup_pending"] and not status["available"]
        assert "PRIVATE_STDERR_READ_FAILURE" not in caplog.text
        assert await processor.close()
        assert processor._stderr is None and not processor.status()["cleanup_pending"]

    asyncio.run(scenario())


def test_close_retains_reclaimed_process_until_stderr_task_finishes():
    async def scenario():
        old = Worker()
        processor, _, _ = processor_with(old)
        release = asyncio.Event()
        drain = asyncio.create_task(release.wait())
        processor._stderr = drain
        try:
            assert not await processor.close(0)
            assert old.reclaimed and processor._owned is old and processor._stderr is drain
            assert not drain.done() and processor.status()["cleanup_pending"]
        finally:
            release.set()
            await drain
            assert await processor.close()
        assert processor._owned is None and processor._stderr is None

    asyncio.run(scenario())


def test_cancelled_close_does_not_cancel_or_forget_stderr_task():
    async def scenario():
        old = Worker()
        processor, _, _ = processor_with(old)
        release = asyncio.Event()
        drain = asyncio.create_task(release.wait())
        processor._stderr = drain
        close = asyncio.create_task(processor.close())
        await old.cleanup_started.wait()
        await asyncio.sleep(0)
        close.cancel()
        try:
            with pytest.raises(asyncio.CancelledError):
                await close
            assert processor._owned is old and processor._stderr is drain and not drain.cancelled()
            assert processor.status()["cleanup_pending"]
        finally:
            release.set()
            await asyncio.gather(close, drain, return_exceptions=True)
            assert await processor.close()

    asyncio.run(scenario())


def test_failed_reader_is_checked_before_dispatch_to_existing_worker():
    async def scenario():
        old = Worker()
        processor, fresh, spawns = processor_with(old)

        async def fail():
            raise OSError("PRIVATE_READER_FAILURE")

        failed = asyncio.create_task(fail())
        processor._stderr = failed
        await asyncio.gather(failed, return_exceptions=True)
        try:
            assert (await processor._request("probe", {}, 20))["worker"] == "fresh"
            assert old.reclaimed and not old.frames and len(fresh.frames) == 1 and len(spawns) == 1
        finally:
            assert await processor.close()

    asyncio.run(scenario())


def test_old_reader_callback_cannot_mark_new_worker_unavailable():
    async def scenario():
        processor = media.MediaProcessor()

        async def fail():
            raise OSError("old diagnostic reader")

        old = asyncio.create_task(fail())
        await asyncio.gather(old, return_exceptions=True)
        release = asyncio.Event()
        current = asyncio.create_task(release.wait())
        processor._stderr = current
        try:
            processor._stderr_finished(old)
            assert processor.status()["available"] and not processor.status()["cleanup_pending"]
            assert not processor.status()["stderr_failed"]
        finally:
            release.set()
            await current
            assert await processor.close()

    asyncio.run(scenario())


def test_empty_diagnostic_path_keeps_normal_worker_reuse():
    async def scenario():
        old = Worker()
        processor, _, spawns = processor_with(old)
        try:
            for _ in range(2):
                assert (await processor._request("probe", {}, 20))["worker"] == "old"
            assert not spawns and not old.terminate_calls
        finally:
            assert await processor.close()

    asyncio.run(scenario())


def test_real_stderr_flood_does_not_block_replies_or_reclamation(tmp_path, monkeypatch, caplog):
    script = tmp_path / "owned_stderr_worker.py"
    script.write_text(
        dedent("""\
            import json, sys
            diagnostic = (b'PRIVATE_FLOOD_DIAGNOSTIC' * 16384)[:262144]
            for line in sys.stdin:
                request = json.loads(line)
                sys.stderr.buffer.write(diagnostic)
                sys.stderr.buffer.flush()
                print(json.dumps({'version':1, 'id':request['id'], 'result':{'ok':True}}), flush=True)
            """),
        encoding="utf-8",
    )
    monkeypatch.setattr(media, "FRAME_LIMIT", 4096)

    async def rescue(stream):
        while await stream.read(4096):
            pass

    async def scenario():
        processor = media.MediaProcessor([sys.executable, str(script)])
        request, safety_reader = None, None
        try:
            for _ in range(2):
                request = asyncio.create_task(processor._request("probe", {}, 20))
                # Observe progress without changing the 20-second processing budget.
                result = await asyncio.wait_for(asyncio.shield(request), 5)
                assert result["ok"] is True
            assert await processor.close(10)
            assert processor.status()["stderr_bytes"] == 2 * 262144
            assert processor._stderr is None and not processor.status()["cleanup_pending"]
            assert "PRIVATE_FLOOD_DIAGNOSTIC" not in caplog.text
        finally:
            # Old-source negative controls have no reader; release only their test pipe.
            owned = processor._owned
            if getattr(processor, "_stderr", None) is None and owned is not None:
                safety_reader = asyncio.create_task(rescue(owned.process.stderr))
            if request is not None:
                if not request.done():
                    request.cancel()
                await asyncio.gather(request, return_exceptions=True)
            assert await processor.close(10)
            if safety_reader is not None:
                await safety_reader

    asyncio.run(scenario())
