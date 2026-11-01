"""抽帧拼图单测：用 PyAV 现场造一段短片，不联网、不碰真实视频。

重点守住三件事：抽出来的帧数对、宽度按约定缩放、拼出来的是**一张** PNG
（多模态模型一次看完的前提）。
"""
from __future__ import annotations

from pathlib import Path

import pytest

from omni.tools.bilibili import frames


def _make_video(path: Path, *, seconds: int = 4, fps: int = 10,
                size: tuple[int, int] = (320, 240)) -> Path:
    """造一段纯色短片（每帧颜色不同，便于确认抽到的是不同时间点）。"""
    import av
    from PIL import Image

    container = av.open(str(path), mode="w")
    stream = container.add_stream("mpeg4", rate=fps)
    stream.width, stream.height = size
    stream.pix_fmt = "yuv420p"
    for index in range(seconds * fps):
        frame = av.VideoFrame.from_image(Image.new("RGB", size, (index % 255, 30, 200)))
        for packet in stream.encode(frame):
            container.mux(packet)
    for packet in stream.encode():
        container.mux(packet)
    container.close()
    return path


def test_target_times_avoid_head_and_tail():
    times = frames.target_times(100.0, 4)
    assert len(times) == 4
    assert times[0] > 1.0
    assert times[-1] < 99.0
    assert times == sorted(times)


def test_extract_frames_returns_requested_count(tmp_path):
    video = _make_video(tmp_path / "clip.mp4", seconds=4)
    picked = frames.extract_frames(video, count=4, width=320)
    assert len(picked) == 4
    for _timestamp, data in picked:
        assert data[:8] == b"\x89PNG\r\n\x1a\n"
    # 时间戳应递增（抽帧按时间顺序排列）
    stamps = [timestamp for timestamp, _ in picked]
    assert stamps == sorted(stamps)


def test_extract_frames_scales_to_requested_width(tmp_path):
    from PIL import Image
    import io

    video = _make_video(tmp_path / "clip.mp4", seconds=3)
    picked = frames.extract_frames(video, count=2, width=480)
    assert picked
    with Image.open(io.BytesIO(picked[0][1])) as image:
        assert image.width == 480
        assert image.height == 360  # 16:10 保持比例


def test_extract_frames_caps_count_and_width(tmp_path):
    video = _make_video(tmp_path / "clip.mp4", seconds=2)
    picked = frames.extract_frames(video, count=99, width=10)
    assert len(picked) <= frames.MAX_FRAMES
    from PIL import Image
    import io

    with Image.open(io.BytesIO(picked[0][1])) as image:
        assert image.width == frames.MIN_WIDTH


def test_contact_sheet_is_one_png_with_labels(tmp_path):
    from PIL import Image
    import io

    video = _make_video(tmp_path / "clip.mp4", seconds=3)
    picked = frames.extract_frames(video, count=4, width=320)
    sheet = frames.contact_sheet(picked)
    assert sheet[:8] == b"\x89PNG\r\n\x1a\n"
    with Image.open(io.BytesIO(sheet)) as image:
        assert image.width == 640          # 两列
        assert image.height > 240          # 至少两行 + 标签条
        assert len(picked) == 4


def test_contact_sheet_rejects_empty_frames():
    with pytest.raises(RuntimeError):
        frames.contact_sheet([])


def test_format_time_is_minute_second():
    assert frames.format_time(0) == "00:00"
    assert frames.format_time(65) == "01:05"
    assert frames.format_time(-3) == "00:00"
