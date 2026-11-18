"""DASH selection uses usable complete candidates and reports malformed metadata explicitly."""

from __future__ import annotations

import asyncio

import pytest
from omni_test_support.v2.bilibili_gateway import CONFIG
from omni_test_support.v2.bilibili_response_bounds import BV, SOURCE
from omni_test_support.v2.bilibili_streams import AUDIO, FULL, MERGED, PARTS, VIDEO, fixture

from omni.domain.contracts import BusinessError


def manifests(kind, tracks):
    return {"dash": {"audio": [AUDIO], "video": [VIDEO], kind: tracks}}, {"durl": PARTS}


@pytest.mark.parametrize("kind", ["audio", "video"])
@pytest.mark.parametrize(
    "case", ["missing_url", "empty_url", "url_alias", "backup_alias", "defaults", "decimal_strings"]
)
def test_lowest_usable_candidate_and_supported_aliases_preserve_exact_stream(kind, case):
    async def scenario():
        selected = dict(AUDIO if kind == "audio" else VIDEO)
        tracks, expected_bytes = [selected], 16
        if case == "missing_url":
            tracks.insert(0, {"bandwidth": 1, "size": 4})
        elif case == "empty_url":
            tracks.insert(0, {"bandwidth": 1, "baseUrl": "", "backupUrl": []})
        elif case == "url_alias":
            selected["base_url"] = selected.pop("baseUrl")
        elif case == "backup_alias":
            selected["backup_url"] = [selected.pop("baseUrl")]
        elif case == "defaults":
            selected.pop("size")
            selected.pop("bandwidth")
            expected_bytes = 0
        elif case == "decimal_strings":
            selected["bandwidth"], selected["size"] = "64", "16"
        gateway, _, calls = fixture(*manifests(kind, tracks))
        try:
            result = await gateway.read_source(CONFIG, "streams", SOURCE)
            assert result[kind] == {"url": (AUDIO if kind == "audio" else VIDEO)["baseUrl"], "bytes": expected_bytes}
            assert len(calls) == 4 and not gateway._reads
        finally:
            assert await gateway.close()

    asyncio.run(scenario())


@pytest.mark.parametrize("kind", ["audio", "video"])
@pytest.mark.parametrize(
    "case",
    [
        "dash",
        "tracks",
        "record",
        "bandwidth_bool",
        "bandwidth_float",
        "bandwidth_negative",
        "bandwidth_text",
        "size_bool",
        "size_negative",
        "url_type",
        "backup_type",
        "backup_record",
    ],
)
def test_known_bad_dash_fields_raise_format_error_without_content_dispatch(kind, case):
    async def scenario():
        track = dict(AUDIO if kind == "audio" else VIDEO)
        play, merged = manifests(kind, [track])
        if case == "dash":
            play["dash"] = "changed"
        elif case == "tracks":
            play["dash"][kind] = "changed"
        elif case == "record":
            play["dash"][kind] = [track, None]
        elif case.startswith("bandwidth_"):
            track["bandwidth"] = {"bool": True, "float": 1.5, "negative": -1, "text": "changed"}[case.split("_")[1]]
        elif case.startswith("size_"):
            track["size"] = True if case == "size_bool" else -1
        elif case == "url_type":
            track["baseUrl"] = 123
        else:
            track.pop("baseUrl")
            track["backupUrl"] = "changed" if case == "backup_type" else [123]
        gateway, _, calls = fixture(play, merged)
        try:
            with pytest.raises(BusinessError) as caught:
                await gateway.read_source(CONFIG, "streams", SOURCE)
            assert caught.value.code == "bilibili_response_invalid" and caught.value.status == 502
            assert all(request.url.host == "api.bilibili.com" for request in calls) and not gateway._reads
        finally:
            assert await gateway.close()

    asyncio.run(scenario())


@pytest.mark.parametrize("kind", ["audio", "video"])
def test_selected_url_policy_is_preserved(kind):
    async def scenario():
        gateway, _, _ = fixture(*manifests(kind, [{"baseUrl": "https://unapproved.invalid/media", "bandwidth": 1}]))
        try:
            with pytest.raises(BusinessError) as caught:
                await gateway.read_source(CONFIG, "streams", SOURCE)
            assert caught.value.code == "bilibili_read_rejected"
        finally:
            assert await gateway.close()

    asyncio.run(scenario())


@pytest.mark.parametrize("operation", ["transcribe", "frames"])
def test_real_job_rejects_bad_dash_before_download_and_allows_explicit_new_work(tmp_path, monkeypatch, operation):
    from omni_test_support.v2.media import _media_instance, _seed, _settle

    async def scenario():
        kind = "audio" if operation == "transcribe" else "video"
        track = dict(AUDIO if kind == "audio" else VIDEO)
        track["size" if kind == "audio" else "bandwidth"] = True if kind == "audio" else "changed"
        gateway, state, calls = fixture(*manifests(kind, [track]))
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
def test_real_job_downloads_next_usable_dash_candidate_without_relabeling_source(tmp_path, monkeypatch, operation):
    from omni_test_support.v2.media import _media_instance, _seed, _settle

    async def scenario():
        kind = "audio" if operation == "transcribe" else "video"
        track = AUDIO if kind == "audio" else VIDEO
        gateway, _, calls = fixture(*manifests(kind, [{"bandwidth": 1}, track]))
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
            assert len(downloads) == 1 and str(downloads[0].url) == track["baseUrl"]
            current = await instance.media.source(agent, str(source["id"]))
            assert current["aid"] == source["aid"] and current["cid"] == source["cid"]
        finally:
            assert (await instance.close())["complete"]

    asyncio.run(scenario())
