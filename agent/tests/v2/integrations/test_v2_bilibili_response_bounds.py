"""HTTP response limits apply while streaming, with no read of the rejected response tail."""

from __future__ import annotations

from omni_test_support.v2.bilibili_response_bounds import (
    BV as BV,
)
from omni_test_support.v2.bilibili_response_bounds import (
    CONFIG as CONFIG,
)
from omni_test_support.v2.bilibili_response_bounds import (
    LIMITS as LIMITS,
)
from omni_test_support.v2.bilibili_response_bounds import (
    NAV as NAV,
)
from omni_test_support.v2.bilibili_response_bounds import (
    SOURCE as SOURCE,
)
from omni_test_support.v2.bilibili_response_bounds import (
    SUBTITLE as SUBTITLE,
)
from omni_test_support.v2.bilibili_response_bounds import (
    BilibiliGateway as BilibiliGateway,
)
from omni_test_support.v2.bilibili_response_bounds import (
    Body as Body,
)
from omni_test_support.v2.bilibili_response_bounds import (
    BusinessError as BusinessError,
)
from omni_test_support.v2.bilibili_response_bounds import (
    Redactor as Redactor,
)
from omni_test_support.v2.bilibili_response_bounds import (
    Secrets as Secrets,
)
from omni_test_support.v2.bilibili_response_bounds import (
    asyncio as asyncio,
)
from omni_test_support.v2.bilibili_response_bounds import (
    bilibili as bilibili,
)
from omni_test_support.v2.bilibili_response_bounds import (
    builtins as builtins,
)
from omni_test_support.v2.bilibili_response_bounds import (
    fixture as fixture,
)
from omni_test_support.v2.bilibili_response_bounds import (
    gzip as gzip,
)
from omni_test_support.v2.bilibili_response_bounds import (
    httpx as httpx,
)
from omni_test_support.v2.bilibili_response_bounds import (
    json as json,
)
from omni_test_support.v2.bilibili_response_bounds import (
    payload as payload,
)
from omni_test_support.v2.bilibili_response_bounds import (
    pytest as pytest,
)
from omni_test_support.v2.bilibili_response_bounds import (
    read as read,
)


@pytest.mark.parametrize("stage", LIMITS)
@pytest.mark.parametrize("mode", ["one_chunk", "filled_buffer"])
def test_overflow_stops_before_tail_or_appending_the_violating_chunk(monkeypatch, stage, mode):
    sizes = []

    class Buffer(builtins.bytearray):
        def extend(self, part):
            super().extend(part)
            sizes.append(len(self))

    monkeypatch.setattr(bilibili, "bytearray", Buffer, raising=False)

    async def scenario():
        limit = LIMITS[stage]
        parts = [b" " * (limit + 1)] if mode == "one_chunk" else [b" " * limit, b"x"]
        body = Body(parts, failure=AssertionError("Rejected response tail was consumed"))
        gateway, _, _, responses = fixture(body)
        try:
            with pytest.raises(BusinessError) as caught:
                await read(gateway, stage)
            assert caught.value.code == "bilibili_response_too_large" and caught.value.status == 502
            assert body.reads == len(parts) and body.closed and responses[0].is_closed
            assert max(sizes, default=0) <= limit
        finally:
            assert await gateway.close()

    asyncio.run(scenario())


@pytest.mark.parametrize("stage", LIMITS)
@pytest.mark.parametrize("size", [None, -1, 0])
def test_valid_unicode_json_and_exact_decoded_byte_bound_preserve_results(stage, size):
    async def scenario():
        data = payload(stage, None if size is None else LIMITS[stage] + size)
        # Split the UTF-8 text and JSON tokens across response chunks.
        body = Body([data[index : index + 7] for index in range(0, min(len(data), 100), 7)])
        prefix = b"".join(body.parts)
        body.parts = [*body.parts, data[len(prefix) :]]
        gateway, _, _, responses = fixture(body)
        try:
            result = await read(gateway, stage)
            assert result == ({"fixture": True} if stage == "api" else {"language": "zh-CN", "text": "合法字幕"})
            assert body.closed and responses[0].is_closed and responses[0].is_stream_consumed
        finally:
            assert await gateway.close()

    asyncio.run(scenario())


@pytest.mark.parametrize("stage", LIMITS)
@pytest.mark.parametrize("bad", ["redirect", "status", "content_type"])
def test_rejected_headers_close_response_without_consuming_body(stage, bad):
    async def scenario():
        body = Body([b"should not be read"])
        gateway, _, _, responses = fixture(
            body,
            status={"redirect": 302, "status": 503}.get(bad, 200),
            headers={"content-type": "text/html"} if bad == "content_type" else {},
        )
        try:
            with pytest.raises(BusinessError) as caught:
                await read(gateway, stage)
            assert caught.value.code == "bilibili_read_unavailable"
            assert not body.reads and body.closed and responses[0].is_closed
        finally:
            assert await gateway.close()

    asyncio.run(scenario())


@pytest.mark.parametrize("stage", LIMITS)
def test_malformed_streamed_json_closes_response_and_keeps_existing_error(stage):
    async def scenario():
        body = Body([b"not ", b"json"])
        gateway, _, _, responses = fixture(body)
        try:
            with pytest.raises(BusinessError) as caught:
                await read(gateway, stage)
            assert caught.value.code == "bilibili_response_invalid"
            assert body.closed and responses[0].is_closed
        finally:
            assert await gateway.close()

    asyncio.run(scenario())


@pytest.mark.parametrize("stage", LIMITS)
def test_limit_counts_decoded_bytes_for_compressed_responses(stage):
    async def scenario():
        compressed = gzip.compress(b" " * (LIMITS[stage] + 1))
        body = Body([compressed], failure=AssertionError("Decoded overflow must stop the response"))
        gateway, _, _, responses = fixture(body, headers={"content-encoding": "gzip"})
        try:
            with pytest.raises(BusinessError) as caught:
                await read(gateway, stage)
            assert caught.value.code == "bilibili_response_too_large"
            assert len(compressed) < LIMITS[stage] and body.closed and responses[0].is_closed
        finally:
            assert await gateway.close()

    asyncio.run(scenario())


@pytest.mark.parametrize("entry", ["owner", "source"])
@pytest.mark.parametrize("failure", ["cancel", "disconnect"])
def test_stream_interruption_closes_subtitle_and_retires_actual_owner(entry, failure):
    async def scenario():
        entered, release = asyncio.Event(), asyncio.Event()
        body = Body([], gate=(entered, release), failure=httpx.ReadError("fixture disconnect"))
        gateway, _, _, responses = fixture(body)
        task = asyncio.create_task(
            gateway.read(CONFIG, "subtitle", BV)
            if entry == "owner"
            else gateway.read_source(CONFIG, "subtitle", SOURCE)
        )
        try:
            await asyncio.wait_for(entered.wait(), 8)
            assert not await gateway.close(0) and task in gateway._reads and not body.closed
            if failure == "cancel":
                task.cancel()
                with pytest.raises(asyncio.CancelledError):
                    await task
            else:
                release.set()
                with pytest.raises(BusinessError) as caught:
                    await task
                assert caught.value.code == "bilibili_read_unavailable"
            assert body.closed and responses[0].is_closed and not gateway._reads
        finally:
            release.set()
            await asyncio.gather(task, return_exceptions=True)
            assert await gateway.close()

    asyncio.run(scenario())


def test_real_media_job_rejects_oversize_before_tail_and_can_explicitly_retry(tmp_path, monkeypatch):
    from omni_test_support.v2.media import _media_instance, _seed, _settle

    async def scenario():
        body = Body(
            [b" " * LIMITS["subtitle"], b"x"], failure=AssertionError("Oversize subtitle tail must not be read")
        )
        gateway, state, _, responses = fixture(body)
        instance = await _media_instance(tmp_path, gateway=gateway)
        published, publish = [], instance.media._objects.publish

        async def observe_publish(*args, **kwargs):
            published.append(args)
            return await publish(*args, **kwargs)

        monkeypatch.setattr(instance.media._objects, "publish", observe_publish)
        try:
            agent, config = await _seed(instance)
            source = await instance.media.register(agent, str(config["id"]), BV)
            job = await instance.media.submit(agent, str(source["id"]), "subtitle", {})
            failed = await _settle(instance, agent, job["id"])
            assert failed["state"] == "failed" and failed["error_code"] == "bilibili_response_too_large"
            assert body.reads == 2 and body.closed and responses[0].is_closed
            assert not instance.media._processor.calls
            assert not (instance.media._staging / str(job["id"])).exists()
            current = await instance.media.source(agent, str(source["id"]))
            assert not current["latest_transcript_id"] and not current["latest_frames_id"]
            assert not published
            state["body"] = Body([payload("subtitle")])
            fresh = await instance.media.submit(agent, str(source["id"]), "subtitle", {})
            assert (await _settle(instance, agent, fresh["id"]))["state"] == "done"
            assert (await instance.media.job(agent, str(job["id"])))["state"] == "failed"
            transcript_id = (await instance.media.source(agent, str(source["id"])))["latest_transcript_id"]
            transcript = await instance.media.transcript(agent, str(transcript_id))
            assert transcript["kind"] == "subtitle" and transcript["segments"] == [[0.0, 0.0, "合法字幕"]]
        finally:
            assert (await instance.close())["complete"]

    asyncio.run(scenario())
