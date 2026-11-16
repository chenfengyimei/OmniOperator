"""Single-part merged streams use verified backup URLs and reject malformed selected metadata."""

from __future__ import annotations

import asyncio

import pytest
from test_v2_bilibili_gateway import CONFIG
from test_v2_bilibili_response_bounds import BV, SOURCE
from test_v2_bilibili_streams import AUDIO, FULL, MERGED, VIDEO, fixture

from omni.domain.contracts import BusinessError


def manifests(kind, parts):
    play, merged = {"durl": [MERGED]}, {"durl": [MERGED]}
    if kind == "audio":
        play["durl"] = parts
    else:
        play, merged = {"dash": {"audio": [AUDIO], "video": [VIDEO]}}, {"durl": parts}
    return play, merged


@pytest.mark.parametrize("kind", ["audio", "video"])
@pytest.mark.parametrize(
    "case", ["primary", "backup_snake", "backup_camel", "empty_backup", "unknown_size", "decimal_size"]
)
def test_single_part_url_aliases_and_size_defaults_preserve_exact_payload(kind, case):
    async def scenario():
        item = dict(MERGED)
        size = 16
        if case in {"backup_snake", "backup_camel", "empty_backup"}:
            item.pop("url")
            item["backupUrl" if case == "backup_camel" else "backup_url"] = [MERGED["url"]]
            if case == "empty_backup":
                item["url"], item["backupUrl"] = "", []
        elif case == "unknown_size":
            item.pop("size")
            size = 0
        elif case == "decimal_size":
            item["size"] = "16"
        gateway, _, calls = fixture(*manifests(kind, [item]))
        try:
            result = await gateway.read_source(CONFIG, "streams", SOURCE)
            assert result[kind] == {"url": MERGED["url"], "bytes": size}
            assert len(calls) == 4 and not gateway._reads
        finally:
            assert await gateway.close()

    asyncio.run(scenario())


@pytest.mark.parametrize("kind", ["audio", "video"])
@pytest.mark.parametrize(
    "case",
    [
        "list_string",
        "list_object",
        "list_bool",
        "record",
        "size_bool",
        "size_float",
        "size_negative",
        "size_text",
        "url_type",
        "backup_type",
        "backup_record",
    ],
)
def test_malformed_merged_fields_are_known_format_failures_before_download(kind, case):
    async def scenario():
        item, parts = dict(MERGED), None
        if case.startswith("list_"):
            parts = {"string": "changed", "object": {}, "bool": False}[case.split("_")[1]]
        elif case == "record":
            parts = [None]
        elif case.startswith("size_"):
            item["size"] = {"bool": True, "float": 1.5, "negative": -1, "text": "changed"}[case.split("_")[1]]
        elif case == "url_type":
            item["url"] = 123
        else:
            item.pop("url")
            item["backup_url"] = "changed" if case == "backup_type" else [123]
        gateway, _, calls = fixture(*manifests(kind, [item] if parts is None else parts))
        try:
            with pytest.raises(BusinessError) as caught:
                await gateway.read_source(CONFIG, "streams", SOURCE)
            assert caught.value.code == "bilibili_response_invalid" and caught.value.status == 502
            assert all(request.url.host == "api.bilibili.com" for request in calls) and not gateway._reads
        finally:
            assert await gateway.close()

    asyncio.run(scenario())


@pytest.mark.parametrize("kind", ["audio", "video"])
def test_backup_url_keeps_existing_cdn_policy(kind):
    async def scenario():
        gateway, _, _ = fixture(*manifests(kind, [{"backup_url": ["https://unapproved.invalid/media"]}]))
        try:
            with pytest.raises(BusinessError) as caught:
                await gateway.read_source(CONFIG, "streams", SOURCE)
            assert caught.value.code == "bilibili_read_rejected"
        finally:
            assert await gateway.close()

    asyncio.run(scenario())


@pytest.mark.parametrize("operation", ["transcribe", "frames"])
def test_real_job_rejects_bad_merged_size_and_allows_explicit_new_work(tmp_path, monkeypatch, operation):
    from test_v2_media import _media_instance, _seed, _settle

    async def scenario():
        kind = "audio" if operation == "transcribe" else "video"
        item = {**MERGED, "size": True if kind == "audio" else "changed"}
        gateway, state, calls = fixture(*manifests(kind, [item]))
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
            assert row["state"] == "failed" and row["error_code"] == "bilibili_response_invalid"
            assert not published and not instance.media._processor.calls
            assert all(call.url.host != "cdn.bilivideo.com" for call in calls)
            assert not (instance.media._staging / str(job["id"])).exists()
            current = await instance.media.source(agent, str(source["id"]))
            assert not current["latest_transcript_id"] and not current["latest_frames_id"]
            state.update(play={"durl": [MERGED]}, merged={"durl": [MERGED]})
            fresh = await instance.media.submit(agent, str(source["id"]), operation, {})
            assert (await _settle(instance, agent, fresh["id"]))["state"] == "done"
            assert (await instance.media.job(agent, str(job["id"])))["state"] == "failed"
        finally:
            assert (await instance.close())["complete"]

    asyncio.run(scenario())


@pytest.mark.parametrize("operation", ["transcribe", "frames"])
def test_real_job_uses_merged_backup_with_full_bytes_and_original_source(tmp_path, monkeypatch, operation):
    from test_v2_media import _media_instance, _seed, _settle

    async def scenario():
        kind = "audio" if operation == "transcribe" else "video"
        gateway, _, calls = fixture(*manifests(kind, [{"backup_url": [MERGED["url"]], "size": 16}]))
        instance = await _media_instance(tmp_path, gateway=gateway)
        processor, inputs = instance.media._processor, []
        method = "transcribe" if operation == "transcribe" else "frames"
        process = getattr(processor, method)

        async def observe(path, *args, **kwargs):
            inputs.append(path.read_bytes())
            return await process(path, *args, **kwargs)

        monkeypatch.setattr(processor, method, observe)
        try:
            agent, config = await _seed(instance)
            source = await instance.media.register(agent, str(config["id"]), BV)
            job = await instance.media.submit(agent, str(source["id"]), operation, {})
            assert (await _settle(instance, agent, job["id"]))["state"] == "done"
            assert inputs == [FULL]
            downloads = [call for call in calls if call.url.host == "cdn.bilivideo.com"]
            assert len(downloads) == 1 and str(downloads[0].url) == MERGED["url"]
            current = await instance.media.source(agent, str(source["id"]))
            assert current["aid"] == source["aid"] and current["cid"] == source["cid"]
            assert not (instance.media._staging / str(job["id"])).exists()
        finally:
            assert (await instance.close())["complete"]

    asyncio.run(scenario())
