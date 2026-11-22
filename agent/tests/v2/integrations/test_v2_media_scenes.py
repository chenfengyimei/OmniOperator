"""Scene-cut frame selection: pure allocation rules, real synthetic-video HSV cuts + Laplacian sharpness, job/REST wiring."""

from __future__ import annotations

import asyncio
from pathlib import Path

import pytest
from omni_test_support.v2.media_frames import frames as frames

from omni.adapters.workers import media as worker
from omni.domain.contracts import BusinessError
from omni.domain.media import bar_rows, frames_plan, scene_cuts, scene_sample_count, scene_segments, scene_slots


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


def _make_fade_video(path: Path, *, fps: int = 25, size=(64, 48)) -> Path:
    import av
    from PIL import Image

    container = av.open(str(path), mode="w")
    try:
        stream = container.add_stream("mpeg4", rate=fps)
        stream.width, stream.height = size
        stream.pix_fmt = "yuv420p"
        for index in range(8 * fps):
            moment = index / fps
            if moment < 3:
                value = 0
            elif moment >= 5:
                value = 255
            else:
                value = int((moment - 3) * 127.5)
            frame = av.VideoFrame.from_image(Image.new("RGB", size, (value, value, value)))
            for packet in stream.encode(frame):
                container.mux(packet)
        for packet in stream.encode():
            container.mux(packet)
    finally:
        container.close()
    return path


def test_scene_sample_count_is_bounded():
    assert scene_sample_count(600) == 240
    assert scene_sample_count(7200) == 240
    assert scene_sample_count(120) == 240
    assert scene_sample_count(0.4) == 2
    assert scene_sample_count(0.0) == 2


def test_scene_cuts_collapses_only_monotonic_gradual_runs():
    times = [0.5, 1.0, 1.5, 2.0, 2.5, 3.0]
    scores = [0.0, 0.0, 20.0, 20.0, 20.0, 0.0]
    assert scene_cuts(times, scores) == [1.5, 2.0, 2.5]
    assert scene_cuts(times, scores, [10.0, 20.0, 40.0, 80.0, 160.0, 200.0]) == [2.0]
    # Flat/non-monotonic consecutive candidates are not trusted transitions and have no
    # neighbourhood contrast, so the spike guard drops them instead of over-segmenting.
    assert scene_cuts(times, scores, [128.0] * 6) == []
    assert scene_cuts(times, scores, [10.0, 200.0, 20.0, 200.0, 20.0, 200.0]) == []
    # Two monotonic samples are not enough evidence for a gradual transition.
    pair_times = [0.5, 1.0, 1.5, 2.0]
    assert scene_cuts(pair_times, [0.0, 0.0, 20.0, 20.0], [10.0, 20.0, 40.0, 80.0]) == []


def test_worker_scenes_collapses_fade_to_one_transition(tmp_path):
    video = _make_fade_video(tmp_path / "fade.mp4")
    result = worker._frames(
        {
            "video": str(video),
            "sheet_output": str(tmp_path / "sheet.png"),
            "count": 3,
            "width": 320,
            "select": "scenes",
        }
    )
    assert result["frame_count"] == 3
    cuts = result["scene_cuts"]
    assert len(cuts) == 1 and 3.5 < cuts[0] < 4.5
    stamps = result["timestamps"]
    assert len(stamps) == 3 and stamps == sorted(stamps) and len(set(stamps)) == 3
    assert all(0.0 <= stamp <= 8.0 for stamp in stamps)
    assert any(stamp < cuts[0] for stamp in stamps) and any(stamp >= cuts[0] for stamp in stamps)
    assert (tmp_path / "sheet.png").is_file()


def test_worker_scenes_quick_cuts_stay_bounded_to_the_sample_grid(tmp_path):
    video = _make_scene_video(
        tmp_path / "quick.mp4",
        [(0, 3, (255, 0, 0)), (3, 3.2, (0, 0, 255)), (3.2, 8, (0, 255, 0))],
    )
    result = worker._frames(
        {
            "video": str(video),
            "sheet_output": str(tmp_path / "sheet.png"),
            "count": 2,
            "width": 320,
            "select": "scenes",
        }
    )
    assert result["frame_count"] == 2
    assert len(result["scene_cuts"]) <= 1
    assert result["timestamps"] == sorted(result["timestamps"])


def test_scene_job_cancel_then_delete_leaves_no_frames(tmp_path):
    from omni_test_support.v2.media import BV, FakeGateway, FakeProcessor, _media_instance, _seed
    from sqlalchemy import select

    from omni.adapters.persistence import schema as s

    video = _make_fade_video(tmp_path / "fade.mp4")

    class Gateway(FakeGateway):
        async def download(self, configuration, url, destination, **kwargs):
            destination.write_bytes(video.read_bytes())
            return {"bytes": destination.stat().st_size}

    class GatedProcessor(FakeProcessor):
        def __init__(self):
            super().__init__()
            self.entered = asyncio.Event()
            self.release = asyncio.Event()

        async def frames(self, video_path, sheet_output, count, width, timestamps=None, select="equidistant"):
            self.entered.set()
            await self.release.wait()
            return await super().frames(video_path, sheet_output, count, width, timestamps, select)

    async def scenario():
        processor = GatedProcessor()
        instance = await _media_instance(tmp_path / "instance", Gateway(), processor)
        try:
            agent, config = await _seed(instance)
            source = await instance.media.register(agent, str(config["id"]), BV)
            job = await instance.media.submit(
                agent, str(source["id"]), "frames", {"count": 3, "width": 320, "select": "scenes"}
            )
            async with asyncio.timeout(10):
                await processor.entered.wait()
            with pytest.raises(BusinessError) as caught:
                await instance.media.delete(agent, str(source["id"]))
            assert caught.value.code == "media_source_busy"
            cancelled = await instance.media.cancel(agent, str(job["id"]))
            assert cancelled["state"] == "cancelled"
            processor.release.set()
            resolved = await instance.media.job(agent, str(job["id"]))
            assert resolved["state"] == "cancelled"
            await instance.media.delete(agent, str(source["id"]))
            assert await instance._database.read(lambda u: u.connection.execute(select(s.media_frames)).all()) == []
        finally:
            processor.release.set()
            assert (await instance.close())["complete"]

    asyncio.run(scenario())


def _make_letterbox_video(
    path: Path,
    bars: int,
    first: tuple[int, int, int] = (100, 60, 50),
    second: tuple[int, int, int] = (60, 100, 90),
    *,
    fps: int = 25,
    size=(64, 48),
) -> Path:
    import av
    from PIL import Image

    container = av.open(str(path), mode="w")
    try:
        stream = container.add_stream("mpeg4", rate=fps)
        stream.width, stream.height = size
        stream.pix_fmt = "yuv420p"
        for index in range(8 * fps):
            color = first if index / fps < 4 else second
            image = Image.new("RGB", size, (0, 0, 0))
            inner = Image.new("RGB", (size[0], size[1] - 2 * bars), color)
            image.paste(inner, (0, bars))
            frame = av.VideoFrame.from_image(image)
            for packet in stream.encode(frame):
                container.mux(packet)
        for packet in stream.encode():
            container.mux(packet)
    finally:
        container.close()
    return path


def test_bar_rows_caps_and_stops_at_content():
    assert bar_rows([0.0, 0.0, 90.0, 0.0]) == 1  # 40% cap of four rows
    assert bar_rows([0.0, 0.0, 0.0, 90.0, 0.0, 0.0, 0.0, 0.0, 0.0, 0.0]) == 3
    assert bar_rows([0.0] * 48) == 19
    assert bar_rows([90.0, 0.0]) == 0
    assert bar_rows([]) == 0


def test_worker_scenes_crops_letterbox_bars_before_scoring(tmp_path):
    full = _make_letterbox_video(tmp_path / "full.mp4", 0)
    letterbox = _make_letterbox_video(tmp_path / "letterbox.mp4", 16)
    outputs = []
    for video in (full, letterbox):
        outputs.append(
            worker._frames(
                {
                    "video": str(video),
                    "sheet_output": str(tmp_path / f"{video.stem}.png"),
                    "count": 2,
                    "width": 320,
                    "select": "scenes",
                }
            )
        )
    assert len(outputs[0]["scene_cuts"]) == 1 and 3.5 < outputs[0]["scene_cuts"][0] < 4.8
    assert len(outputs[1]["scene_cuts"]) == 1 and 3.5 < outputs[1]["scene_cuts"][0] < 4.8
    assert outputs[1]["timestamps"] == sorted(outputs[1]["timestamps"])


def test_worker_scenes_all_black_source_stays_bounded(tmp_path):
    video = _make_letterbox_video(tmp_path / "black.mp4", 24, (0, 0, 0), (0, 0, 0))
    result = worker._frames(
        {
            "video": str(video),
            "sheet_output": str(tmp_path / "black.png"),
            "count": 3,
            "width": 320,
            "select": "scenes",
        }
    )
    assert result["frame_count"] == 3
    assert result["scene_cuts"] == []
    assert (tmp_path / "black.png").is_file()


def test_scene_cuts_keeps_isolated_spikes_and_drops_noise():
    times = [0.5 * index for index in range(12)]
    scores = [0.0, 3.0, 4.0, 13.0, 12.5, 13.0, 4.0, 3.0, 3.0, 3.0, 20.0, 3.0]
    assert scene_cuts(times, scores, [20.0] * 12) == [5.0]
    assert scene_cuts(times, scores) == [1.5, 2.0, 2.5, 5.0]


def _make_noise_video(path: Path, *, fps: int = 25, size=(64, 48)) -> Path:
    import random

    import av
    from PIL import Image

    rng = random.Random(11)
    noise = {index: rng.randint(-8, 8) for index in range(8 * fps + 1)}
    container = av.open(str(path), mode="w")
    try:
        stream = container.add_stream("mpeg4", rate=fps)
        stream.width, stream.height = size
        stream.pix_fmt = "yuv420p"
        for index in range(8 * fps):
            value = max(0, min(255, 20 + noise[index]))
            frame = av.VideoFrame.from_image(Image.new("RGB", size, (value, value, min(255, value + 5))))
            for packet in stream.encode(frame):
                container.mux(packet)
        for packet in stream.encode():
            container.mux(packet)
    finally:
        container.close()
    return path


def test_worker_scenes_suppresses_dark_noise_spikes(tmp_path):
    video = _make_noise_video(tmp_path / "noise.mp4")
    result = worker._frames(
        {
            "video": str(video),
            "sheet_output": str(tmp_path / "noise.png"),
            "count": 3,
            "width": 320,
            "select": "scenes",
        }
    )
    assert result["frame_count"] == 3
    assert result["scene_cuts"] == []
    assert result["timestamps"] == sorted(result["timestamps"])


def test_worker_scenes_keeps_one_second_montage_cuts(tmp_path):
    scenes = [(index, index + 1, (30 + index * 25, 60, 200 - index * 20)) for index in range(8)]
    video = _make_scene_video(tmp_path / "montage.mp4", scenes)
    result = worker._frames(
        {
            "video": str(video),
            "sheet_output": str(tmp_path / "montage.png"),
            "count": 6,
            "width": 320,
            "select": "scenes",
        }
    )
    assert result["frame_count"] == 6
    assert len(result["scene_cuts"]) >= 5
    assert result["timestamps"] == sorted(result["timestamps"])


def test_scene_cuts_gain_lifts_dark_isolated_cut_and_keeps_noise_down():
    times = [0.5, 1.0, 1.5, 2.0, 2.5]
    dark_cut = [0.0, 10.3, 0.0, 0.0, 0.0]
    assert scene_cuts(times, dark_cut) == []
    assert scene_cuts(times, dark_cut, [20.0] * 5) == [1.0]
    dark_noise = [0.0, 13.0, 12.3, 0.0, 0.0]
    assert scene_cuts(times, dark_noise, [20.0] * 5) == []


def _make_very_dark_cut_video(path: Path, *, fps: int = 25, size=(64, 48)) -> Path:
    import av
    from PIL import Image

    container = av.open(str(path), mode="w")
    try:
        stream = container.add_stream("mpeg4", rate=fps)
        stream.width, stream.height = size
        stream.pix_fmt = "yuv420p"
        for index in range(8 * fps):
            color = (8, 8, 16) if index / fps < 4 else (18, 18, 36)
            frame = av.VideoFrame.from_image(Image.new("RGB", size, color))
            for packet in stream.encode(frame):
                container.mux(packet)
        for packet in stream.encode():
            container.mux(packet)
    finally:
        container.close()
    return path


def test_worker_scenes_gain_detects_very_dark_cut(tmp_path):
    video = _make_very_dark_cut_video(tmp_path / "very-dark.mp4")
    result = worker._frames(
        {
            "video": str(video),
            "sheet_output": str(tmp_path / "very-dark.png"),
            "count": 2,
            "width": 320,
            "select": "scenes",
        }
    )
    assert result["frame_count"] == 2
    assert len(result["scene_cuts"]) == 1 and 3.5 < result["scene_cuts"][0] < 4.8
    assert result["timestamps"] == sorted(result["timestamps"])
