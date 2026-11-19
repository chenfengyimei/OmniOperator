"""Scene-cut frame selection: pure allocation rules, real synthetic-video HSV cuts + Laplacian sharpness, job/REST wiring."""

from __future__ import annotations

import asyncio
from pathlib import Path

import pytest
from omni_test_support.v2.media_frames import frames as frames

from omni.adapters.workers import media as worker
from omni.domain.contracts import BusinessError
from omni.domain.media import frames_plan, scene_cuts, scene_segments, scene_slots


def _make_scene_video(
    path: Path, scenes: list[tuple[float, float, tuple[int, int, int]]], *, fps: int = 5, size=(64, 48)
) -> Path:
    import av
    from PIL import Image

    duration = scenes[-1][1]
    container = av.open(str(path), mode="w")
    try:
        stream = container.add_stream("mpeg4", rate=fps)
        stream.width, stream.height = size
        stream.pix_fmt = "yuv420p"
        for index in range(int(duration * fps)):
            moment = index / fps
            color = next(c for start, end, c in scenes if start <= moment < end)
            frame = av.VideoFrame.from_image(Image.new("RGB", size, color))
            for packet in stream.encode(frame):
                container.mux(packet)
        for packet in stream.encode():
            container.mux(packet)
    finally:
        container.close()
    return path


def _checker(size=(64, 48)):
    from PIL import Image

    image = Image.new("RGB", size)
    image.putdata(
        [(255, 255, 255) if (x // 4 + y // 4) % 2 else (0, 0, 0) for y in range(size[1]) for x in range(size[0])]
    )
    return image


def _make_sharpness_video(path: Path, *, fps: int = 4, size=(64, 48)) -> Path:
    import av
    from PIL import Image

    checker = _checker(size)
    container = av.open(str(path), mode="w")
    try:
        stream = container.add_stream("mpeg4", rate=fps)
        stream.width, stream.height = size
        stream.pix_fmt = "yuv420p"
        for index in range(8 * fps):
            moment = index / fps
            if moment < 4:
                image = checker if int(moment) % 2 == 0 else Image.new("RGB", size, (128, 128, 128))
            else:
                image = Image.new("RGB", size, (0, 0, 255))
            frame = av.VideoFrame.from_image(image)
            for packet in stream.encode(frame):
                container.mux(packet)
        for packet in stream.encode():
            container.mux(packet)
    finally:
        container.close()
    return path


def test_plan_select_defaults_keep_shape_and_scenes_adds_key():
    assert frames_plan(3, 640) == {"count": 3, "width": 640}
    assert frames_plan(3, 640, None, "equidistant") == {"count": 3, "width": 640}
    assert frames_plan(3, 640, None, "scenes") == {"count": 3, "width": 640, "select": "scenes"}


def test_plan_rejects_unknown_select_and_scenes_with_timestamps():
    for bad in ("bogus", "", "SCENES", 5, True):
        with pytest.raises(BusinessError) as caught:
            frames_plan(3, 640, None, bad)
        assert caught.value.code == "media_frames_invalid"
    with pytest.raises(BusinessError) as caught:
        frames_plan(2, 640, [0.5, 2.0], "scenes")
    assert caught.value.code == "media_frames_invalid"


def test_scene_cuts_threshold_and_min_gap_suppression():
    times = [0.0, 0.5, 1.0, 1.2, 2.0, 2.5]
    scores = [0.0, 2.0, 30.0, 40.0, 3.0, 13.0]
    assert scene_cuts(times, scores) == [1.2, 2.5]
    assert scene_cuts([0.0, 1.0], [0.0, 11.9]) == []
    assert scene_cuts([], []) == []
    assert scene_cuts([0.0, 1.0], [99.0, 0.0]) == []


def test_scene_segments_boundaries():
    assert scene_segments([4.0, 8.0], 12.0) == [(0.0, 4.0), (4.0, 8.0), (8.0, 12.0)]
    assert scene_segments([], 12.0) == [(0.0, 12.0)]


def test_scene_slots_proportional_and_longest_when_scarce():
    segments = [(0.0, 2.0), (2.0, 10.0)]
    windows = scene_slots(segments, 4)
    assert len(windows) == 4
    assert sum(1 for start, _ in windows if start < 2.0) == 1
    assert windows == sorted(windows)
    assert scene_slots(segments, 2) == [(0.0, 2.0), (2.0, 10.0)]
    scarce = scene_slots([(0.0, 1.0), (1.0, 5.0), (5.0, 6.0)], 2)
    assert scarce == [(0.0, 1.0), (1.0, 5.0)]
    assert scene_slots([], 3) == [] and scene_slots(segments, 0) == []


def test_worker_scenes_selects_frames_across_detected_cuts(tmp_path):
    video = _make_scene_video(tmp_path / "v.mp4", [(0, 4, (255, 0, 0)), (4, 8, (0, 255, 0)), (8, 12, (0, 0, 255))])
    request = {
        "video": str(video),
        "sheet_output": str(tmp_path / "sheet.png"),
        "count": 6,
        "width": 320,
        "select": "scenes",
    }
    result = worker._frames(request)
    assert result["frame_count"] == 6 and result["select"] == "scenes"
    cuts = result["scene_cuts"]
    assert len(cuts) == 2 and 3.5 < cuts[0] < 5.0 and 7.5 < cuts[1] < 9.0
    stamps = result["timestamps"]
    assert any(t < cuts[0] for t in stamps)
    assert any(cuts[0] <= t < cuts[1] for t in stamps)
    assert any(t >= cuts[1] for t in stamps)


def test_worker_scenes_prefers_laplacian_sharp_frames(tmp_path):
    video = _make_sharpness_video(tmp_path / "v.mp4")
    request = {
        "video": str(video),
        "sheet_output": str(tmp_path / "sheet.png"),
        "count": 2,
        "width": 320,
        "select": "scenes",
    }
    result = worker._frames(request)
    assert result["frame_count"] == 2
    first = result["timestamps"][0]
    assert first < 4.0 and int(first) % 2 == 0
    assert result["timestamps"][1] >= 4.0


def test_worker_scenes_without_cuts_falls_back_equidistant(tmp_path):
    video = _make_scene_video(tmp_path / "v.mp4", [(0, 8, (10, 200, 30))])
    request = {
        "video": str(video),
        "sheet_output": str(tmp_path / "sheet.png"),
        "count": 3,
        "width": 320,
        "select": "scenes",
    }
    result = worker._frames(request)
    assert result["scene_cuts"] == []
    assert result["timestamps"] == [round(t, 3) for t in worker._target_times(8.0, 3)]


def test_worker_default_mode_response_shape_unchanged(tmp_path):
    video = _make_scene_video(tmp_path / "v.mp4", [(0, 4, (255, 0, 0)), (4, 8, (0, 255, 0))])
    request = {"video": str(video), "sheet_output": str(tmp_path / "sheet.png"), "count": 3, "width": 320}
    result = worker._frames(request)
    assert "select" not in result and "scene_cuts" not in result
    assert result["frame_count"] == 3


def test_worker_rejects_invalid_select_before_decoding(frames):
    frames.request.update(select="bogus")
    with pytest.raises(ValueError, match="Invalid frame plan"):
        worker._frames(frames.request)
    assert frames.state.container_closed == 0 and not frames.output.exists()


def test_worker_rejects_scenes_with_explicit_timestamps(frames):
    frames.request.update(count=2, select="scenes", timestamps=[0.5, 2.0])
    with pytest.raises(ValueError, match="Invalid frame plan"):
        worker._frames(frames.request)
    assert frames.state.container_closed == 0 and not frames.output.exists()


def test_real_processor_forwards_scene_selection(tmp_path):
    from omni.adapters.media import MediaProcessor

    video = _make_scene_video(tmp_path / "v.mp4", [(0, 4, (255, 0, 0)), (4, 8, (0, 255, 0))])

    async def scenario():
        processor = MediaProcessor()
        try:
            result = await processor.frames(video, tmp_path / "sheet.png", 2, 320, None, "scenes")
            assert result["frame_count"] == 2 and result["select"] == "scenes"
            assert len(result["scene_cuts"]) == 1
        finally:
            assert await processor.close()

    asyncio.run(scenario())


def test_real_job_scene_selection_persists_and_addresses_cells(tmp_path):
    from omni_test_support.v2.media import BV, FakeGateway, _media_instance, _seed, _settle

    from omni.adapters.media import MediaProcessor

    video = _make_scene_video(tmp_path / "v.mp4", [(0, 4, (255, 0, 0)), (4, 8, (0, 255, 0)), (8, 12, (0, 0, 255))])

    class Gateway(FakeGateway):
        async def download(self, configuration, url, destination, **kwargs):
            destination.write_bytes(video.read_bytes())
            return {"bytes": destination.stat().st_size}

    async def scenario():
        instance = await _media_instance(tmp_path / "instance", Gateway(), MediaProcessor())
        try:
            agent, config = await _seed(instance)
            source = await instance.media.register(agent, str(config["id"]), BV)
            for bad in ({"select": "bogus"}, {"select": "scenes", "timestamps": [0.5, 2.0]}):
                with pytest.raises(BusinessError) as caught:
                    await instance.media.submit(agent, str(source["id"]), "frames", {"count": 2, "width": 320, **bad})
                assert caught.value.code == "media_frames_invalid"
            job = await instance.media.submit(
                agent, str(source["id"]), "frames", {"count": 3, "width": 320, "select": "scenes"}
            )
            row = await _settle(instance, agent, job["id"])
            assert row["state"] == "done", row
            cells = await instance.media.cells(agent, str(source["id"]), None, None)
            assert cells["frame_count"] == 3 and len(cells["cells"]) == 3
        finally:
            assert (await instance.close())["complete"]

    asyncio.run(scenario())


def test_rest_input_select_field():
    import pydantic

    from omni.adapters.http import MediaJobInput

    assert MediaJobInput.model_validate({"operation": "frames"}).select == "equidistant"
    data = MediaJobInput.model_validate({"operation": "frames", "select": "scenes"})
    assert data.select == "scenes"
    with pytest.raises(pydantic.ValidationError):
        MediaJobInput.model_validate({"operation": "frames", "select": "bogus"})
