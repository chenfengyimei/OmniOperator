"""分镜（视频理解）单测：切点检测 → 分段 → 台词 → 取帧 → 宫格 → 可寻址索引。

全部离线：帧是现场合成的，视频是现场用 PyAV 造的（mpeg4 编码，不联网、不碰真实视频）。

守住五件事：
1. **切点别漏也别多**——七个合成场景（纯色分段 / 同亮度换色 / 静止 / 暗场硬切 /
   快剪 / 慢速 / 渐变）逐个给出期望刀数；
2. **渐变不能被当成剪辑**——它是连续高原，不是突变；
3. **格子必须覆盖整片**——曾经有一次"把格起点吸附到切点"的逻辑把开头 2 秒吃掉了；
4. **索引必须可复核**——格号能反查绝对时间，取帧时刻必须落在该格内；
5. **如实报最小格尺寸**——不报平均。
"""

from __future__ import annotations

import io
from pathlib import Path
from types import SimpleNamespace

import pytest

from omni.tools.storyboard import decode, detect, render, segments
from omni.tools.storyboard.detect import Cut
from omni.tools.storyboard.segments import Cell, Storyboard


# ---------------------------------------------------------------- 合成帧


def _frame(color: tuple[int, int, int], width: int = detect.SCAN_WIDTH, height: int = detect.SCAN_HEIGHT) -> bytes:
    """一帧纯色 RGB888 字节串。"""
    return bytes(color) * (width * height)


def _shots(shots: list[tuple[float, tuple[int, int, int]]], fps: int = 10) -> list[tuple[float, bytes]]:
    """[(时长秒, 颜色)] → [(时间秒, RGB 字节)]。"""
    frames: list[tuple[float, bytes]] = []
    time = 0.0
    for seconds, color in shots:
        for _ in range(int(round(seconds * fps))):
            frames.append((round(time, 4), _frame(color)))
            time += 1.0 / fps
    return frames


def _gradient(frames: int = 100, fps: int = 10) -> list[tuple[float, bytes]]:
    """缓慢渐变（红 → 蓝）：每帧只变一点点，但一路连到片尾。"""
    result: list[tuple[float, bytes]] = []
    for index in range(frames):
        ratio = index / max(1, frames - 1)
        red = int(200 - 170 * ratio)
        blue = int(30 + 170 * ratio)
        result.append((round(index / fps, 4), _frame((red, 30, blue))))
    return result


# ------------------------------------------------------------ 切点检测


def test_detect_cuts_finds_every_cut_in_color_sequence():
    """6 个纯色镜头 → 5 刀，位置要对。"""
    frames = _shots(
        [
            (2.0, (220, 30, 30)),
            (2.0, (30, 220, 30)),
            (2.0, (30, 30, 220)),
            (2.0, (230, 220, 40)),
            (2.0, (220, 30, 220)),
            (2.0, (240, 240, 240)),
        ]
    )
    cuts = detect.detect_cuts(frames, duration=12.0)
    assert len(cuts) == 5
    assert [round(c.time, 1) for c in cuts] == [2.0, 4.0, 6.0, 8.0, 10.0]


def test_detect_cuts_sees_same_luminance_color_change():
    """同亮度换色：只看亮度差会漏掉，HSV 三通道差能看见。"""
    frames = _shots([(2.0, (120, 60, 60)), (2.0, (60, 120, 60))])
    cuts = detect.detect_cuts(frames, duration=4.0)
    assert len(cuts) == 1
    assert abs(cuts[0].time - 2.0) < 0.2


def test_detect_cuts_ignores_static_material():
    """整片不动 → 一刀都不该有。"""
    frames = _shots([(6.0, (90, 90, 90))])
    assert detect.detect_cuts(frames, duration=6.0) == []


def test_detect_cuts_finds_cut_in_dark_scene():
    """暗场硬切：绝对差小，但亮度归一后要能把分数拉回同一把尺子。"""
    frames = _shots([(2.0, (30, 30, 35)), (2.0, (30, 30, 120))])
    cuts = detect.detect_cuts(frames, duration=4.0)
    assert len(cuts) == 1
    assert abs(cuts[0].time - 2.0) < 0.2


def test_detect_cuts_handles_fast_cut():
    """快剪 12 镜（0.16 秒一刀）：每一刀都合法，不能被间隔抑制吃掉。"""
    palette = [(30, 30, 225), (225, 30, 30), (30, 225, 30), (225, 225, 30)]
    shots = [(0.16, palette[index % len(palette)]) for index in range(12)]
    frames = _shots(shots, fps=25)
    cuts = detect.detect_cuts(frames, duration=12 * 0.16)
    assert len(cuts) == 11


def test_detect_cuts_handles_slow_cut():
    """慢速 6 镜：刀少但不能漏。"""
    frames = _shots(
        [
            (3.0, (200, 40, 40)),
            (3.0, (40, 200, 40)),
            (3.0, (40, 40, 200)),
            (3.0, (200, 200, 40)),
            (3.0, (200, 40, 200)),
            (3.0, (60, 60, 60)),
        ]
    )
    cuts = detect.detect_cuts(frames, duration=18.0)
    assert len(cuts) == 5


def test_detect_cuts_does_not_report_gradient_as_cuts():
    """渐变是连续高原不是突变——这是最容易被误报的场景。"""
    frames = _gradient()
    assert detect.detect_cuts(frames, duration=10.0) == []


def test_detect_cuts_from_scores_matches_batch_path():
    """流式打分与一次性打分必须给同一批切点（两条路径不能分叉）。"""
    frames = _shots(
        [
            (2.0, (220, 30, 30)),
            (2.0, (30, 220, 30)),
            (2.0, (30, 30, 220)),
            (2.0, (230, 220, 40)),
        ]
    )
    batch = detect.detect_cuts(frames, duration=8.0)
    times = [time for time, _ in frames][1:]
    scores = detect.score_frame_pairs(frames)
    streamed = detect.detect_cuts_from_scores(times, scores, duration=8.0)
    assert [round(c.time, 4) for c in batch] == [round(c.time, 4) for c in streamed]


def test_numpy_path_matches_python_path():
    """numpy 快速通道与纯 Python 保底通道必须给同样的结果（数值不能分叉）。"""
    pixels = bytes()
    for index in range(detect.SCAN_WIDTH * detect.SCAN_HEIGHT):
        pixels += bytes(
            (
                (index * 37) % 256,
                (index * 91) % 256,
                (index * 13) % 256,
            )
        )
    numpy_planes = detect.rgb_to_hsv_plane(pixels)
    python_planes = detect._rgb_to_hsv_plane_python(pixels)
    assert numpy_planes[0] == python_planes[0]
    assert numpy_planes[1] == python_planes[1]
    assert numpy_planes[2] == python_planes[2]

    other = bytes(
        value
        for index in range(detect.SCAN_WIDTH * detect.SCAN_HEIGHT)
        for value in (
            (index * 53 + 7) % 256,
            (index * 29) % 256,
            (index * 71) % 256,
        )
    )
    other_planes = detect.rgb_to_hsv_plane(other)
    numpy_diff = detect.hsv_difference(numpy_planes, other_planes)
    python_diff = detect._hsv_difference_python(python_planes, other_planes)
    assert numpy_diff == pytest.approx(python_diff, rel=1e-9)


# ---------------------------------------------------------------- 分段


def test_segments_cover_the_whole_video():
    """格子必须从 0 覆盖到片尾——曾经有一次吸附逻辑把开头 2 秒吃掉了。"""
    cuts = [Cut(time=2.0, score=80.0), Cut(time=4.0, score=80.0), Cut(time=6.0, score=80.0)]
    planned = segments.plan_segments(cuts, duration=8.0, max_cells=12)
    assert planned[0].start == 0.0
    assert planned[-1].end == pytest.approx(8.0)
    for earlier, later in zip(planned, planned[1:]):
        assert later.start == pytest.approx(earlier.end)


def test_segment_boundaries_land_on_real_cuts():
    """格边界应落在真实切点上——每格才是一个完整镜头。"""
    cuts = [Cut(time=float(index * 2), score=90.0) for index in range(1, 6)]
    planned = segments.plan_segments(cuts, duration=12.0, max_cells=12)
    cut_times = {cut.time for cut in cuts}
    for segment in planned[1:]:
        assert segment.start in cut_times


def test_dense_region_gets_more_cells_than_sparse_region():
    """「6 秒 20 刀 + 20 秒空镜」：快剪那边格子要多，空镜那边格子要长。"""
    palette = [(220, 40, 40), (40, 220, 40), (40, 40, 220), (220, 220, 40)]
    shots = [(0.3, palette[index % len(palette)]) for index in range(20)]
    shots.append((20.0, (80, 80, 80)))
    frames = _shots(shots, fps=25)
    cuts = detect.detect_cuts(frames, duration=6.0 + 20.0)
    planned = segments.plan_segments(cuts, duration=26.0, max_cells=12)
    assert len(planned) <= 12
    early = sum(1 for segment in planned if segment.end <= 6.0)
    assert early >= 8, f"快剪区只分了 {early} 格，密度分配没生效"


def test_plan_segments_respects_max_cells():
    cuts = [Cut(time=float(index), score=90.0) for index in range(1, 30)]
    planned = segments.plan_segments(cuts, duration=30.0, max_cells=8)
    assert len(planned) <= 8


def test_short_cells_are_merged_not_split():
    """素材粒度太细时，宁可并格也不能切出比素材还碎的格。"""
    cuts = [Cut(time=0.2 * index, score=90.0) for index in range(1, 10)]
    planned = segments.plan_segments(cuts, duration=2.0, max_cells=12, min_cell_gap=1.0)
    for segment in planned:
        assert segment.duration >= 0.15


def test_single_cut_video_still_gets_a_cell():
    planned = segments.plan_segments([], duration=10.0, max_cells=12)
    assert len(planned) == 1
    assert planned[0].start == 0.0
    assert planned[0].end == pytest.approx(10.0)


# ---------------------------------------------------------------- 台词


def test_transcript_attaches_to_the_cell_containing_its_start():
    cuts = [Cut(time=2.0, score=90.0), Cut(time=4.0, score=90.0)]
    planned = segments.plan_segments(cuts, duration=6.0, max_cells=12)
    lines = segments.attach_transcript(
        planned,
        [
            (0.5, "开场白"),
            (2.5, "第二段"),
            (5.0, "第三段"),
        ],
    )
    assert lines[0] == ["开场白"]
    assert lines[1] == ["第二段"]
    assert lines[2] == ["第三段"]


def test_transcript_is_optional_and_blank_entries_are_dropped():
    planned = segments.plan_segments([], duration=4.0, max_cells=12)
    assert segments.attach_transcript(planned, []) == [[]]
    assert segments.attach_transcript(planned, [(0.0, "   ")]) == [[]]


# ---------------------------------------------------------------- 索引


def test_index_row_is_machine_readable():
    """索引是给人和脚本复核用的：格号 + 绝对时间 + 帧时刻 + 字数 + 原文。"""
    cuts = [Cut(time=2.0, score=90.0), Cut(time=4.0, score=90.0)]
    board = segments.build_storyboard(
        cuts,
        duration=6.0,
        max_cells=12,
        transcript=[(0.5, "hello")],
        frame_times=[(1, 1.0, 12.5)],
    )
    row = board.to_index()[0]
    assert set(row) == {"cell", "from", "to", "frame_time", "chars", "text"}
    assert row["cell"] == 1
    assert row["from"] == 0.0
    assert row["text"] == "hello"
    assert row["chars"] == 5
    assert row["frame_time"] == 1.0


def test_cell_lookup_by_index():
    cell = Cell(index=3, start=1.0, end=2.0, frame_time=1.5)
    board = Storyboard(cells=[cell], duration=2.0)
    assert board.cell_by_index(3) is cell
    assert board.cell_by_index(9) is None


def test_cell_mid_is_inside_the_cell():
    cell = Cell(index=1, start=2.0, end=6.0, frame_time=4.0)
    assert cell.mid == pytest.approx(4.0)
    assert cell.duration == pytest.approx(4.0)


# ---------------------------------------------------------------- 渲染


def _board_with_images(cells: int = 3, transcript: bool = True) -> Storyboard:
    board_cells = []
    for index in range(1, cells + 1):
        board_cells.append(
            Cell(
                index=index,
                start=float(index - 1),
                end=float(index),
                frame_time=index - 0.5,
                lines=("台词内容",) if transcript else (),
            )
        )
    return Storyboard(
        cells=board_cells,
        duration=float(cells),
        transcript_chars=cells * 4 if transcript else 0,
    )


def _png(color: tuple[int, int, int] = (200, 40, 40), size=(320, 180)) -> bytes:
    from PIL import Image

    buffer = io.BytesIO()
    Image.new("RGB", size, color).save(buffer, format="PNG")
    return buffer.getvalue()


def test_render_produces_one_png_grid():
    board = _board_with_images(4)
    result = render.render_storyboard(board, [(cell.index, _png()) for cell in board.cells])
    assert result.png[:8] == b"\x89PNG\r\n\x1a\n"
    assert result.columns == 4
    assert result.rows == 1
    assert result.width == 4 * (render.CELL_WIDTH + render.PADDING) + render.PADDING


def test_render_reports_smallest_cell_not_average():
    """如实报最小格：模型该知道自己看不清什么。"""
    board = _board_with_images(3)
    result = render.render_storyboard(board, [(cell.index, _png()) for cell in board.cells])
    assert result.smallest_cell_px == (render.CELL_WIDTH, result.cell_height)
    note = render.readability_note(result)
    assert "每格" in note


def test_render_adds_transcript_band_only_when_there_is_transcript():
    board = _board_with_images(2, transcript=True)
    with_band = render.render_storyboard(board, [(c.index, _png()) for c in board.cells])
    plain = _board_with_images(2, transcript=False)
    without_band = render.render_storyboard(plain, [(c.index, _png()) for c in plain.cells])
    assert with_band.height > without_band.height


def test_render_skips_cells_without_frames_instead_of_inventing_them():
    board = _board_with_images(3)
    result = render.render_storyboard(board, [(1, _png())])
    assert result.columns == 1
    assert result.rows == 1


def test_render_rejects_empty_input():
    with pytest.raises(ValueError):
        render.render_storyboard(Storyboard(), [])


def test_format_time_is_minute_second():
    assert render.format_time(0) == "00:00"
    assert render.format_time(65) == "01:05"
    assert render.format_time(-3) == "00:00"


# ------------------------------------------------------------ 解码端到端


def _make_video(
    path: Path, shots: list[tuple[float, tuple[int, int, int]]], fps: int = 10, size=(320, 240), textured: int = -1
) -> Path:
    """用 PyAV 现场造一段视频（mpeg4 编码，纯本地）。"""
    import av
    from PIL import Image, ImageDraw

    container = av.open(str(path), mode="w")
    stream = container.add_stream("mpeg4", rate=fps)
    stream.width, stream.height = size
    stream.pix_fmt = "yuv420p"
    for index, (seconds, color) in enumerate(shots):
        for _ in range(int(round(seconds * fps))):
            image = Image.new("RGB", size, color)
            if index == textured:
                draw = ImageDraw.Draw(image)
                for x in range(0, size[0], 6):
                    draw.line([(x, 0), (x, size[1])], fill=(255, 255, 255), width=2)
            frame = av.VideoFrame.from_image(image)
            for packet in stream.encode(frame):
                container.mux(packet)
    for packet in stream.encode():
        container.mux(packet)
    container.close()
    return path


def test_end_to_end_storyboard_from_synthetic_video(tmp_path):
    """全链路：视频 → 切点 → 分段 → 台词 → 取帧 → 宫格 → 可寻址索引。"""
    video = _make_video(
        tmp_path / "clip.mp4",
        [
            (2.0, (220, 30, 30)),
            (2.0, (30, 220, 30)),
            (2.0, (30, 30, 220)),
            (2.0, (230, 220, 40)),
            (2.0, (240, 240, 240)),
        ],
    )
    duration, fps, width, height = decode.probe_video(video)
    assert duration == pytest.approx(10.0, abs=0.2)
    assert fps == pytest.approx(10.0, abs=0.5)
    assert (width, height) == (320, 240)

    times, scores, stream_duration = decode.scan_cut_scores(video)
    assert len(times) == len(scores)
    assert stream_duration == pytest.approx(10.0, abs=0.2)

    cuts = detect.detect_cuts_from_scores(times, scores, duration=stream_duration)
    assert len(cuts) == 4

    board = segments.build_storyboard(
        cuts,
        duration=stream_duration,
        max_cells=12,
        transcript=[(0.5, "第一句"), (4.5, "第三句")],
    )
    assert len(board.cells) == 5
    assert board.cells[0].start == 0.0
    assert board.cells[-1].end == pytest.approx(stream_duration, abs=0.2)

    picked = decode.pick_sharp_frames(video, [(cell.index, cell.start, cell.end) for cell in board.cells], width=320)
    assert len(picked) == 5
    for index, timestamp, png, _sharp in picked:
        cell = board.cell_by_index(index)
        assert cell is not None
        # 取帧时刻必须落在该格内，否则索引指向了别处
        assert cell.start - 0.01 <= timestamp <= cell.end + 0.01
        assert png[:8] == b"\x89PNG\r\n\x1a\n"

    result = render.render_storyboard(board, [(index, png) for index, _t, png, _s in picked])
    assert result.png[:8] == b"\x89PNG\r\n\x1a\n"
    assert result.rows == 2
    assert result.columns == 4


def test_pick_sharp_frames_prefers_the_textured_frame(tmp_path):
    """段内取 3 候选比 Laplacian 方差取最锐：糊的那几帧要被跳过。"""
    video = _make_video(
        tmp_path / "clip.mp4",
        [
            (2.0, (40, 40, 40)),
            (2.0, (40, 40, 40)),
            (2.0, (40, 40, 40)),
        ],
        textured=1,
    )
    picked = decode.pick_sharp_frames(video, [(1, 0.0, 6.0)], width=320)
    assert picked, "没取到任何帧"
    _index, timestamp, _png, sharpness = picked[0]
    assert sharpness > 0, "清晰度分全是 0，纹理镜头没被认出来"
    assert 2.0 <= timestamp <= 4.0, f"取到的帧 {timestamp} 不在纹理镜头内"


def test_frame_at_returns_the_nearest_moment(tmp_path):
    video = _make_video(tmp_path / "clip.mp4", [(4.0, (90, 90, 90))])
    timestamp, png = decode.frame_at(video, 2.0, width=320)
    assert abs(timestamp - 2.0) < 0.2
    assert png[:8] == b"\x89PNG\r\n\x1a\n"


def test_decode_rejects_file_without_video_track(tmp_path):
    """坏输入要给可解释的错误，不能崩。"""
    broken = tmp_path / "broken.mp4"
    broken.write_bytes(b"not a video at all")
    with pytest.raises(decode.VideoDecodeError):
        decode.probe_video(broken)


# ---------------------------------------------------------------- 工具层


class _FakeArtifacts:
    """产物库替身：只登记不落盘（离线测试不碰真实产物目录）。"""

    def __init__(self) -> None:
        self.saved: list[dict[str, object]] = []

    def save_image(
        self,
        data: bytes,
        mime: str,
        *,
        session_id: int | None,
        run_id: str | None,
        scope: str = "private",
        kind: str = "screenshot",
        source: str = "",
        width: int | None = None,
        height: int | None = None,
    ):
        self.saved.append(
            {
                "data": data,
                "mime": mime,
                "kind": kind,
                "source": source,
                "width": width,
                "height": height,
            }
        )
        return SimpleNamespace(artifact_id=f"fake{len(self.saved):04d}")


class _FakeSource:
    """本地视频源：把测试造好的视频当成"下载"结果。"""

    def __init__(self, video: Path, transcript=None) -> None:
        self.video = video
        self._transcript = list(transcript or [])
        self.fetched: list[str] = []

    def fetch(self, target: str, destination: Path) -> Path:
        self.fetched.append(target)
        destination.write_bytes(self.video.read_bytes())
        return destination

    def transcript(self, target: str) -> list[tuple[float, str]]:
        return list(self._transcript)


def _registry_with(source, artifacts, *, can_view: bool = True):
    from omni.tools.registry import ToolRegistry
    from omni.tools.storyboard.tools import register_storyboard_tools

    registry = ToolRegistry()
    register_storyboard_tools(
        registry,
        source=source,
        artifacts=artifacts,
        can_view=can_view,
    )
    return registry


def test_video_storyboard_tool_returns_index_and_artifact(tmp_path):
    """工具契约：一张图 + 一份能引用的索引 + 产物登记。"""
    video = _make_video(
        tmp_path / "clip.mp4",
        [
            (2.0, (220, 30, 30)),
            (2.0, (30, 220, 30)),
            (2.0, (30, 30, 220)),
            (2.0, (230, 220, 40)),
        ],
    )
    source = _FakeSource(video, [(0.5, "开场白"), (3.0, "第二段")])
    artifacts = _FakeArtifacts()
    registry = _registry_with(source, artifacts)
    tool = registry.get("video_storyboard")
    assert tool is not None

    result = tool.func({"target": "BV1xx411c7mD", "cells": 6, "with_transcript": True})

    assert "[artifact:fake0001]" in result
    assert "分镜索引" in result
    assert "#1" in result and "取帧" in result
    assert "开场白" in result
    assert source.fetched == ["BV1xx411c7mD"]
    saved = artifacts.saved[0]
    assert saved["kind"] == "video_storyboard"
    assert saved["mime"] == "image/png"
    assert saved["data"][:8] == b"\x89PNG\r\n\x1a\n"
    assert saved["width"] and saved["height"]


def test_video_storyboard_tool_without_transcript_is_faster(tmp_path):
    """默认不跑 ASR：台词是可选的，画面不该被它拖住。"""
    video = _make_video(tmp_path / "clip.mp4", [(2.0, (200, 40, 40))])
    source = _FakeSource(video, [(0.5, "不该出现")])
    registry = _registry_with(source, _FakeArtifacts())
    tool = registry.get("video_storyboard")
    result = tool.func({"target": "BV1xx411c7mD"})
    assert "不该出现" not in result


def test_video_storyboard_tool_survives_transcript_failure(tmp_path):
    """台词挂了不该把画面也拖没——画面才是这个工具的主业。"""
    video = _make_video(tmp_path / "clip.mp4", [(2.0, (200, 40, 40))])

    class _BrokenSource(_FakeSource):
        def transcript(self, target: str):
            raise RuntimeError("ASR 模型没下载")

    source = _BrokenSource(video)
    registry = _registry_with(source, _FakeArtifacts())
    tool = registry.get("video_storyboard")
    result = tool.func({"target": "BV1xx411c7mD", "with_transcript": True})
    assert "[artifact:" in result
    assert "ASR 模型没下载" in result


def test_video_storyboard_tool_reports_missing_source():
    """没装配视频来源时要说清楚，而不是崩。"""
    from omni.tools.registry import ToolRegistry
    from omni.tools.storyboard.tools import register_storyboard_tools

    registry = ToolRegistry()
    register_storyboard_tools(registry, source=None, artifacts=_FakeArtifacts())
    tool = registry.get("video_storyboard")
    result = tool.func({"target": "BV1xx411c7mD"})
    assert "视频来源未装配" in result


def test_video_zoom_tool_returns_a_frame(tmp_path):
    video = _make_video(tmp_path / "clip.mp4", [(4.0, (90, 90, 90))])
    source = _FakeSource(video)
    artifacts = _FakeArtifacts()
    registry = _registry_with(source, artifacts)
    tool = registry.get("video_zoom")
    result = tool.func({"target": "BV1xx411c7mD", "at": 2.0})
    assert "[artifact:fake0001]" in result
    assert artifacts.saved[0]["kind"] == "video_zoom"


def test_video_zoom_tool_requires_a_timestamp():
    registry = _registry_with(_FakeSource(Path("x")), _FakeArtifacts())
    tool = registry.get("video_zoom")
    result = tool.func({"target": "BV1xx411c7mD"})
    assert "缺少 at" in result


def test_storyboard_tools_are_registered_under_video_toolset():
    registry = _registry_with(_FakeSource(Path("x")), _FakeArtifacts())
    assert set(registry.names(toolsets={"video"})) == {"video_storyboard", "video_zoom"}
    for name in ("video_storyboard", "video_zoom"):
        tool = registry.get(name)
        assert tool is not None
        assert tool.toolset == "video"
        assert tool.read_only is True
