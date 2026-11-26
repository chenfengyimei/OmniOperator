"""Transcript JSON rejects ambiguous values before publication or HTTP serialization."""

from __future__ import annotations

import asyncio
import json

import pytest

from omni.domain import media
from omni.domain.contracts import BusinessError

INVALID = [
    "boolean_version",
    "float_version",
    "duplicate_version",
    "duplicate_kind",
    "duplicate_language",
    "duplicate_segments",
    "escaped_duplicate",
    "nan",
    "infinity",
    "overflow_float",
    "deep_json",
    "huge_start",
    "huge_end",
    "long_language",
    "surrogate_language",
    "surrogate_text",
    "boolean_subtitle_start",
    "boolean_subtitle_end",
    "utf16",
    "utf32",
    "invalid_utf8",
    "extra_field",
]


def payload(case="normal"):
    value = {"version": 1, "kind": "asr", "language": "zh", "segments": [[0.0, 1.0, "PRIVATE_TRANSCRIPT_TEXT"]]}
    if case == "boolean_version":
        value["version"] = True
    elif case == "float_version":
        value["version"] = 1.0
    elif case in {"huge_start", "huge_end"}:
        value["segments"][0][case == "huge_end"] = 10**400
    elif case == "long_language":
        value["language"] = "z" * 17
    elif case == "surrogate_language":
        value["language"] = "\ud800"
    elif case == "surrogate_text":
        value["segments"][0][2] = "\ud800"
    elif case in {"boolean_subtitle_start", "boolean_subtitle_end"}:
        value["kind"] = "subtitle"
        value["segments"] = [[0.0, 0.0, "PRIVATE_TRANSCRIPT_TEXT"]]
        value["segments"][0][case == "boolean_subtitle_end"] = False
    elif case == "extra_field":
        value["unexpected"] = "PRIVATE_TRANSCRIPT_TEXT"
    body = json.dumps(value, separators=(",", ":")).encode()
    if case.startswith("duplicate_"):
        field = case.removeprefix("duplicate_")
        body = body[:-1] + b"," + json.dumps(field).encode() + b":" + json.dumps(value[field]).encode() + b"}"
    elif case == "escaped_duplicate":
        body = body[:-1] + b',"\\u0076ersion":1}'
    elif case in {"nan", "infinity", "overflow_float"}:
        body = body.replace(b"0.0", {"nan": b"NaN", "infinity": b"Infinity", "overflow_float": b"1e999"}[case], 1)
    elif case == "deep_json":
        body = b"[" * 2000 + b"0" + b"]" * 2000
    elif case in {"utf16", "utf32"}:
        body = body.decode().encode({"utf16": "utf-16", "utf32": "utf-32"}[case])
    elif case == "invalid_utf8":
        body = body.replace(b"PRIVATE_TRANSCRIPT_TEXT", b"\xff")
    return body


@pytest.mark.parametrize("case", INVALID)
def test_invalid_transcript_is_a_public_bounded_error(case):
    with pytest.raises(BusinessError) as error:
        media.parse_transcript(payload(case))
    assert error.value.status == 422
    assert error.value.code in {"media_transcript_unreadable", "media_transcript_invalid"}
    assert "PRIVATE_TRANSCRIPT_TEXT" not in error.value.message


@pytest.mark.parametrize(
    "case",
    ["asr", "subtitle", "integer_zero", "language_boundary", "text_boundary", "segment_boundary", "byte_boundary"],
)
def test_valid_transcripts_keep_existing_rows_and_boundaries(case):
    kind, language, rows = "asr", "zh", [[0.0, 1.0, "你好，world"]]
    if case in {"subtitle", "integer_zero"}:
        kind, rows = "subtitle", [[0 if case == "integer_zero" else 0.0, 0.0, "真实字幕"]]
    elif case == "language_boundary":
        language = "中" * 16
    elif case == "text_boundary":
        rows = [[0.0, 1.0, "字" * media.MAX_TRANSCRIPT_CHARS]]
    elif case == "segment_boundary":
        rows = [[0.0, 0.0, "字"] for _ in range(media.MAX_SEGMENTS)]
    body = media.transcript_payload(kind, language, rows)
    if case == "byte_boundary":
        body += b" " * (media.MAX_TRANSCRIPT_BYTES - len(body))
    result = media.parse_transcript(body)
    assert result == {"version": 1, "kind": kind, "language": language, "segments": rows}


def test_oversized_transcript_is_rejected_before_json_decode(monkeypatch):
    body = payload()
    body += b" " * (media.MAX_TRANSCRIPT_BYTES + 1 - len(body))
    calls, original = [], json.loads

    def decode(*args, **kwargs):
        calls.append(True)
        return original(*args, **kwargs)

    with monkeypatch.context() as patch:
        patch.setattr(json, "loads", decode)
        with pytest.raises(BusinessError) as error:
            media.parse_transcript(body)
    assert error.value.code == "media_transcript_unreadable" and not calls


@pytest.mark.parametrize("timestamp", [10**400, float("nan"), float("inf")], ids=["huge_integer", "nan", "infinity"])
def test_frame_timestamp_numeric_failure_stays_a_business_error(timestamp):
    with pytest.raises(BusinessError) as error:
        media.sheet_result(1, 320, 180, [timestamp], "0" * 64, 1)
    assert error.value.code == "media_frames_invalid" and error.value.status == 422


@pytest.mark.parametrize("case", ["duplicate_version", "huge_start"])
def test_real_asr_job_rejects_bad_codec_before_object_publication(tmp_path, monkeypatch, case):
    from omni_test_support.v2.media import BV, FakeProcessor, _media_instance, _seed, _settle

    class Processor(FakeProcessor):
        async def transcribe(self, audio, output, plan):
            output.write_bytes(payload(case))
            return {"ok": True}

    async def scenario():
        instance = await _media_instance(tmp_path, processor=Processor())
        published, original = [], instance.media._objects.publish

        async def publish(*args, **kwargs):
            published.append(True)
            return await original(*args, **kwargs)

        try:
            agent, config = await _seed(instance)
            source = await instance.media.register(agent, str(config["id"]), BV)
            with monkeypatch.context() as patch:
                patch.setattr(instance.media._objects, "publish", publish)
                job = await instance.media.submit(agent, str(source["id"]), "transcribe", {})
                row = await _settle(instance, agent, job["id"], wait_for_cleanup=True)
            assert row["state"] == "failed" and row["error_code"] in {
                "media_transcript_unreadable",
                "media_transcript_invalid",
            }
            assert not published and not (instance.media._staging / str(job["id"])).exists()
            assert not (await instance.media.source(agent, str(source["id"])))["latest_transcript_id"]
            fresh = await instance.media.submit(agent, str(source["id"]), "subtitle", {})
            assert (await _settle(instance, agent, fresh["id"]))["state"] == "done"
        finally:
            assert (await instance.close())["complete"]

    asyncio.run(scenario())


@pytest.mark.parametrize("case", ["duplicate_version", "surrogate_text"])
def test_http_rejects_stored_invalid_transcript_without_serialization_failure(tmp_path, case):
    from fastapi.testclient import TestClient
    from omni_test_support.v2.media import BV, CHANNEL, FakeGateway, FakeProcessor, NoModel, Secrets

    from omni.composition.http import http_app

    app = http_app(
        tmp_path,
        model=NoModel(),
        bilibili_backend=FakeGateway(),
        media_processor=FakeProcessor(),
        credential_backend=Secrets(),
    )
    with TestClient(app) as client:
        client.headers["Authorization"] = "Bearer " + (tmp_path / "run/owner.token").read_text(encoding="utf-8")
        agent = client.get("/api/v1/agents").json()["items"][0]["id"]
        base = f"/api/v1/agents/{agent}"
        config = client.post(
            f"{base}/configurations",
            json={"kind": "channel", "name": "Codec fixture", "enabled": True, "value": CHANNEL},
        ).json()
        source = client.post(f"{base}/media/sources", json={"config_id": config["id"], "target": BV}).json()
        runtime = app.state.runtime

        async def seed(body):
            database = runtime.media._db
            job = await database.write(lambda unit: unit.media_job_create(agent, source["id"], "transcribe", {}))
            await database.write(lambda unit: unit.media_job_start(job["id"]))
            obj = await runtime.objects.publish(agent, body, "application/json")
            await database.write(
                lambda unit: unit.media_job_complete_transcript(
                    job["id"], {"kind": "asr", "language": "zh", "object_id": obj["id"], "bytes": len(body)}
                )
            )
            view = await runtime.media.source(agent, source["id"])
            return job["id"], view["latest_transcript_id"]

        job_id, artifact_id = client.portal.call(seed, payload(case))
        for suffix in ("", "/window"):
            response = client.get(f"{base}/media/transcripts/{artifact_id}{suffix}")
            assert response.status_code == 422 and response.json()["error"]["code"] == "media_transcript_unreadable"
            assert "PRIVATE_TRANSCRIPT_TEXT" not in response.text
        assert client.get(f"{base}/media/jobs/{job_id}").json()["state"] == "done"
        _, good_id = client.portal.call(seed, payload())
        assert client.get(f"{base}/media/transcripts/{good_id}").status_code == 200
