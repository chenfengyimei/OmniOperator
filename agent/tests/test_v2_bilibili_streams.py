"""Segmented manifests cannot silently become a complete media source's first fragment."""

from __future__ import annotations

import asyncio
import copy

import httpx
import pytest
from test_v2_bilibili_gateway import CONFIG, Secrets
from test_v2_bilibili_response_bounds import BV, NAV, SOURCE

from omni.adapters.bilibili import BilibiliGateway
from omni.adapters.diagnostics import Redactor
from omni.domain.contracts import BusinessError

HOST = "https://cdn.bilivideo.com/"
AUDIO = {"baseUrl": HOST + "complete-audio.m4s", "bandwidth": 64, "size": 16}
VIDEO = {"baseUrl": HOST + "complete-video.m4s", "bandwidth": 128, "size": 16}
PARTS = [{"url": HOST + f"fragment-{index}.flv", "size": 4, "order": index} for index in (1, 2)]
MERGED = {"url": HOST + "complete-merged.mp4", "size": 16}
FULL = b"complete fixture"


def fixture(play, merged_play):
    state, calls = {"play": copy.deepcopy(play), "merged": copy.deepcopy(merged_play)}, []

    def handle(request):
        calls.append(request)
        if request.url.host == "cdn.bilivideo.com":
            return httpx.Response(200, content=FULL if request.url.path.startswith("/complete-") else b"part")
        if request.url.path.endswith("/nav"):
            data = NAV
        elif request.url.path.endswith("/view"):
            data = {**SOURCE, "title": "Fixture", "duration": 120}
        elif request.url.path == "/x/player/playurl":
            assert request.url.params["bvid"] == BV and request.url.params["cid"] == SOURCE["cid"]
            data = state["play" if request.url.params["fnval"] == "16" else "merged"]
        else:
            raise AssertionError("Unexpected endpoint")
        return httpx.Response(200, json={"code": 0, "data": data})

    async def no_delivery(_):
        raise AssertionError("No publications")

    gateway = BilibiliGateway(
        Secrets(),
        Redactor(),
        no_delivery,
        client_factory=lambda **options: httpx.AsyncClient(transport=httpx.MockTransport(handle), **options),
    )
    return gateway, state, calls


@pytest.mark.parametrize("entry", ["owner", "source"])
def test_multipart_only_manifests_are_explicitly_unsupported(entry):
    async def scenario():
        gateway, _, calls = fixture({"durl": PARTS}, {"durl": PARTS})
        try:
            with pytest.raises(BusinessError) as caught:
                if entry == "owner":
                    await gateway.read(CONFIG, "streams", BV)
                else:
                    await gateway.read_source(CONFIG, "streams", SOURCE)
            assert caught.value.code == "bilibili_stream_unsupported" and caught.value.status == 422
            assert len(calls) == 4 and all(call.url.host == "api.bilibili.com" for call in calls)
            assert not gateway._reads
        finally:
            assert await gateway.close()

    asyncio.run(scenario())


@pytest.mark.parametrize(
    "case",
    ["single_merged", "dash_fallback", "audio_only", "video_only", "dash_and_single", "lowest_bitrate"],
)
def test_only_complete_candidates_are_selected_and_existing_priority_is_preserved(case):
    async def scenario():
        play, merged = {"durl": [MERGED]}, {"durl": [MERGED]}
        expected = {"audio": {"url": MERGED["url"], "bytes": 16}, "video": {"url": MERGED["url"], "bytes": 16}}
        if case == "dash_fallback":
            play = {"dash": {"audio": [AUDIO], "video": [VIDEO]}, "durl": PARTS}
            merged = {"durl": PARTS}
            expected = {
                "audio": {"url": AUDIO["baseUrl"], "bytes": 16},
                "video": {"url": VIDEO["baseUrl"], "bytes": 16},
            }
        elif case == "audio_only":
            play, merged = {"dash": {"audio": [AUDIO]}}, {"durl": PARTS}
            expected = {"audio": {"url": AUDIO["baseUrl"], "bytes": 16}, "video": None}
        elif case == "video_only":
            play = {"durl": PARTS}
            expected["audio"] = None
        elif case in {"dash_and_single", "lowest_bitrate"}:
            audio = [AUDIO]
            if case == "lowest_bitrate":
                audio = [{**AUDIO, "baseUrl": HOST + "complete-expensive.m4s", "bandwidth": 256}, AUDIO]
            play = {"dash": {"audio": audio, "video": [VIDEO]}, "durl": PARTS}
            expected["audio"] = {"url": AUDIO["baseUrl"], "bytes": 16}
        gateway, _, calls = fixture(play, merged)
        try:
            assert await gateway.read_source(CONFIG, "streams", SOURCE) == expected
            assert len(calls) == 4 and not gateway._reads
        finally:
            assert await gateway.close()

    asyncio.run(scenario())


def test_empty_manifest_keeps_existing_no_stream_error():
    async def scenario():
        gateway, _, _ = fixture({}, {})
        try:
            with pytest.raises(BusinessError) as caught:
                await gateway.read_source(CONFIG, "streams", SOURCE)
            assert caught.value.code == "bilibili_read_rejected" and caught.value.status == 502
        finally:
            assert await gateway.close()

    asyncio.run(scenario())


@pytest.mark.parametrize("operation", ["transcribe", "frames"])
def test_real_jobs_reject_partial_media_before_download_and_allow_explicit_complete_work(
    tmp_path, monkeypatch, operation
):
    from test_v2_media import _media_instance, _seed, _settle

    async def scenario():
        gateway, state, calls = fixture({"durl": PARTS}, {"durl": PARTS})
        instance = await _media_instance(tmp_path, gateway=gateway)
        published, publish = [], instance.media._objects.publish

        async def observe(*args, **kwargs):
            published.append(args)
            return await publish(*args, **kwargs)

        monkeypatch.setattr(instance.media._objects, "publish", observe)
        try:
            agent, config = await _seed(instance)
            source = await instance.media.register(agent, str(config["id"]), BV)
            job = await instance.media.submit(agent, str(source["id"]), operation, {})
            row = await _settle(instance, agent, job["id"])
            assert row["state"] == "failed" and row["error_code"] == "bilibili_stream_unsupported"
            assert not published and not instance.media._processor.calls
            assert all(call.url.host != "cdn.bilivideo.com" for call in calls)
            assert not (instance.media._staging / str(job["id"])).exists()
            current = await instance.media.source(agent, str(source["id"]))
            assert not current["latest_transcript_id"] and not current["latest_frames_id"]
            state.update(play={"durl": [MERGED]}, merged={"durl": [MERGED]})
            fresh = await instance.media.submit(agent, str(source["id"]), operation, {})
            assert (await _settle(instance, agent, fresh["id"]))["state"] == "done"
            assert (await instance.media.job(agent, str(job["id"])))["state"] == "failed"
            assert len([call for call in calls if call.url.host == "cdn.bilivideo.com"]) == 1
            assert len(instance.media._processor.calls) == 1 and len(published) == 1
        finally:
            assert (await instance.close())["complete"]

    asyncio.run(scenario())


@pytest.mark.parametrize("operation", ["transcribe", "frames"])
def test_real_jobs_use_full_dash_candidates_when_merged_manifest_is_segmented(tmp_path, monkeypatch, operation):
    from test_v2_media import _media_instance, _seed, _settle

    async def scenario():
        gateway, _, calls = fixture({"dash": {"audio": [AUDIO], "video": [VIDEO]}}, {"durl": PARTS})
        instance = await _media_instance(tmp_path, gateway=gateway)
        processor, inputs = instance.media._processor, []
        name = "transcribe" if operation == "transcribe" else "frames"
        process = getattr(processor, name)

        async def observe(path, *args, **kwargs):
            inputs.append(path.read_bytes())
            return await process(path, *args, **kwargs)

        monkeypatch.setattr(processor, name, observe)
        try:
            agent, config = await _seed(instance)
            source = await instance.media.register(agent, str(config["id"]), BV)
            job = await instance.media.submit(agent, str(source["id"]), operation, {})
            assert (await _settle(instance, agent, job["id"]))["state"] == "done"
            assert inputs == [FULL]
            downloads = [call for call in calls if call.url.host == "cdn.bilivideo.com"]
            assert len(downloads) == 1
            assert str(downloads[0].url) == (AUDIO if operation == "transcribe" else VIDEO)["baseUrl"]
            assert not (instance.media._staging / str(job["id"])).exists()
        finally:
            assert (await instance.close())["complete"]

    asyncio.run(scenario())
