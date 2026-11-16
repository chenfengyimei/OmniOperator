"""Worker artifacts have bounded reads before publication and retain exact bytes."""

from __future__ import annotations

import asyncio
import hashlib
import io
from pathlib import Path

import pytest

from omni.application.media import MediaService
from omni.domain.contracts import BusinessError
from omni.domain.media import MAX_SHEET_BYTES, MAX_TRANSCRIPT_BYTES, transcript_payload


class Output:
    def __init__(self, content, *, error=None):
        self.content, self.error, self.reads, self.closed = content, error, [], []

    def open(self, mode):
        assert mode == "rb"
        owner = self

        class Reader(io.BytesIO):
            def read(self, size=-1):
                owner.reads.append(size)
                if owner.error:
                    raise owner.error
                return super().read(size)

            def close(self):
                owner.closed.append(True)
                super().close()

        return Reader(self.content)

    def read_bytes(self):
        with self.open("rb") as stream:
            return stream.read()


class Objects:
    def __init__(self):
        self.calls = []

    async def publish(self, agent_id, content, mime):
        self.calls.append((agent_id, content, mime))
        return {"id": "object", "bytes": len(content)}


class Images:
    def __init__(self):
        self.calls = []

    async def prepare(self, agent_id, object_id):
        self.calls.append((agent_id, object_id))
        return {"state": "registered_not_attached"}


def fixture(tmp_path, content):
    objects, images = Objects(), Images()
    service = MediaService(None, objects, images, None, None, None, tmp_path)
    result = {
        "frame_count": 1,
        "width": 320,
        "height": 180,
        "timestamps": [1.0],
        "sha256": hashlib.sha256(content).hexdigest(),
        "bytes": len(content),
    }
    return service, objects, images, result


@pytest.mark.parametrize("case", ["normal", "extra", "truncated", "empty", "bad_hash"])
def test_sheet_reads_only_declared_size_plus_one_and_preserves_integrity_checks(tmp_path, case):
    async def scenario():
        content = b"owned sheet bytes"
        service, objects, images, result = fixture(tmp_path, content)
        actual = {"extra": content + b"!", "truncated": content[:-1], "empty": b""}.get(case, content)
        output = Output(actual)
        if case == "bad_hash":
            result["sha256"] = "0" * 64
        if case == "normal":
            row = await service._register_sheet("agent", output, result)
            assert row["object_id"] == "object" and row["sha256"] == result["sha256"]
            assert objects.calls == [("agent", content, "image/png")] and images.calls == [("agent", "object")]
        else:
            with pytest.raises(BusinessError) as error:
                await service._register_sheet("agent", output, result)
            assert error.value.code == "media_sheet_invalid" and error.value.status == 503
            assert not objects.calls and not images.calls
        assert output.reads == [len(content) + 1] and output.closed == [True]

    asyncio.run(scenario())


@pytest.mark.parametrize("field,value", [("bytes", 0), ("bytes", MAX_SHEET_BYTES + 1), ("width", 8193)])
def test_invalid_sheet_metadata_is_rejected_before_any_file_read(tmp_path, field, value):
    async def scenario():
        content = b"owned sheet bytes"
        service, objects, images, result = fixture(tmp_path, content)
        result[field] = value
        output = Output(content)
        with pytest.raises(BusinessError) as error:
            await service._register_sheet("agent", output, result)
        assert error.value.code == "media_frames_invalid"
        assert not output.reads and not objects.calls and not images.calls

    asyncio.run(scenario())


def test_sheet_at_exact_eight_mib_limit_is_preserved(tmp_path):
    async def scenario():
        content = b"x" * MAX_SHEET_BYTES
        service, objects, _, result = fixture(tmp_path, content)
        output = Output(content)
        row = await service._register_sheet("agent", output, result)
        assert row["bytes"] == MAX_SHEET_BYTES and objects.calls[0][1] == content
        assert output.reads == [MAX_SHEET_BYTES + 1] and output.closed == [True]

    asyncio.run(scenario())


def test_sheet_read_error_closes_stream_without_publishing(tmp_path):
    async def scenario():
        content = b"owned sheet bytes"
        service, objects, images, result = fixture(tmp_path, content)
        failure = OSError("PRIVATE_OUTPUT_READ_FAILURE")
        output = Output(content, error=failure)
        with pytest.raises(OSError) as error:
            await service._register_sheet("agent", output, result)
        assert error.value is failure and output.closed == [True]
        assert output.reads == [len(content) + 1] and not objects.calls and not images.calls

    asyncio.run(scenario())


@pytest.mark.parametrize("case", ["normal", "exact_limit", "over_limit", "wrong_kind"])
def test_real_asr_jobs_bound_output_before_publication_and_allow_explicit_new_work(tmp_path, monkeypatch, case):
    from test_v2_media import BV, FakeProcessor, _media_instance, _seed, _settle

    class Processor(FakeProcessor):
        async def transcribe(self, audio, output, plan):
            kind = "subtitle" if case == "wrong_kind" else "asr"
            self.body = transcript_payload(kind, "zh", [[0.0, 0.0, "owned output"]])
            if case in {"exact_limit", "over_limit"}:
                self.body += b" " * (MAX_TRANSCRIPT_BYTES - len(self.body) + (case == "over_limit"))
            output.write_bytes(self.body)
            return {"ok": True}

    async def scenario():
        processor = Processor()
        instance = await _media_instance(tmp_path, processor=processor)
        reads, closed, published = [], [], []
        original_open, original_publish = Path.open, instance.media._objects.publish

        def observe(path, mode="r", *args, **kwargs):
            stream = original_open(path, mode, *args, **kwargs)
            if mode != "rb" or path.name != "transcript.json" or path.parent.parent != instance.media._staging:
                return stream

            class Reader:
                def __enter__(self):
                    stream.__enter__()
                    return self

                def __exit__(self, *info):
                    result = stream.__exit__(*info)
                    closed.append(stream.closed)
                    return result

                def read(self, size=-1):
                    reads.append(size)
                    return stream.read(size)

            return Reader()

        async def publish(agent_id, content, mime):
            published.append((content, mime))
            return await original_publish(agent_id, content, mime)

        try:
            agent, config = await _seed(instance)
            source = await instance.media.register(agent, str(config["id"]), BV)
            with monkeypatch.context() as patch:
                patch.setattr(Path, "open", observe)
                patch.setattr(instance.media._objects, "publish", publish)
                job = await instance.media.submit(agent, str(source["id"]), "transcribe", {})
                row = await _settle(instance, agent, job["id"])
            if case in {"over_limit", "wrong_kind"}:
                assert row["state"] == "failed" and row["error_code"] == "media_transcript_unreadable"
                assert not published
                view = await instance.media.source(agent, str(source["id"]))
                assert not view["latest_transcript_id"]
            else:
                assert row["state"] == "done" and published == [(processor.body, "application/json")]
                view = await instance.media.source(agent, str(source["id"]))
                artifact = await instance.media.transcript(agent, str(view["latest_transcript_id"]))
                assert artifact["kind"] == "asr" and artifact["bytes"] == len(processor.body)
            assert reads == [MAX_TRANSCRIPT_BYTES + 1] and closed == [True]
            assert not (instance.media._staging / str(job["id"])).exists()
            fresh = await instance.media.submit(agent, str(source["id"]), "subtitle", {})
            assert (await _settle(instance, agent, fresh["id"]))["state"] == "done"
            assert (await instance.media.job(agent, str(job["id"])))["state"] == row["state"]
        finally:
            assert (await instance.close())["complete"]

    asyncio.run(scenario())
