"""Media download resumes only contiguous validated bytes and retires all read ownership."""

from __future__ import annotations

import asyncio
from pathlib import Path

import httpx
import pytest
from omni_test_support.v2.bilibili_gateway import CONFIG, Secrets

from omni.adapters.bilibili import BilibiliGateway
from omni.adapters.diagnostics import Redactor
from omni.domain.contracts import BusinessError

URL = "https://cdn.bilivideo.com/owned.m4s"


class Body(httpx.AsyncByteStream):
    def __init__(self, content, gate=None):
        self.content, self.gate, self.reads, self.closed = content, gate, 0, False

    async def __aiter__(self):
        self.reads += 1
        if self.gate is not None:
            entered, release = self.gate
            entered.set()
            await release.wait()
        yield self.content

    async def aclose(self):
        self.closed = True


def fixture(responses):
    calls = []

    def handle(request):
        calls.append(request)
        assert len(calls) <= len(responses), "Unexpected download retry"
        return responses[len(calls) - 1]

    async def no_delivery(_):
        raise AssertionError("No write operations")

    gateway = BilibiliGateway(
        Secrets(),
        Redactor(),
        no_delivery,
        client_factory=lambda **options: httpx.AsyncClient(transport=httpx.MockTransport(handle), **options),
    )
    return gateway, calls


@pytest.mark.parametrize(
    "headers",
    [
        {},
        {"Content-Range": "bytes 0-2/6"},
        {"Content-Range": "bytes 4-5/6"},
        {"Content-Range": "bytes 3-2/6"},
        {"Content-Range": "bytes 3-6/6"},
        {"Content-Range": "bytes 3-5/*"},
        {"Content-Range": "items 3-5/6"},
        {"Content-Range": "bytes 3-5/6", "Content-Length": "2"},
        {"Content-Range": "bytes 3-5/6", "Content-Type": "multipart/byteranges; boundary=fixture"},
        {"Content-Range": "bytes 3-5/6", "Content-Encoding": "gzip"},
        {"Content-Range": "bytes 3-5/6", "Content-Length": "-1"},
    ],
)
def test_bad_partial_response_is_rejected_before_reading_or_appending(tmp_path, headers):
    async def scenario():
        body = Body(b"def")
        gateway, calls = fixture([httpx.Response(206, headers=headers, stream=body)])
        destination = tmp_path / "media.bin"
        destination.write_bytes(b"abc")
        try:
            with pytest.raises(BusinessError) as caught:
                await gateway.download(CONFIG, URL, destination, max_bytes=16, budget_s=180)
            assert caught.value.code == "media_download_failed"
            assert destination.read_bytes() == b"abc" and body.reads == 0 and body.closed
            assert len(calls) == 1 and not gateway._reads
        finally:
            assert await gateway.close()

    asyncio.run(scenario())


@pytest.mark.parametrize("mode", ["full", "resume", "restart", "segmented", "short_full", "short_partial"])
def test_valid_downloads_preserve_exact_bytes_and_complete_all_ranges(tmp_path, mode):
    async def scenario():
        destination = tmp_path / "media.bin"
        if mode in {"resume", "restart"}:
            destination.write_bytes(b"abc")
        responses = {
            "full": [httpx.Response(200, content=b"abcdef")],
            "resume": [httpx.Response(206, headers={"Content-Range": "bytes 3-5/6"}, content=b"def")],
            "restart": [httpx.Response(200, content=b"abcdef")],
            "segmented": [
                httpx.Response(206, headers={"Content-Range": "bytes 0-2/6"}, content=b"abc"),
                httpx.Response(206, headers={"Content-Range": "bytes 3-5/6"}, content=b"def"),
            ],
            "short_full": [
                httpx.Response(200, headers={"Content-Length": "6"}, stream=Body(b"abc")),
                httpx.Response(206, headers={"Content-Range": "bytes 3-5/6"}, content=b"def"),
            ],
            "short_partial": [
                httpx.Response(
                    206, headers={"Content-Length": "6", "Content-Range": "bytes 0-5/6"}, stream=Body(b"abc")
                ),
                httpx.Response(206, headers={"Content-Range": "bytes 3-5/6"}, content=b"def"),
            ],
        }[mode]
        gateway, calls = fixture(responses)
        guards = []

        async def guard():
            guards.append(len(calls))

        try:
            result = await gateway.download(CONFIG, URL, destination, max_bytes=6, budget_s=180, before_dispatch=guard)
            assert destination.read_bytes() == b"abcdef" and result["bytes"] == 6
            assert result["resumed"] is (mode not in {"full", "restart"})
            assert len(guards) == len(calls) == len(responses)
            assert all(call.headers["accept-encoding"] == "identity" for call in calls)
            if mode in {"resume", "restart"}:
                assert calls[0].headers["range"] == "bytes=3-"
            if len(calls) == 2:
                assert calls[1].headers["range"] == "bytes=3-"
            assert not gateway._reads
        finally:
            assert await gateway.close()

    asyncio.run(scenario())


@pytest.mark.parametrize("change", ["none", "etag", "missing_etag", "size", "restart"])
@pytest.mark.parametrize("first_status", [200, 206])
def test_resume_uses_strong_validator_and_refuses_changed_partial_representation(tmp_path, change, first_status):
    async def scenario():
        first = httpx.Response(
            first_status,
            headers={"Content-Length": "6", "ETag": '"v1"'}
            if first_status == 200
            else {"Content-Range": "bytes 0-2/6", "ETag": '"v1"'},
            stream=Body(b"abc"),
        )
        headers = {"Content-Range": "bytes 3-5/6", "ETag": '"v1"'}
        if change == "etag":
            headers["ETag"] = '"v2"'
        elif change == "missing_etag":
            headers.pop("ETag")
        elif change == "size":
            headers["Content-Range"] = "bytes 3-5/7"
        second = (
            httpx.Response(206, headers=headers, content=b"def")
            if change != "restart"
            else httpx.Response(200, headers={"ETag": '"v2"'}, content=b"new")
        )
        gateway, calls = fixture([first, second])
        destination = tmp_path / "media.bin"
        try:
            if change in {"etag", "missing_etag", "size"}:
                with pytest.raises(BusinessError) as caught:
                    await gateway.download(CONFIG, URL, destination, max_bytes=16, budget_s=180)
                assert caught.value.code == "media_download_failed" and destination.read_bytes() == b"abc"
            else:
                result = await gateway.download(CONFIG, URL, destination, max_bytes=16, budget_s=180)
                assert destination.read_bytes() == (b"new" if change == "restart" else b"abcdef")
                assert result["bytes"] == (3 if change == "restart" else 6)
            assert calls[1].headers["if-range"] == '"v1"' and not gateway._reads
        finally:
            assert await gateway.close()

    asyncio.run(scenario())


@pytest.mark.parametrize("tag", ['W/"v1"', "invalid", '"space tag"', '"tab\ttag"'])
def test_weak_or_invalid_etag_is_not_sent_as_if_range(tmp_path, tag):
    async def scenario():
        gateway, calls = fixture(
            [
                httpx.Response(200, headers={"Content-Length": "6", "ETag": tag}, stream=Body(b"abc")),
                httpx.Response(206, headers={"Content-Range": "bytes 3-5/6"}, content=b"def"),
            ]
        )
        destination = tmp_path / "media.bin"
        try:
            assert (await gateway.download(CONFIG, URL, destination, max_bytes=6, budget_s=180))["bytes"] == 6
            assert destination.read_bytes() == b"abcdef" and "if-range" not in calls[1].headers
        finally:
            assert await gateway.close()

    asyncio.run(scenario())


def test_existing_partial_over_budget_is_rejected_without_http_or_truncation(tmp_path):
    async def scenario():
        gateway, calls = fixture([])
        destination = tmp_path / "media.bin"
        destination.write_bytes(b"oversized")
        try:
            with pytest.raises(BusinessError) as caught:
                await gateway.download(CONFIG, URL, destination, max_bytes=3, budget_s=180)
            assert caught.value.code == "media_download_too_large"
            assert destination.read_bytes() == b"oversized" and not calls and not gateway._reads
        finally:
            assert await gateway.close()

    asyncio.run(scenario())


@pytest.mark.parametrize("declared", [False, True])
def test_oversized_chunk_is_not_written_to_disk(tmp_path, declared):
    async def scenario():
        content = b"x" * ((1 << 20) + 8)
        body = Body(content)
        gateway, _ = fixture(
            [httpx.Response(200, headers={"Content-Length": str(len(content))} if declared else {}, stream=body)]
        )
        destination = tmp_path / "media.bin"
        try:
            with pytest.raises(BusinessError) as caught:
                await gateway.download(CONFIG, URL, destination, max_bytes=(1 << 20) + 4, budget_s=180)
            assert caught.value.code == "media_download_too_large"
            assert (destination.stat().st_size if destination.exists() else 0) == (0 if declared else 1 << 20)
            assert body.closed and not gateway._reads
        finally:
            assert await gateway.close()

    asyncio.run(scenario())


def test_bad_declared_body_length_never_reports_success(tmp_path):
    async def scenario():
        gateway, _ = fixture([httpx.Response(200, headers={"Content-Length": "3"}, stream=Body(b"abcdef"))])
        destination = tmp_path / "media.bin"
        try:
            with pytest.raises(BusinessError) as caught:
                await gateway.download(CONFIG, URL, destination, max_bytes=16, budget_s=180)
            assert caught.value.code == "media_download_failed" and destination.stat().st_size == 0
        finally:
            assert await gateway.close()

    asyncio.run(scenario())


def test_three_incomplete_ranges_exhaust_original_retry_budget_without_success(tmp_path):
    async def scenario():
        responses = [
            httpx.Response(206, headers={"Content-Range": f"bytes {index}-{index}/4"}, content=b"x")
            for index in range(3)
        ]
        gateway, calls = fixture(responses)
        destination = tmp_path / "media.bin"
        try:
            with pytest.raises(BusinessError) as caught:
                await gateway.download(CONFIG, URL, destination, max_bytes=4, budget_s=180)
            assert caught.value.code == "media_download_failed" and len(calls) == 3
            assert destination.read_bytes() == b"xxx" and not gateway._reads
        finally:
            assert await gateway.close()

    asyncio.run(scenario())


def test_failed_directory_creation_releases_download_ownership(tmp_path, monkeypatch):
    async def scenario():
        gateway, calls = fixture([])
        destination = tmp_path / "missing" / "media.bin"
        original = Path.mkdir

        def fail(path, *args, **kwargs):
            if path == destination.parent:
                raise PermissionError("injected mkdir failure")
            return original(path, *args, **kwargs)

        try:
            with monkeypatch.context() as patch:
                patch.setattr(Path, "mkdir", fail)
                with pytest.raises(BusinessError) as caught:
                    await gateway.download(CONFIG, URL, destination, max_bytes=16, budget_s=180)
            assert caught.value.code == "media_download_failed" and not calls and not gateway._reads
        finally:
            assert await gateway.close(0)

    asyncio.run(scenario())


def test_cancelled_download_closes_response_and_retires_read_owner(tmp_path):
    async def scenario():
        entered, release = asyncio.Event(), asyncio.Event()
        body = Body(b"abc", (entered, release))
        gateway, _ = fixture([httpx.Response(200, stream=body)])
        caller = asyncio.create_task(gateway.download(CONFIG, URL, tmp_path / "media.bin", max_bytes=3, budget_s=180))
        try:
            await asyncio.wait_for(entered.wait(), 8)
            assert not await gateway.close(0)
            caller.cancel()
            with pytest.raises(asyncio.CancelledError):
                await caller
            assert body.closed and not gateway._reads
        finally:
            release.set()
            await asyncio.gather(caller, return_exceptions=True)
            assert await gateway.close()

    asyncio.run(scenario())


def test_real_media_job_rejects_bad_range_before_processing_and_allows_explicit_new_work(tmp_path):
    from omni_test_support.v2.media import BV, FakeGateway, FakeProcessor, _media_instance, _seed, _settle

    async def scenario():
        transport, calls = fixture(
            [
                httpx.Response(206, headers={"Content-Range": "bytes 1-2/3"}, content=b"bc"),
                httpx.Response(200, content=b"abc"),
            ]
        )
        gateway, processor = FakeGateway(), FakeProcessor()
        original_read = gateway.read

        async def read(*args, **kwargs):
            result = await original_read(*args, **kwargs)
            if args[1] == "streams":
                result["audio"]["url"] = URL
            return result

        gateway.read, gateway.download = read, transport.download
        instance = await _media_instance(tmp_path / "instance", gateway, processor)
        try:
            agent, config = await _seed(instance)
            source = await instance.media.register(agent, str(config["id"]), BV)
            bad = await instance.media.submit(agent, str(source["id"]), "transcribe", {})
            row = await _settle(instance, agent, bad["id"], wait_for_cleanup=True)
            assert row["state"] == "failed" and row["error_code"] == "media_download_failed"
            assert not processor.calls and len(calls) == 1
            assert not (await instance.media.source(agent, str(source["id"])))["latest_transcript_id"]
            assert not (instance.media._staging / str(bad["id"])).exists()
            fresh = await instance.media.submit(agent, str(source["id"]), "transcribe", {})
            assert (await _settle(instance, agent, fresh["id"]))["state"] == "done"
            assert len(processor.calls) == 1 and len(calls) == 2
        finally:
            assert (await instance.close())["complete"]
            assert await transport.close()

    asyncio.run(scenario())
