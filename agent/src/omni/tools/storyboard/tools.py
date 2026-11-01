"""分镜工具：把视频变成"能看、能引用、能回看"的画面索引。

两个工具（对照参考项目的 video_storyboard / video_zoom）：

- `video_storyboard`：全片 → **一张**宫格图（每格烧编号与时间码）+ 可寻址索引；
- `video_zoom`：按时间码回到原始流取一帧大图，给模型放大看某一格。

**与参考项目的三点不同**（我们能做得更好或更省的地方）：

1. **取流走我们自己的渠道**（B站 WBI 直链 / 本地文件），不引 ffmpeg + yt-dlp +
   cookies.txt——所以本域零新增外部依赖，也不碰用户的登录态文件；
2. **台词按段对齐而不是画音频包络**：单独出一张音频图要多花一份视觉 token，
   还丢了对齐；我们把台词直接挂到格子上，对齐关系一样在；
3. **如实报最小格尺寸**，让模型知道自己看不清什么、该不该去 zoom。

**为什么模型需要这个**：我们的多模态模型能看图，但给一堆散图它无法引用
「第 5 格」。一张带编号与时间码的宫格 + 一份机器可读索引，
才让"模型引用一次就闭环、人对着时间码能复核"成立。
"""
from __future__ import annotations

import tempfile
from pathlib import Path
from typing import Any

from ..registry import Risk, SessionRef, Tool, ToolRegistry
from ..runctx import RunRef
from ..shots import save_screenshot
from . import decode, detect, render, segments
from .source import VideoSource

# 格子数：格数是视觉预算，不是时长（静态单镜头给 16 格 = 16 张一样的图）
DEFAULT_CELLS = 12
MAX_CELLS = 16
# 取帧宽度：宫格格宽 320，源帧取宽一点，裁切时更经得起看
FRAME_WIDTH = 640
# 放大单格的宽度
ZOOM_WIDTH = 1280
# 台词回退长度（每格取前 N 字挂进索引）
MAX_CELL_TEXT = 120


def register_storyboard_tools(
    registry: ToolRegistry,
    *,
    source: VideoSource | None = None,
    artifacts: Any = None,
    session_ref: SessionRef | None = None,
    run_ref: RunRef | None = None,
    can_view: bool = False,
    cache_root: Path | None = None,
) -> None:
    """注册分镜工具。

    `source` 是平台相关的取流适配器（由 bootstrap 注入）。
    传 None 时工具仍在册，但调用会明确报错说"没装配视频来源"——
    比起悄悄不注册，模型能知道该换条路。
    """

    def _artifact_context() -> tuple[int | None, str | None]:
        session_id = session_ref.get() if session_ref is not None else None
        run = run_ref.get() if run_ref is not None else None
        run_id = run.run_id if run is not None else None
        return session_id, run_id

    def _temporary_dir(prefix: str) -> tempfile.TemporaryDirectory:
        root = cache_root / "video" if cache_root is not None else None
        if root is not None:
            root.mkdir(parents=True, exist_ok=True)
        return tempfile.TemporaryDirectory(prefix=prefix, dir=root)

    def _storyboard(args: dict[str, Any]) -> str:
        if source is None:
            return "错误：视频来源未装配（video_storyboard 需要一个可用的取流适配器）。"
        target = str(args["target"]).strip()
        if not target:
            return "错误：缺少 target（视频标识或链接）。"
        try:
            cells = int(args.get("cells") or DEFAULT_CELLS)
        except (TypeError, ValueError):
            cells = DEFAULT_CELLS
        cells = max(1, min(cells, MAX_CELLS))
        want_transcript = bool(args.get("with_transcript", False))

        transcript: list[tuple[float, str]] = []
        if want_transcript:
            try:
                transcript = source.transcript(target) or []
            except Exception as exc:  # noqa: BLE001 —— 台词拿不到不该让画面也没了
                transcript = []
                transcript_error = str(exc)
            else:
                transcript_error = ""
        else:
            transcript_error = ""

        with _temporary_dir("omni-storyboard-") as tmp:
            video = Path(tmp) / "video.mp4"
            source.fetch(target, video)
            times, scores, duration = decode.scan_cut_scores(video)
            if not scores:
                return "没能从视频里解出画面（可能是纯音频流，或文件已损坏）。"
            cuts = detect.detect_cuts_from_scores(times, scores, duration=duration)
            board = segments.build_storyboard(
                cuts, duration=duration, max_cells=cells, transcript=transcript,
            )
            picked = decode.pick_sharp_frames(
                video,
                [(cell.index, cell.start, cell.end) for cell in board.cells],
                width=FRAME_WIDTH,
            )
            if not picked:
                return "没能从视频里取到画面帧（解码失败）。"
            board = segments.build_storyboard(
                cuts, duration=duration, max_cells=cells, transcript=transcript,
                frame_times=[(index, moment, sharp)
                             for index, moment, _png, sharp in picked],
            )
            result = render.render_storyboard(
                board, [(index, png) for index, _moment, png, _sharp in picked],
            )

        session_id, run_id = _artifact_context()
        text = save_screenshot(
            artifacts, result.png, session_id=session_id, run_id=run_id,
            kind="video_storyboard", source="video_storyboard", can_view=can_view,
            label="视频分镜图",
        )
        lines = [
            text,
            render.readability_note(result),
            f"共 {len(board.cells)} 格 · 视频时长 {render.format_time(duration)} · "
            f"检出 {len(cuts)} 个剪辑点。",
            "分镜索引（格号 → 时间码 → 取帧时刻 → 该段台词）：",
        ]
        for row in board.to_index():
            snippet = str(row["text"] or "")
            if len(snippet) > MAX_CELL_TEXT:
                snippet = snippet[: MAX_CELL_TEXT - 1] + "…"
            lines.append(
                f"#{row['cell']} {render.format_time(row['from'])}-{render.format_time(row['to'])}"
                f"｜取帧 {render.format_time(row['frame_time'])}"
                f"｜{snippet or '（无台词）'}"
            )
        if transcript_error:
            lines.append(f"（台词没取到，画面不受影响：{transcript_error}）")
        if not can_view:
            lines.append("注意：当前没有可用的视觉模型，这张图不会自动进入对话。")
        return "\n".join(lines)

    def _zoom(args: dict[str, Any]) -> str:
        if source is None:
            return "错误：视频来源未装配（video_zoom 需要一个可用的取流适配器）。"
        target = str(args["target"]).strip()
        try:
            moment = float(args.get("at"))
        except (TypeError, ValueError):
            return "错误：缺少 at（要放大的时间点，秒）。"
        try:
            width = int(args.get("width") or ZOOM_WIDTH)
        except (TypeError, ValueError):
            width = ZOOM_WIDTH
        width = max(320, min(width, 1920))
        with _temporary_dir("omni-zoom-") as tmp:
            video = Path(tmp) / "video.mp4"
            source.fetch(target, video)
            actual, png = decode.frame_at(video, moment, width=width)
        session_id, run_id = _artifact_context()
        text = save_screenshot(
            artifacts, png, session_id=session_id, run_id=run_id,
            kind="video_zoom", source="video_zoom", can_view=can_view,
            label="视频单帧放大",
        )
        return (
            f"{text}\n实际取到 {render.format_time(actual)}"
            f"（请求 {moment:.2f} 秒）。"
        )

    registry.register(
        Tool(
            name="video_storyboard",
            description=(
                "把一个视频做成**一张**分镜图存为产物 [artifact:id]："
                "按镜头切分，每个镜头一格，格上烧了编号与时间码，"
                "返回的索引能让你精确引用「第几格是第几分几秒」，"
                "也能让人对着时间码回看原片复核。"
                "适合需要看**画面**（谁在讲、屏幕上有什么、画面怎么变）的场景；"
                "纯文字内容仍建议配合字幕/转写工具。"
                "要看清某一格的细节，用 video_zoom 按该格的取帧时刻放大。"
            ),
            parameters={
                "type": "object",
                "properties": {
                    "target": {"type": "string", "description": "视频标识或链接"},
                    "cells": {"type": "integer",
                              "description": f"最多分几格（1-{MAX_CELLS}，默认{DEFAULT_CELLS}）"},
                    "with_transcript": {"type": "boolean",
                                        "description": "是否把台词挂到各格（默认 false；开启会额外跑一次语音转写，较慢）"},
                },
                "required": ["target"],
            },
            func=_storyboard,
            toolset="video",
            risk=Risk.SAFE,
            read_only=True,
        )
    )
    registry.register(
        Tool(
            name="video_zoom",
            description=(
                "取视频某一时刻的单帧大图存为产物 [artifact:id]，用于放大看分镜图里某一格的细节。"
                "时间点取 video_storyboard 返回索引里的「取帧」时刻。"
            ),
            parameters={
                "type": "object",
                "properties": {
                    "target": {"type": "string", "description": "视频标识或链接"},
                    "at": {"type": "number", "description": "要放大的时间点（秒）"},
                    "width": {"type": "integer",
                              "description": f"单帧宽度（320-1920，默认{ZOOM_WIDTH}）"},
                },
                "required": ["target", "at"],
            },
            func=_zoom,
            toolset="video",
            risk=Risk.SAFE,
            read_only=True,
        )
    )
