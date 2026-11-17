"""Reject unusable declared video duration before seeking, decoding or publishing frame sheets."""

from __future__ import annotations

import asyncio
import json
import sys
from contextlib import closing
from fractions import Fraction
from textwrap import dedent
from types import SimpleNamespace

import pytest
from test_v2_media_frames import frames as frames

from omni.adapters.workers import media as worker
from omni.domain.contracts import BusinessError


def metadata(frames, monkeypatch, stream_seconds, container_seconds=0, time_base=1):
    original_open, original_sample = sys.modules["av"].open, worker._frame_at
    state = SimpleNamespace(targets=[], decodes=0)
    monkeypatch.setattr(sys.modules["av"], "time_base", 1000000, raising=False)

    class Container:
        duration = None if container_seconds is None else container_seconds * 1000000

        def __init__(self, native):
            self.native = native

        def __enter__(self):
            self.native.__enter__()
            self.streams = SimpleNamespace(video=[SimpleNamespace(duration=stream_seconds, time_base=time_base)])
            return self

        def __exit__(self, *args):
            return self.native.__exit__(*args)

        def decode(self, stream):
            state.decodes += 1
            return self.native.decode(stream)

    def sample(*args):
        state.targets.append(args[2])
        return original_sample(*args)

    monkeypatch.setattr(sys.modules["av"], "open", lambda *args, **kwargs: Container(original_open(*args, **kwargs)))
    monkeypatch.setattr(worker, "_frame_at", sample)
    return state


@pytest.mark.parametrize("source", ["stream", "container"])
@pytest.mark.parametrize("seconds", [-1, 7200.001, float("inf"), -float("inf"), float("nan"), 10**400])
def test_bad_declared_duration_fails_before_seek_decode_or_image_allocation(frames, monkeypatch, source, seconds):
    state = metadata(
        frames, monkeypatch, seconds if source == "stream" else None, seconds if source == "container" else 0
    )
    with pytest.raises(ValueError, match="^video_duration_out_of_range$"):
        worker._frames(frames.request)
    assert not state.targets and state.decodes == 0 and frames.state.conversions == 0
    assert frames.state.container_closed == 1 and not frames.state.images and not frames.state.buffers
    assert not frames.output.parent.exists()


@pytest.mark.parametrize("source", ["stream", "container"])
@pytest.mark.parametrize("seconds", [None, 0, 0.25, 7200])
def test_unknown_zero_fractional_and_exact_limit_durations_keep_existing_paths(frames, monkeypatch, source, seconds):
    state = metadata(
        frames, monkeypatch, seconds if source == "stream" else None, seconds if source == "container" else 0
    )
    result = worker._frames(frames.request)
    assert result["frame_count"] == 3 and result["timestamps"] == [0.25, 1.25, 2.25]
    assert (
        (state.decodes == 1 and not state.targets) if not seconds else (state.decodes == 0 and len(state.targets) == 3)
    )
    assert frames.state.container_closed == 1 and all(image.closed == 1 for image in frames.state.images)


@pytest.mark.parametrize(
    "stream_seconds,time_base,container_seconds,expected",
    [(10, Fraction(1, 2), 7201, 5), (None, 1, 10, 10), (10, 0, 10, 10)],
)
def test_stream_precedence_rational_time_base_and_container_fallback_are_preserved(
    frames, monkeypatch, stream_seconds, time_base, container_seconds, expected
):
    state = metadata(frames, monkeypatch, stream_seconds, container_seconds, time_base)
    assert worker._frames(frames.request)["frame_count"] == 3
    assert state.targets == worker._target_times(expected, 3) and state.decodes == 0


@pytest.mark.parametrize("source", ["stream", "container"])
def test_worker_protocol_reports_precise_duration_error_without_output(frames, monkeypatch, capsys, source):
    state = metadata(frames, monkeypatch, 7201 if source == "stream" else None, 7201 if source == "container" else 0)
    identity = "12345678-1234-1234-1234-123456789abc"
    worker._handle(json.dumps({"version": 1, "id": identity, "operation": "frames", **frames.request}))
    reply = json.loads(capsys.readouterr().out)
    assert reply["id"] == identity and reply["result"]["ok"] is False
    assert reply["result"]["code"] == "media_video_duration_out_of_range"
    assert state.decodes == 0 and not state.targets and not frames.output.exists()


def processor_with_long_first_duration(tmp_path, source):
    from omni.adapters.media import MediaProcessor

    script, audit = tmp_path / "duration_worker.py", tmp_path / "duration_audit.jsonl"
    script.write_text(
        "AUDIT = "
        + repr(str(audit))
        + "\nSOURCE = "
        + repr(source)
        + "\n"
        + dedent("""\
        import av
        import json
        import os
        from fractions import Fraction
        from pathlib import Path
        from types import SimpleNamespace
        from omni.adapters.workers import media

        original_open, original_frames = av.open, media._frames
        requests = 0

        class Container:
            def __init__(self, native):
                self.native = native
                self.decodes = self.seeks = 0
                self.duration = 7201000000 if requests == 1 and SOURCE == 'container' else native.duration
                if requests == 1:
                    self.streams = SimpleNamespace(video=[SimpleNamespace(
                        duration=7201 if SOURCE == 'stream' else None, time_base=Fraction(1, 1))])
                else:
                    self.streams = native.streams
            def __enter__(self):
                self.native.__enter__()
                return self
            def __exit__(self, *args):
                result = self.native.__exit__(*args)
                with Path(AUDIT).open('a', encoding='utf-8') as output:
                    output.write(json.dumps(dict(request=requests, pid=os.getpid(), decodes=self.decodes,
                                                 seeks=self.seeks, container_closed=True)) + '\\n')
                return result
            def seek(self, offset, **kwargs):
                self.seeks += 1
                kwargs['stream'] = self.native.streams.video[0]
                return self.native.seek(offset, **kwargs)
            def decode(self, stream):
                self.decodes += 1
                return self.native.decode(video=0)

        def frames(request):
            global requests
            requests += 1
            return original_frames(request)

        av.open = lambda *args, **kwargs: Container(original_open(*args, **kwargs))
        media._frames = frames
        raise SystemExit(media.main())
        """),
        encoding="utf-8",
    )
    return MediaProcessor([sys.executable, str(script)]), audit


@pytest.mark.parametrize("source", ["stream", "container"])
def test_real_stdio_rejects_bad_metadata_without_decode_then_reuses_worker_for_real_video(tmp_path, source):
    from PIL import Image
    from test_bilibili_frames import _make_video

    video = _make_video(tmp_path / "source.mp4", seconds=1, fps=2, size=(64, 48))

    async def scenario():
        processor, audit = processor_with_long_first_duration(tmp_path, source)
        output = tmp_path / "sheet.png"
        try:
            with pytest.raises(BusinessError) as caught:
                await processor.frames(video, output, 1, 320)
            assert caught.value.code == "media_video_duration_out_of_range" and not output.exists()
            owner = processor._owned
            assert owner is not None
            result = await processor.frames(video, output, 1, 320)
            assert processor._owned is owner and result["timestamps"] == [0.5]
            with closing(Image.open(output)) as sheet:
                sheet.load()
                assert sheet.format == "PNG" and sheet.size == (320, 260)
            rows = [json.loads(line) for line in audit.read_text(encoding="utf-8").splitlines()]
            assert len(rows) == 2 and rows[0]["pid"] == rows[1]["pid"]
            assert rows[0]["decodes"] == rows[0]["seeks"] == 0 and rows[1]["decodes"] == rows[1]["seeks"] == 1
            assert all(row["container_closed"] for row in rows)
        finally:
            assert await processor.close()

    asyncio.run(scenario())


@pytest.mark.parametrize("source_kind", ["stream", "container"])
def test_real_job_duration_failure_has_no_frame_artifact_and_explicit_new_job_succeeds(tmp_path, source_kind):
    from test_bilibili_frames import _make_video
    from test_v2_media import BV, FakeGateway, _media_instance, _seed, _settle

    video = _make_video(tmp_path / "source.mp4", seconds=1, fps=2, size=(64, 48))

    class Gateway(FakeGateway):
        async def download(self, configuration, url, destination, **kwargs):
            destination.write_bytes(video.read_bytes())
            return {"bytes": destination.stat().st_size}

    async def scenario():
        processor, audit = processor_with_long_first_duration(tmp_path, source_kind)
        instance = await _media_instance(tmp_path / "instance", Gateway(), processor)
        try:
            agent, config = await _seed(instance)
            source = await instance.media.register(agent, str(config["id"]), BV)
            failed = await instance.media.submit(agent, str(source["id"]), "frames", {"count": 1, "width": 320})
            row = await _settle(instance, agent, failed["id"])
            assert row["state"] == "failed" and row["error_code"] == "media_video_duration_out_of_range"
            assert not (await instance.media.source(agent, str(source["id"])))["latest_frames_id"]
            assert not (instance.media._staging / str(failed["id"])).exists()
            fresh = await instance.media.submit(agent, str(source["id"]), "frames", {"count": 1, "width": 320})
            assert (await _settle(instance, agent, fresh["id"]))["state"] == "done"
            assert not (instance.media._staging / str(fresh["id"])).exists()
            assert (await instance.media.job(agent, str(failed["id"])))["state"] == "failed"
            latest = (await instance.media.source(agent, str(source["id"])))["latest_frames_id"]
            assert (await instance.media.frames(agent, str(latest)))["timestamps"] == [0.5]
            rows = [json.loads(line) for line in audit.read_text(encoding="utf-8").splitlines()]
            assert len(rows) == 2 and rows[0]["pid"] == rows[1]["pid"]
            assert rows[0]["decodes"] == rows[0]["seeks"] == 0
            assert all(row["container_closed"] for row in rows)
        finally:
            assert (await instance.close())["complete"]

    asyncio.run(scenario())
