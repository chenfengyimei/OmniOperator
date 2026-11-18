"""Malformed worker error identities are protocol uncertainty, never coerced business outcomes."""

from __future__ import annotations

import asyncio
import json
import sys
from textwrap import dedent

import pytest
from omni_test_support.v2.media_transport import Worker, processor_with

from omni.adapters.media import MediaProcessor
from omni.domain.contracts import BusinessError

BAD_CODES = {
    "missing": None,
    "null": None,
    "boolean": False,
    "integer": 7,
    "float": 1.5,
    "array": ["media_audio_missing"],
    "object": {"code": "media_audio_missing"},
    "empty": "",
    "whitespace": " ",
    "newline": "media_audio_missing\n",
    "uppercase": "Media_Error",
    "punctuation": "media.error",
    "unicode": "错误",
    "overlong": "x" * 81,
}


def error_reply(worker, case):
    result = {"ok": False}
    if case != "missing":
        result["code"] = BAD_CODES[case]
    return json.dumps({"version": 1, "id": worker.frames[-1]["id"], "result": result}).encode() + b"\n"


@pytest.mark.parametrize("case", BAD_CODES)
def test_malformed_error_code_is_unknown_and_reclaimed_before_explicit_next_request(case):
    async def scenario():
        old = Worker()
        processor, fresh, spawns = processor_with(old)

        async def readline():
            return error_reply(old, case)

        old.readline = readline
        try:
            with pytest.raises(BusinessError) as caught:
                await processor._request("probe", {}, 20)
            assert caught.value.code == "media_processing_failed" and isinstance(caught.value.__cause__, ValueError)
            assert old.reclaimed and old.terminate_calls == 1 and processor._owned is None
            assert len(old.frames) == 1 and not spawns
            assert (await processor._request("probe", {}, 20))["worker"] == "fresh"
            assert processor._owned is fresh and len(spawns) == 1
        finally:
            assert await processor.close()

    asyncio.run(scenario())


@pytest.mark.parametrize(
    "code",
    [
        "media_component_missing",
        "media_asr_model_missing",
        "media_processing_error",
        "media_audio_duration_out_of_range",
        "fixture_error",
        "x" * 80,
    ],
)
def test_valid_error_identity_preserves_precise_business_failure_and_live_worker(code):
    async def scenario():
        old = Worker()
        processor, _, spawns = processor_with(old)

        async def readline():
            return (
                json.dumps({"version": 1, "id": old.frames[-1]["id"], "result": {"ok": False, "code": code}}).encode()
                + b"\n"
            )

        old.readline = readline
        try:
            for _ in range(2):
                with pytest.raises(BusinessError) as caught:
                    await processor._request("probe", {}, 20)
                assert caught.value.code == code
            assert processor._owned is old and not old.terminate_calls and not spawns
            assert len(old.frames) == 2
        finally:
            assert await processor.close()

    asyncio.run(scenario())


def test_failed_reclamation_keeps_owner_and_blocks_any_replay_or_replacement():
    async def scenario():
        old = Worker(reclaim=False)
        processor, fresh, spawns = processor_with(old)

        async def readline():
            return error_reply(old, "object")

        old.readline = readline
        try:
            with pytest.raises(BusinessError) as caught:
                await processor._request("probe", {}, 20)
            assert caught.value.code == "media_processing_failed"
            assert processor._owned is old and processor.status()["cleanup_pending"]
            assert not processor.status()["available"] and not old.reclaimed
            with pytest.raises(BusinessError) as caught:
                await processor._request("probe", {}, 20)
            assert caught.value.code == "media_cleanup_pending" and len(old.frames) == 1 and not spawns
            old.reclaim = True
            assert (await processor._request("probe", {}, 20))["worker"] == "fresh"
            assert old.reclaimed and processor._owned is fresh and len(spawns) == 1
        finally:
            old.reclaim = True
            assert await processor.close()

    asyncio.run(scenario())


@pytest.mark.parametrize("case", ["missing", "object"])
def test_real_stdio_database_bad_error_is_unknown_without_artifact_and_new_work_uses_fresh_process(
    tmp_path, monkeypatch, case
):
    from omni_test_support.v2.media import BV, _media_instance, _seed, _settle

    script, marker = tmp_path / "error_worker.py", tmp_path / "first-worker"
    script.write_text(
        dedent("""\
            import json, os, sys
            from pathlib import Path
            marker, case = Path(sys.argv[1]), sys.argv[2]
            first = not marker.exists()
            for line in sys.stdin:
                request = json.loads(line)
                if first:
                    marker.write_text(str(os.getpid()), encoding='utf-8')
                    result = {'ok': False}
                    if case == 'object':
                        result['code'] = {'unsafe': 'fixture-provider-canary'}
                else:
                    payload = {'version':1,'kind':'asr','language':'zh','segments':[[0.0,1.0,'owned transcript']]}
                    Path(request['output']).write_text(json.dumps(payload), encoding='utf-8')
                    result = {'ok':True}
                print(json.dumps({'version':1,'id':request['id'],'result':result}), flush=True)
        """),
        encoding="utf-8",
    )

    async def scenario():
        processor = MediaProcessor([sys.executable, str(script), str(marker), case])
        owners, spawn = [], processor._spawn

        async def observe_spawn():
            owned = await spawn()
            owners.append(owned)
            return owned

        monkeypatch.setattr(processor, "_spawn", observe_spawn)
        instance = await _media_instance(tmp_path / "instance", processor=processor)
        published, publish = [], instance.media._objects.publish

        async def observe_publish(*args, **kwargs):
            published.append(args)
            return await publish(*args, **kwargs)

        monkeypatch.setattr(instance.media._objects, "publish", observe_publish)
        try:
            agent, config = await _seed(instance)
            source = await instance.media.register(agent, str(config["id"]), BV)
            bad = await instance.media.submit(agent, str(source["id"]), "transcribe", {})
            row = await _settle(instance, agent, bad["id"])
            await asyncio.gather(*tuple(instance.media._tasks))
            assert row["state"] == "unknown" and row["error_code"] == "media_processing_failed"
            assert not published and not (await instance.media.source(agent, str(source["id"])))["latest_transcript_id"]
            assert not (instance.media._staging / str(bad["id"])).exists()
            assert len(owners) == 1 and owners[0].reclaimed and owners[0].process.returncode is not None
            assert processor._owned is None and not processor.status()["cleanup_pending"]
            fresh = await instance.media.submit(agent, str(source["id"]), "transcribe", {})
            assert (await _settle(instance, agent, fresh["id"]))["state"] == "done"
            assert len(owners) == 2 and owners[1] is not owners[0] and owners[1].process is not owners[0].process
            assert len(published) == 1
            assert (await instance.media.job(agent, str(bad["id"])))["state"] == "unknown"
        finally:
            assert (await instance.close())["complete"]
            assert all(owner.reclaimed for owner in owners)

    asyncio.run(scenario())
