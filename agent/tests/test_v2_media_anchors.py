"""Frame anchors come from usable decoded timestamps, never zero or sample-target guesses."""

from __future__ import annotations

import asyncio
import json
import sys
from textwrap import dedent
from types import SimpleNamespace

import pytest
from test_v2_media_frames import frames as frames

from omni.adapters.workers import media as worker
from omni.domain.contracts import BusinessError
from omni.domain.media import MAX_DURATION_S

sample_frame = worker._frame_at


@pytest.mark.parametrize(
    "timestamp", [None, True, "1", float("nan"), float("inf"), -float("inf"), -0.001, 7200.001, 10**400]
)
def test_sampling_rejects_unusable_timestamp_without_decoding_another_frame(timestamp):
    decoded, seeks = [], []

    def decode(stream):
        decoded.append(timestamp)
        yield SimpleNamespace(time=timestamp)
        raise AssertionError("Decoded beyond the first unusable anchor")

    container = SimpleNamespace(decode=decode, seek=lambda *args, **kwargs: seeks.append((args, kwargs)))
    with pytest.raises(ValueError, match="^frame_timestamp_unusable$"):
        sample_frame(container, SimpleNamespace(time_base=0.5), 0.5)
    assert len(decoded) == 1 and len(seeks) == 1


@pytest.mark.parametrize("times,target,expected", [([0.0], 0.0, 0), ([7200.0], 7200.0, 0), ([0.0, 1.75, 2.2], 2.0, 2)])
def test_sampling_keeps_true_zero_duration_boundary_and_nearest_frame(times, target, expected):
    candidates = [SimpleNamespace(time=timestamp) for timestamp in times]
    container = SimpleNamespace(decode=lambda stream: iter(candidates), seek=lambda *args, **kwargs: None)
    assert sample_frame(container, SimpleNamespace(time_base=0.5), target) is candidates[expected]
    assert max(times) <= MAX_DURATION_S


@pytest.fixture
def stamped_frames(frames, monkeypatch):
    timestamps = [0.0, None]
    calls, original_sample = [], worker._frame_at
    original_open = sys.modules["av"].open

    def sample(*args):
        frame = original_sample(*args)
        frame.time = timestamps[len(calls)]
        calls.append(frame.time)
        return frame

    def opened(*args, **kwargs):
        container = original_open(*args, **kwargs)
        original_decode = container.decode

        def decode(stream):
            for index, frame in enumerate(original_decode(stream)):
                frame.time = timestamps[index]
                yield frame

        container.decode = decode
        return container

    monkeypatch.setattr(worker, "_frame_at", sample)
    monkeypatch.setattr(sys.modules["av"], "open", opened)
    frames.request["count"] = 2
    return SimpleNamespace(fixture=frames, timestamps=timestamps)


@pytest.mark.parametrize("metadata", [False, True])
@pytest.mark.parametrize("timestamp", [None, float("nan"), float("inf"), -0.001, 7200.001])
def test_generation_rejects_bad_anchor_before_conversion_and_closes_prior_images(stamped_frames, metadata, timestamp):
    fixture = stamped_frames.fixture
    fixture.state.metadata = metadata
    stamped_frames.timestamps[1] = timestamp
    with pytest.raises(ValueError, match="^frame_timestamp_unusable$"):
        worker._frames(fixture.request)
    assert fixture.state.conversions == 1 and fixture.state.container_closed == 1
    assert all(image.closed == 1 for image in fixture.state.images)
    assert not fixture.state.buffers and not fixture.output.exists()


@pytest.mark.parametrize("metadata", [False, True])
def test_generation_preserves_real_zero_and_exact_two_hour_anchor(stamped_frames, metadata):
    fixture = stamped_frames.fixture
    fixture.state.metadata = metadata
    stamped_frames.timestamps[:] = [0.0, float(MAX_DURATION_S)]
    result = worker._frames(fixture.request)
    assert result["timestamps"] == [0.0, 7200.0]
    assert fixture.state.conversions == 2 and all(image.closed == 1 for image in fixture.state.images)


@pytest.mark.parametrize("metadata", [False, True])
def test_worker_reports_missing_anchor_as_precise_business_failure(stamped_frames, metadata, capsys):
    fixture = stamped_frames.fixture
    fixture.state.metadata = metadata
    worker._handle(
        json.dumps(
            {"version": 1, "id": "12345678-1234-1234-1234-123456789abc", "operation": "frames", **fixture.request}
        )
    )
    reply = json.loads(capsys.readouterr().out)
    assert reply["result"]["ok"] is False
    assert reply["result"]["code"] == "media_frame_timestamp_unusable"
    assert not fixture.output.exists()


def processor_with_missing_first_pts(tmp_path):
    from omni.adapters.media import MediaProcessor

    script = tmp_path / "missing_pts_worker.py"
    script.write_text(
        dedent("""\
        import av
        from omni.adapters.workers import media

        original_open, original_frames = av.open, media._frames
        bad, requests = False, 0

        class Container:
            def __init__(self, native):
                self.native = native
            def __getattr__(self, name):
                return getattr(self.native, name)
            def __enter__(self):
                self.native.__enter__()
                return self
            def __exit__(self, *args):
                return self.native.__exit__(*args)
            def decode(self, *args, **kwargs):
                for frame in self.native.decode(*args, **kwargs):
                    if bad:
                        frame.pts = None
                    yield frame

        def frames(request):
            global bad, requests
            requests += 1
            bad = requests == 1
            return original_frames(request)

        av.open = lambda *args, **kwargs: Container(original_open(*args, **kwargs))
        media._frames = frames
        raise SystemExit(media.main())
        """),
        encoding="utf-8",
    )
    return MediaProcessor([sys.executable, str(script)])


def test_real_stdio_missing_pts_is_not_replaced_by_sample_target_and_worker_stays_usable(tmp_path):
    from PIL import Image
    from test_bilibili_frames import _make_video

    video = _make_video(tmp_path / "source.mp4", seconds=1, fps=2, size=(64, 48))

    async def scenario():
        processor = processor_with_missing_first_pts(tmp_path)
        output = tmp_path / "sheet.png"
        try:
            with pytest.raises(BusinessError) as caught:
                await processor.frames(video, output, 1, 320)
            assert caught.value.code == "media_frame_timestamp_unusable" and not output.exists()
            owner = processor._owned
            assert owner is not None
            result = await processor.frames(video, output, 1, 320)
            assert processor._owned is owner and result["timestamps"] == [0.5]
            with Image.open(output) as sheet:
                sheet.load()
                assert sheet.format == "PNG" and sheet.size == (320, 260)
        finally:
            assert await processor.close()

    asyncio.run(scenario())


def test_real_job_missing_pts_publishes_no_frame_artifact_and_explicit_new_job_succeeds(tmp_path):
    from test_bilibili_frames import _make_video
    from test_v2_media import BV, FakeGateway, _media_instance, _seed, _settle

    video = _make_video(tmp_path / "source.mp4", seconds=1, fps=2, size=(64, 48))

    class Gateway(FakeGateway):
        async def download(self, configuration, url, destination, **kwargs):
            destination.write_bytes(video.read_bytes())
            return {"bytes": destination.stat().st_size}

    async def scenario():
        instance = await _media_instance(tmp_path / "instance", Gateway(), processor_with_missing_first_pts(tmp_path))
        try:
            agent, config = await _seed(instance)
            source = await instance.media.register(agent, str(config["id"]), BV)
            failed = await instance.media.submit(agent, str(source["id"]), "frames", {"count": 1, "width": 320})
            row = await _settle(instance, agent, failed["id"])
            assert row["state"] == "failed" and row["error_code"] == "media_frame_timestamp_unusable"
            assert not (await instance.media.source(agent, str(source["id"])))["latest_frames_id"]
            assert not (instance.media._staging / str(failed["id"])).exists()
            fresh = await instance.media.submit(agent, str(source["id"]), "frames", {"count": 1, "width": 320})
            assert (await _settle(instance, agent, fresh["id"]))["state"] == "done"
            assert (await instance.media.job(agent, str(failed["id"])))["state"] == "failed"
            latest = (await instance.media.source(agent, str(source["id"])))["latest_frames_id"]
            assert (await instance.media.frames(agent, str(latest)))["timestamps"] == [0.5]
        finally:
            assert (await instance.close())["complete"]

    asyncio.run(scenario())
