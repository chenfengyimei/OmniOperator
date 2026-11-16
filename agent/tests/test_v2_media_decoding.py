"""Bound video decode consumption and release retained iterators before container teardown."""

from __future__ import annotations

import asyncio
import json
import sys
from contextlib import closing
from textwrap import dedent
from types import SimpleNamespace

import pytest
from test_v2_media_frames import frames as frames

from omni.adapters.workers import media as worker


class Decoder:
    def __init__(self, native, *, error_at=None, close_fault=False, events=None):
        self.native = native
        self.error_at, self.close_fault = error_at, close_fault
        self.attempts = self.closed = 0
        self.events = events if events is not None else []
        self.error = RuntimeError("injected decoder failure")

    def __iter__(self):
        return self

    def __next__(self):
        self.attempts += 1
        if self.attempts == self.error_at:
            raise self.error
        return next(self.native)

    def close(self):
        self.closed += 1
        self.events.append("decoder_close")
        close = getattr(self.native, "close", None)
        if callable(close):
            close()
        if self.close_fault:
            raise OSError("injected decoder close failure")


@pytest.mark.parametrize(
    "times,target,error_at,expected,attempts",
    [
        ([0.0, 0.75, 1.0], 0.5, None, 1, 2),
        ([0.0, 0.75], 2.0, None, 1, 3),
        ([], 0.5, None, None, 1),
        ([None, 0.75], 0.5, None, "anchor", 1),
        ([0.0, 0.75], 0.5, 2, "decode", 2),
    ],
)
def test_sampling_closes_retained_decoder_on_target_eof_and_errors(times, target, error_at, expected, attempts):
    candidates = [SimpleNamespace(time=value) for value in times]
    decoder = Decoder(iter(candidates), error_at=error_at)
    container = SimpleNamespace(decode=lambda stream: decoder, seek=lambda *args, **kwargs: None)
    if expected == "anchor":
        with pytest.raises(ValueError, match="^frame_timestamp_unusable$"):
            worker._frame_at(container, SimpleNamespace(time_base=0.5), target)
    elif expected == "decode":
        with pytest.raises(RuntimeError) as caught:
            worker._frame_at(container, SimpleNamespace(time_base=0.5), target)
        assert caught.value is decoder.error
    else:
        result = worker._frame_at(container, SimpleNamespace(time_base=0.5), target)
        assert result is (None if expected is None else candidates[expected])
    assert decoder.attempts == attempts and decoder.closed == 1


def test_seek_failure_does_not_create_decoder():
    def seek(*args, **kwargs):
        raise OSError("fixture is not seekable")

    def decode(stream):
        raise AssertionError("decode must not start after failed seek")

    assert worker._frame_at(SimpleNamespace(seek=seek, decode=decode), SimpleNamespace(time_base=0.5), 1) is None


def test_plain_iterator_sampling_still_uses_nearest_actual_frame():
    candidates = [SimpleNamespace(time=0.0), SimpleNamespace(time=0.75)]
    container = SimpleNamespace(decode=lambda stream: iter(candidates), seek=lambda *args, **kwargs: None)
    assert worker._frame_at(container, SimpleNamespace(time_base=0.5), 0.5) is candidates[1]


def test_sampling_close_failure_retains_original_timestamp_error():
    decoder = Decoder(iter([SimpleNamespace(time=None)]), close_fault=True)
    container = SimpleNamespace(decode=lambda stream: decoder, seek=lambda *args, **kwargs: None)
    with pytest.raises(OSError, match="injected decoder close failure") as caught:
        worker._frame_at(container, SimpleNamespace(time_base=0.5), 0.5)
    assert isinstance(caught.value.__context__, ValueError)
    assert str(caught.value.__context__) == "frame_timestamp_unusable" and decoder.closed == 1


def install_decoder(frames, monkeypatch, *, error_at=None, close_fault=False, short=False):
    frames.state.metadata = False
    original_open = sys.modules["av"].open
    retained, events = [], []

    class Container:
        def __init__(self, native):
            self.native = native

        def __getattr__(self, name):
            return getattr(self.native, name)

        def __enter__(self):
            self.native.__enter__()
            return self

        def __exit__(self, *args):
            events.append("container_exit")
            return self.native.__exit__(*args)

        def decode(self, stream):
            native = self.native.decode(stream)
            if short:
                original = native

                def first():
                    try:
                        yield next(original)
                    finally:
                        original.close()

                native = first()
            decoder = Decoder(native, error_at=error_at, close_fault=close_fault, events=events)
            retained.append(decoder)  # Prevent refcount/GC cleanup from masking missing ownership.
            return decoder

    monkeypatch.setattr(sys.modules["av"], "open", lambda path: Container(original_open(path)))
    return retained, events


@pytest.mark.parametrize("count", [1, 3, 12])
def test_metadata_less_video_stops_before_decoder_tail_and_closes_before_container(frames, monkeypatch, count):
    frames.request["count"] = count
    retained, events = install_decoder(frames, monkeypatch, error_at=count + 1)
    result = worker._frames(frames.request)
    assert result["frame_count"] == count and frames.state.conversions == count
    assert result["timestamps"] == [index + 0.25 for index in range(count)]
    assert retained[0].attempts == count and retained[0].closed == 1
    assert events == ["decoder_close", "container_exit"]
    assert frames.state.container_closed == 1 and all(image.closed == 1 for image in frames.state.images)


@pytest.mark.parametrize("phase", ["decode", "convert", "resize", "budget", "short"])
def test_metadata_less_decoder_closes_on_failure_and_short_input(frames, monkeypatch, phase):
    retained, events = install_decoder(
        frames, monkeypatch, error_at=2 if phase == "decode" else None, short=phase == "short"
    )
    if phase in {"convert", "resize"}:
        frames.state.phase = phase
    elif phase == "budget":
        frames.state.source_size = (1, 100000)
    if phase == "short":
        assert worker._frames(frames.request)["frame_count"] == 1
    else:
        with pytest.raises(ValueError if phase == "budget" else RuntimeError):
            worker._frames(frames.request)
        assert not frames.output.exists()
    assert retained[0].closed == 1 and events == ["decoder_close", "container_exit"]
    assert frames.state.container_closed == 1 and all(image.closed == 1 for image in frames.state.images)


def test_decoder_close_failure_still_closes_picked_images_and_retains_decode_error(frames, monkeypatch):
    retained, events = install_decoder(frames, monkeypatch, error_at=2, close_fault=True)
    with pytest.raises(OSError, match="injected decoder close failure") as caught:
        worker._frames(frames.request)
    assert caught.value.__context__ is retained[0].error
    assert retained[0].closed == 1 and events == ["decoder_close", "container_exit"]
    assert frames.state.conversions == 1 and all(image.closed == 1 for image in frames.state.images)
    assert frames.state.container_closed == 1 and not frames.output.exists()


def processor_with_decoder_tail(tmp_path, *, bad_first=False):
    from omni.adapters.media import MediaProcessor

    script, audit = tmp_path / "decoder_worker.py", tmp_path / "decoder_audit.jsonl"
    script.write_text(
        "AUDIT = "
        + repr(str(audit))
        + "\nBAD_FIRST = "
        + repr(bad_first)
        + "\n"
        + dedent("""\
        import av
        import json
        import os
        from pathlib import Path
        from types import SimpleNamespace
        from omni.adapters.workers import media

        original_open, original_frames = av.open, media._frames
        requests, limit = 0, 0
        retained = []

        class Decoder:
            def __init__(self, native):
                self.native = native
                self.attempts = self.closed = 0
                retained.append(self)
            def __iter__(self):
                return self
            def __next__(self):
                self.attempts += 1
                if BAD_FIRST and requests == 1 and self.attempts == 2:
                    raise ValueError('frame_timestamp_unusable')
                if self.attempts > limit:
                    raise RuntimeError('damaged tail after requested frames')
                return next(self.native)
            def close(self):
                self.closed += 1
                self.native.close()

        class Container:
            duration = 0
            def __init__(self, native):
                self.native = native
                stream = native.streams.video[0]
                self.streams = SimpleNamespace(video=[SimpleNamespace(duration=None, time_base=stream.time_base)])
            def __enter__(self):
                self.native.__enter__()
                return self
            def __exit__(self, *args):
                decoder = retained[-1]
                with Path(AUDIT).open('a', encoding='utf-8') as output:
                    output.write(json.dumps(dict(request=requests, pid=os.getpid(), attempts=decoder.attempts,
                                                 closed_before_container=decoder.closed)) + '\\n')
                return self.native.__exit__(*args)
            def decode(self, stream):
                return Decoder(self.native.decode(video=0))

        def frames(request):
            global requests, limit
            requests += 1
            limit = request['count']
            return original_frames(request)

        av.open = lambda *args, **kwargs: Container(original_open(*args, **kwargs))
        media._frames = frames
        raise SystemExit(media.main())
        """),
        encoding="utf-8",
    )
    return MediaProcessor([sys.executable, str(script)]), audit


def test_real_stdio_decoder_tail_is_not_read_and_same_worker_handles_next_request(tmp_path):
    from PIL import Image
    from test_bilibili_frames import _make_video

    video = _make_video(tmp_path / "source.mp4", seconds=1, fps=4, size=(64, 48))

    async def scenario():
        processor, audit = processor_with_decoder_tail(tmp_path)
        try:
            for count in (2, 1):
                output = tmp_path / f"sheet-{count}.png"
                result = await processor.frames(video, output, count, 320)
                assert result["frame_count"] == count and result["timestamps"] == [index / 4 for index in range(count)]
                with closing(Image.open(output)) as sheet:
                    sheet.load()
                    assert sheet.format == "PNG" and sheet.size == (320 * count, 260)
            rows = [json.loads(line) for line in audit.read_text(encoding="utf-8").splitlines()]
            assert len(rows) == 2 and rows[0]["pid"] == rows[1]["pid"]
            assert [row["attempts"] for row in rows] == [2, 1]
            assert all(row["closed_before_container"] == 1 for row in rows)
        finally:
            assert await processor.close()

    asyncio.run(scenario())


def test_real_job_decoder_failure_has_no_artifact_and_fresh_bounded_job_succeeds(tmp_path):
    from test_bilibili_frames import _make_video
    from test_v2_media import BV, FakeGateway, _media_instance, _seed, _settle

    video = _make_video(tmp_path / "source.mp4", seconds=1, fps=4, size=(64, 48))

    class Gateway(FakeGateway):
        async def download(self, configuration, url, destination, **kwargs):
            destination.write_bytes(video.read_bytes())
            return {"bytes": destination.stat().st_size}

    async def scenario():
        processor, audit = processor_with_decoder_tail(tmp_path, bad_first=True)
        instance = await _media_instance(tmp_path / "instance", Gateway(), processor)
        try:
            agent, config = await _seed(instance)
            source = await instance.media.register(agent, str(config["id"]), BV)
            failed = await instance.media.submit(agent, str(source["id"]), "frames", {"count": 2, "width": 320})
            row = await _settle(instance, agent, failed["id"])
            assert row["state"] == "failed" and row["error_code"] == "media_frame_timestamp_unusable"
            assert not (await instance.media.source(agent, str(source["id"])))["latest_frames_id"]
            assert not (instance.media._staging / str(failed["id"])).exists()
            fresh = await instance.media.submit(agent, str(source["id"]), "frames", {"count": 2, "width": 320})
            assert (await _settle(instance, agent, fresh["id"]))["state"] == "done"
            assert not (instance.media._staging / str(fresh["id"])).exists()
            assert (await instance.media.job(agent, str(failed["id"])))["state"] == "failed"
            latest = (await instance.media.source(agent, str(source["id"])))["latest_frames_id"]
            assert (await instance.media.frames(agent, str(latest)))["timestamps"] == [0.0, 0.25]
            rows = [json.loads(line) for line in audit.read_text(encoding="utf-8").splitlines()]
            assert len(rows) == 2 and rows[0]["pid"] == rows[1]["pid"]
            assert all(row["closed_before_container"] == 1 for row in rows)
        finally:
            assert (await instance.close())["complete"]

    asyncio.run(scenario())
