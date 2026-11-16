"""HTTP response limits apply while streaming, with no read of the rejected response tail."""

from __future__ import annotations

import asyncio
import builtins
import gzip
import json

import httpx
import pytest
from test_v2_bilibili_gateway import CONFIG, Secrets

from omni.adapters import bilibili
from omni.adapters.bilibili import BilibiliGateway
from omni.adapters.diagnostics import Redactor
from omni.domain.contracts import BusinessError

BV = "BV1xx411c7mD"
SOURCE = {"bvid": BV, "aid": "123", "cid": "789"}
SUBTITLE = "https://aisubtitle.hdslb.com/fixture.json"
NAV = {
    "isLogin": True,
    "mid": 456,
    "wbi_img": {"img_url": "/" + "a" * 32 + ".png", "sub_url": "/" + "b" * 32 + ".png"},
}
LIMITS = {"api": 524288, "subtitle": 2 * 1024 * 1024}


class Body(httpx.AsyncByteStream):
    def __init__(self, parts, *, gate=None, failure=None):
        self.parts, self.gate, self.failure = parts, gate, failure
        self.reads, self.closed = 0, False

    async def __aiter__(self):
        if self.gate is not None:
            entered, release = self.gate
            entered.set()
            await release.wait()
        for part in self.parts:
            self.reads += 1
            yield part
        if self.failure is not None:
            raise self.failure

    async def aclose(self):
        self.closed = True


def fixture(body, *, status=200, headers=None):
    calls, responses = [], []
    state = {"body": body}

    def handle(request):
        calls.append(request)
        if request.url.path in {"/bounded", "/fixture.json"}:
            response = httpx.Response(
                status, headers={"content-type": "application/json", **(headers or {})}, stream=state["body"]
            )
            responses.append(response)
            return response
        if request.url.path.endswith("/nav"):
            data = NAV
        elif request.url.path.endswith("/view"):
            data = {**SOURCE, "title": "Fixture", "duration": 120}
        elif request.url.path == "/x/player/wbi/v2":
            assert request.url.params["cid"] == SOURCE["cid"]
            data = {"subtitle": {"subtitles": [{"lan": "zh-CN", "subtitle_url": SUBTITLE}]}}
        else:
            raise AssertionError(f"Unexpected endpoint: {request.url.path}")
        return httpx.Response(200, json={"code": 0, "data": data})

    async def no_delivery(_):
        raise AssertionError("No publication")

    gateway = BilibiliGateway(
        Secrets(),
        Redactor(),
        no_delivery,
        client_factory=lambda **options: httpx.AsyncClient(transport=httpx.MockTransport(handle), **options),
    )
    return gateway, state, calls, responses


async def read(gateway, stage):
    async with gateway._client({}) as client:
        if stage == "api":
            return await gateway._get(client, "/bounded")
        return await gateway._subtitle(client, NAV, SOURCE)


def payload(stage, size=None):
    value = {"code": 0, "data": {"fixture": True}} if stage == "api" else {"body": [{"content": "合法字幕"}]}
    data = json.dumps(value, ensure_ascii=False).encode()
    return data if size is None else data + b" " * (size - len(data))


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
    from test_v2_media import _media_instance, _seed, _settle

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
