"""Media IPC rejects incomplete or ambiguous frames and budgets the whole exchange."""

from __future__ import annotations

import asyncio
import json
import sys
from textwrap import dedent

import pytest
from test_v2_media_transport import Worker, processor_with

from omni.adapters import media
from omni.domain.contracts import BusinessError


def reply_bytes(worker, case):
    packet = {"version": 1, "id": worker.frames[-1]["id"], "result": {"ok": True, "worker": worker.name}}
    if case == "missing_version":
        packet.pop("version")
    elif case in {"wrong_version", "boolean_version", "float_version"}:
        packet["version"] = {"wrong_version": 2, "boolean_version": True, "float_version": 1.0}[case]
    elif case == "missing_ok":
        packet["result"].pop("ok")
    elif case in {"string_ok", "integer_ok", "null_ok"}:
        packet["result"]["ok"] = {"string_ok": "yes", "integer_ok": 1, "null_ok": None}[case]
    elif case in {"nan", "infinity", "negative_infinity"}:
        packet["result"]["metric"] = {
            "nan": float("nan"),
            "infinity": float("inf"),
            "negative_infinity": -float("inf"),
        }[case]
    elif case == "wrong_id":
        packet["id"] = "another-request"
    elif case == "result_array":
        packet["result"] = []
    elif case == "root_array":
        return b"[]\n"
    elif case == "invalid_json":
        return b"{bad}\n"
    elif case == "invalid_utf8":
        return b"\xff\n"
    elif case == "empty":
        return b""
    elif case == "deep_json":
        return b"[" * 2000 + b"0" + b"]" * 2000 + b"\n"
    elif case == "overflow_float":
        packet["result"]["metric"] = 1.25
    body = json.dumps(packet, ensure_ascii=False).encode()
    if case == "duplicate_id":
        body = body[:-1] + b',"id":' + json.dumps(packet["id"]).encode() + b"}"
    elif case == "duplicate_ok":
        body = body.replace(b'"ok": true', b'"ok": false, "ok": true')
    elif case == "duplicate_nested":
        body = body[:-1] + b',"extra":{"x":1,"x":2}}'
    elif case == "no_newline":
        return body
    elif case == "overflow_float":
        body = body.replace(b"1.25", b"1e999")
    if case in {"exact_limit", "over_limit"}:
        body += b" " * (media.FRAME_LIMIT - len(body) - (case == "exact_limit"))
    return body + b"\n"


@pytest.mark.parametrize(
    "case",
    [
        "missing_version",
        "wrong_version",
        "boolean_version",
        "float_version",
        "missing_ok",
        "string_ok",
        "integer_ok",
        "null_ok",
        "nan",
        "infinity",
        "negative_infinity",
        "overflow_float",
        "deep_json",
        "duplicate_id",
        "duplicate_ok",
        "duplicate_nested",
        "no_newline",
        "over_limit",
        "wrong_id",
        "result_array",
        "root_array",
        "invalid_json",
        "invalid_utf8",
        "empty",
    ],
)
def test_bad_reply_is_uncertain_and_reclaims_worker_before_next_request(monkeypatch, case):
    async def scenario():
        old = Worker()
        processor, fresh, spawns = processor_with(old)

        async def readline():
            return reply_bytes(old, case)

        old.readline = readline
        monkeypatch.setattr(media, "FRAME_LIMIT", 16384 if case == "deep_json" else 512)
        try:
            with pytest.raises(BusinessError) as caught:
                await processor._request("probe", {}, 20)
            assert caught.value.code == "media_processing_failed"
            assert old.reclaimed and processor._owned is None and old.terminate_calls == 1
            assert len(old.frames) == 1 and not spawns
            assert (await processor._request("probe", {}, 20))["worker"] == "fresh"
            assert processor._owned is fresh and len(spawns) == 1
        finally:
            assert await processor.close()

    asyncio.run(scenario())


@pytest.mark.parametrize("newline", [False, True])
def test_streamreader_limit_error_is_reclaimed_as_protocol_failure(monkeypatch, newline):
    async def scenario():
        old = Worker()
        processor, _, _ = processor_with(old)
        reader = asyncio.StreamReader(limit=513)
        reader.feed_data(b"x" * 1024 + (b"\n" if newline else b""))
        reader.feed_eof()
        old.process.stdout = reader
        monkeypatch.setattr(media, "FRAME_LIMIT", 512)
        try:
            with pytest.raises(BusinessError) as caught:
                await processor._request("probe", {}, 20)
            assert caught.value.code == "media_processing_failed" and isinstance(caught.value.__cause__, ValueError)
            assert old.reclaimed and processor._owned is None
        finally:
            assert await processor.close()

    asyncio.run(scenario())


@pytest.mark.parametrize(
    "code", [None, "media_component_missing", "media_asr_model_missing", "media_audio_duration_out_of_range"]
)
def test_exact_frame_limit_and_precise_worker_outcomes_preserve_live_worker(monkeypatch, code):
    async def scenario():
        old = Worker()
        processor, _, spawns = processor_with(old)

        async def readline():
            if code is None:
                value = reply_bytes(old, "exact_limit")
                assert len(value) == 512
                return value
            return (
                json.dumps({"version": 1, "id": old.frames[-1]["id"], "result": {"ok": False, "code": code}}).encode()
                + b"\n"
            )

        old.readline = readline
        monkeypatch.setattr(media, "FRAME_LIMIT", 512)
        try:
            for _ in range(2):
                if code is None:
                    assert (await processor._request("probe", {}, 20))["ok"] is True
                else:
                    with pytest.raises(BusinessError) as caught:
                        await processor._request("probe", {}, 20)
                    assert caught.value.code == code
            assert processor._owned is old and not old.terminate_calls and not spawns
        finally:
            assert await processor.close()

    asyncio.run(scenario())


def test_processing_deadline_includes_drain_without_restarting_read_budget(monkeypatch):
    async def scenario():
        old = Worker()
        processor, _, spawns = processor_with(old)
        loop = asyncio.get_running_loop()
        original_time, offset = loop.time, [0.0]
        monkeypatch.setattr(loop, "time", lambda: original_time() + offset[0])

        async def drain():
            # Advance this loop's injected clock; no production timeout is shortened.
            offset[0] += 38
            await asyncio.sleep(0)

        async def readline():
            await asyncio.sleep(0)
            return reply_bytes(old, "valid")

        old.drain, old.readline = drain, readline
        try:
            with pytest.raises(BusinessError) as caught:
                await processor._request("probe", {}, 37)
            assert caught.value.code == "media_processing_timeout" and old.reclaimed
            assert len(old.frames) == 1 and not spawns
        finally:
            monkeypatch.setattr(loop, "time", original_time)
            assert await processor.close()

    asyncio.run(scenario())


@pytest.mark.parametrize("case", ["overlong", "partial", "wrong_version"])
def test_real_stdio_bad_frame_reclaims_group_and_next_worker_is_fresh(tmp_path, monkeypatch, case):
    script, marker = tmp_path / "owned_protocol_worker.py", tmp_path / "first-worker.json"
    script.write_text(
        dedent("""\
            import json, os, sys
            from pathlib import Path
            marker, case = Path(sys.argv[1]), sys.argv[2]
            first = not marker.exists()
            for line in sys.stdin:
                request = json.loads(line)
                packet = {'version':1, 'id':request['id'], 'result':{'ok':True, 'pid':os.getpid()}}
                if first:
                    marker.write_text(json.dumps({'pid':os.getpid()}), encoding='utf-8')
                    first = False
                    if case == 'overlong':
                        packet['result']['padding'] = 'x' * 1024
                    elif case == 'wrong_version':
                        packet['version'] = 2
                    body = json.dumps(packet)
                    if case == 'partial':
                        sys.stdout.write(body)
                        sys.stdout.flush()
                        break
                    print(body, flush=True)
                else:
                    print(json.dumps(packet), flush=True)
            """),
        encoding="utf-8",
    )
    monkeypatch.setattr(media, "FRAME_LIMIT", 512)

    async def scenario():
        processor = media.MediaProcessor([sys.executable, str(script), str(marker), case])
        try:
            with pytest.raises(BusinessError) as caught:
                await processor._request("probe", {}, 20)
            assert caught.value.code == "media_processing_failed"
            assert processor._owned is None and not processor.status()["cleanup_pending"]
            old_worker_pid = json.loads(marker.read_text(encoding="utf-8"))["pid"]
            result = await processor._request("probe", {}, 20)
            assert result["pid"] != old_worker_pid and processor.status()["running"]
        finally:
            assert await processor.close(10)
        assert not processor.status()["running"]

    asyncio.run(scenario())
