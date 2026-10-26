"""B站读工具集（toolset=bilibili，全部 SAFE 只读）。

包装原则：只做参数校验 + 结果排版；业务与 HTTP 细节在 api.py。
"""
from __future__ import annotations

import datetime as dt
from typing import Any

from ..registry import Risk, Tool, ToolRegistry
from .api import BiliAPI


def register_bilibili_tools(registry: ToolRegistry, api: BiliAPI) -> None:

    def _fmt_duration(seconds: Any) -> str:
        try:
            total = int(seconds)
        except (TypeError, ValueError):
            return "未知"
        hours, rem = divmod(total, 3600)
        minutes, secs = divmod(rem, 60)
        return f"{hours}:{minutes:02d}:{secs:02d}" if hours else f"{minutes}:{secs:02d}"

    def _fmt_date(ts: Any) -> str:
        try:
            return dt.datetime.fromtimestamp(int(ts)).astimezone().strftime("%Y-%m-%d")
        except (TypeError, ValueError, OSError, OverflowError):
            return "未知"

    def video_info(args: dict[str, Any]) -> str:
        info = api.video_info(str(args["target"]))
        stat = info["stat"]
        return "\n".join(
            [
                f"标题：{info['title']}",
                f"UP主：{info['owner']}",
                f"时长：{_fmt_duration(info['duration_s'])} · 发布于 {_fmt_date(info['pubdate'])}",
                "数据：播放 {} · 点赞 {} · 投币 {} · 收藏 {} · 弹幕 {} · 评论 {}".format(
                    stat.get("view", "?"),
                    stat.get("like", "?"),
                    stat.get("coin", "?"),
                    stat.get("favorite", "?"),
                    stat.get("danmaku", "?"),
                    stat.get("reply", "?"),
                ),
                f"链接：https://www.bilibili.com/video/{info['bvid']}",
                f"简介：{info['desc'] or '（无）'}",
            ]
        )

    def video_comments(args: dict[str, Any]) -> str:
        count = min(max(int(args.get("count") or 10), 1), 20)
        items = api.video_comments(str(args["target"]), count)
        if not items:
            return "没有拿到评论（评论可能关闭，或接口未开放给未登录用户）。"
        return "\n".join(
            f"- {c['user']}（{c['like']} 赞）：{c['content']}" for c in items
        )

    def video_subtitle(args: dict[str, Any]) -> str:
        text = api.video_subtitle(str(args["target"]))
        if not text:
            return "该视频没有可获取的字幕（未登录时 AI 字幕通常不可见）。建议结合简介与热门评论理解内容。"
        return text

    registry.register(
        Tool(
            name="bilibili_video_info",
            description=(
                "获取B站视频的基本信息：标题、UP主、时长、发布日期、播放/点赞/投币/收藏数据、简介。"
                "入参 target 支持 BV号、av号、完整视频链接或 b23.tv 短链"
            ),
            parameters={
                "type": "object",
                "properties": {
                    "target": {"type": "string", "description": "视频标识或链接"}
                },
                "required": ["target"],
            },
            func=video_info,
            toolset="bilibili",
            risk=Risk.SAFE,
        )
    )
    registry.register(
        Tool(
            name="bilibili_video_comments",
            description="获取B站视频的热门评论（默认10条，含点赞数），了解观众对视频的反响",
            parameters={
                "type": "object",
                "properties": {
                    "target": {"type": "string", "description": "视频标识或链接"},
                    "count": {"type": "integer", "description": "评论条数（1-20，默认10）"},
                },
                "required": ["target"],
            },
            func=video_comments,
            toolset="bilibili",
            risk=Risk.SAFE,
        )
    )
    registry.register(
        Tool(
            name="bilibili_video_subtitle",
            description="获取B站视频的字幕全文（如有）。返回空提示说明没有可获取的字幕",
            parameters={
                "type": "object",
                "properties": {
                    "target": {"type": "string", "description": "视频标识或链接"}
                },
                "required": ["target"],
            },
            func=video_subtitle,
            toolset="bilibili",
            risk=Risk.SAFE,
        )
    )
