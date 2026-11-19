"""Explicit-timestamps frames jobs refetch chosen moments; equidistant default is unchanged."""

from __future__ import annotations

from omni_test_support.v2.media_frames import asyncio as asyncio
from omni_test_support.v2.media_frames import frames as frames
from omni_test_support.v2.media_frames import pytest as pytest
from omni_test_support.v2.media_frames import worker as worker

from omni.domain.contracts import BusinessError
from omni.domain.media import MAX_DURATION_S, frames_plan


def test_default_plan_shape_has_no_timestamps_key():
    assert frames_plan(3, 640) == {"count": 3, "width": 640}


def test_explicit_plan_keeps_one_anchor_per_cell():
    assert frames_plan(2, 1280, [0.5, 2.0]) == {"count": 2, "width": 1280, "timestamps": [0.5, 2.0]}


def test_integer_anchors_are_normalized_to_float():
    assert frames_plan(1, 320, [3]) == {"count": 1, "width": 320, "timestamps": [3.0]}


@pytest.mark.parametrize("anchor", [0.0, float(MAX_DURATION_S)])
def test_anchor_range_boundaries_are_accepted(anchor):
    assert frames_plan(1, 640, [anchor])["timestamps"] == [anchor]


@pytest.mark.parametrize(
    "count,timestamps",
    [
        (2, [0.5]),
        (1, []),
        (1, "0.5"),
        (1, 0.5),
        (1, {"a": 1}),
        (1, [float("nan")]),
        (1, [float("inf")]),
        (1, [-0.1]),
        (1, [float(MAX_DURATION_S) + 0.1]),
        (1, [True]),
        (1, ["0.5"]),
        (1, [None]),
    ],
)
def test_invalid_explicit_anchors_are_rejected(count, timestamps):
    with pytest.raises(BusinessError) as caught:
        frames_plan(count, 640, timestamps)
    assert caught.value.code == "media_frames_invalid"


def test_worker_forwards_explicit_targets_to_seek(frames, monkeypatch):
    recorded = []
    original = worker._frame_at

    def recorder(container, stream, target):
        recorded.append(target)
        return original(container, stream, target)

    monkeypatch.setattr(worker, "_frame_at", recorder)
    frames.request.update(count=2, width=320, timestamps=[5.0, 9.0])
    result = worker._frames(frames.request)
    assert recorded == [5.0, 9.0]
    assert result["frame_count"] == 2


def test_worker_default_mode_still_uses_equidistant_targets(frames, monkeypatch):
    recorded = []
    original = worker._frame_at

    def recorder(container, stream, target):
        recorded.append(target)
        return original(container, stream, target)

    monkeypatch.setattr(worker, "_frame_at", recorder)
    frames.state.metadata = True
    frames.request.update(count=3, width=320)
    worker._frames(frames.request)
    assert recorded == [2.0, 5.0, 8.0]


@pytest.mark.parametrize(
    "count,timestamps",
    [
        (2, [0.5]),
        (1, []),
        (1, "0.5"),
        (1, 0.5),
        (1, [float("nan")]),
        (1, [float("inf")]),
        (1, [-0.1]),
        (1, [7200.1]),
        (1, [True]),
        (1, ["0.5"]),
        (1, [None]),
    ],
)
def test_worker_rejects_invalid_explicit_anchors_before_decoding(frames, count, timestamps):
    frames.request.update(count=count, width=320, timestamps=timestamps)
    with pytest.raises(ValueError, match="Invalid frame plan"):
        worker._frames(frames.request)
    assert frames.state.container_closed == 0 and not frames.output.exists()


def test_real_processor_seeks_explicit_timestamps(tmp_path):
    from PIL import Image
    from test_bilibili_frames import _make_video

    from omni.adapters.media import MediaProcessor

    video = _make_video(tmp_path / "v.mp4", seconds=4, fps=2, size=(64, 48))

    async def scenario():
        processor = MediaProcessor()
        output = tmp_path / "sheet.png"
        try:
            result = await processor.frames(video, output, 2, 320, [0.5, 2.0])
            assert result["frame_count"] == 2
            assert all(abs(a - b) < 0.6 for a, b in zip(result["timestamps"], [0.5, 2.0], strict=True))
            with Image.open(output) as sheet:
                sheet.load()
                assert sheet.format == "PNG" and sheet.size == (640, 260)
        finally:
            assert await processor.close()

    asyncio.run(scenario())


def test_real_job_explicit_timestamps_persist_and_address_cells(tmp_path):
    from omni_test_support.v2.media import BV, FakeGateway, _media_instance, _seed, _settle
    from test_bilibili_frames import _make_video

    from omni.adapters.media import MediaProcessor

    video = _make_video(tmp_path / "v.mp4", seconds=4, fps=2, size=(64, 48))

    class Gateway(FakeGateway):
        async def download(self, configuration, url, destination, **kwargs):
            destination.write_bytes(video.read_bytes())
            return {"bytes": destination.stat().st_size}

    async def scenario():
        instance = await _media_instance(tmp_path / "instance", Gateway(), MediaProcessor())
        try:
            agent, config = await _seed(instance)
            source = await instance.media.register(agent, str(config["id"]), BV)
            job = await instance.media.submit(
                agent, str(source["id"]), "frames", {"count": 2, "width": 320, "timestamps": [0.5, 2.0]}
            )
            row = await _settle(instance, agent, job["id"])
            assert row["state"] == "done", row
            src = await instance.media.source(agent, str(source["id"]))
            assert src["latest_frames_id"]
            cells = await instance.media.cells(agent, str(source["id"]), None, None)
            assert cells["frame_count"] == 2 and len(cells["cells"]) == 2 and cells["columns"] == 2
        finally:
            assert (await instance.close())["complete"]

    asyncio.run(scenario())


def test_rest_input_accepts_optional_timestamps():
    from omni.adapters.http import MediaJobInput

    data = MediaJobInput.model_validate({"operation": "frames", "count": 2, "width": 320, "timestamps": [0.5, 2.0]})
    assert data.timestamps == [0.5, 2.0]
    assert MediaJobInput.model_validate({"operation": "frames"}).timestamps is None


def test_rest_input_bounds_timestamp_list_length():
    import pydantic

    from omni.adapters.http import MediaJobInput

    with pytest.raises(pydantic.ValidationError):
        MediaJobInput.model_validate({"operation": "frames", "timestamps": [0.0] * 13})
