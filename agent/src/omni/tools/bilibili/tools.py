"""B站读工具集（toolset=bilibili，全部 SAFE 只读）。

包装原则：只做参数校验 + 结果排版；业务与 HTTP 细节在 api.py，
ASR 细节在 asr.py，抽帧拼图在 frames.py。

数字具身视角下的分工：
- 账号：bilibili_account_info（当前登录的是谁）
- 看内容：bilibili_video_info / subtitle / transcript（读文字）
- 看画面：bilibili_video_frames（抽帧拼成一张长图，多模态一次看完）
- 看反馈：bilibili_video_comments / bilibili_comment_replies（评论与楼中楼）
- 找人：bilibili_user_search（为艾特定 UID）
写（发评论、私信回复）走 write_tools.py 的草稿 + 人工审批链。
"""

from __future__ import annotations

import datetime as dt
import tempfile
from pathlib import Path
from typing import Any

from ...config import Asr
from ..registry import Risk, SessionRef, Tool, ToolRegistry
from ..runctx import RunRef
from ..shots import save_screenshot
from . import asr, frames
from .api import BiliAPI, BiliAPIError

MAX_COMMENT_COUNT = 20


def register_bilibili_tools(
    registry: ToolRegistry,
    api: BiliAPI,
    asr_config: Asr | None = None,
    *,
    cache_root: Path | None = None,
    artifacts: Any = None,
    session_ref: SessionRef | None = None,
    run_ref: RunRef | None = None,
    can_view: bool = False,
) -> None:

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

    def _artifact_context() -> tuple[int | None, str | None]:
        session_id = session_ref.get() if session_ref is not None else None
        run = run_ref.get() if run_ref is not None else None
        run_id = run.run_id if run is not None else None
        return session_id, run_id

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

    def account_info(_args: dict[str, Any]) -> str:
        """当前 B站登录账号（UID/昵称/等级）——评论与私信都以它为准。"""
        try:
            info = api.account()
        except BiliAPIError as exc:
            return f"读取账号失败：{exc}"
        if not info.get("logged_in"):
            return (
                "当前凭证没有登录 B站（或已失效）：只能看公开内容，字幕/音频/发布都拿不到。"
                "把浏览器里的 SESSDATA 重新粘到设置中心「哔哩哔哩」再试。"
            )
        lines = [
            f"账号：{info['name']}（UID {info['uid']}）",
            f"等级：Lv{info['level']}" + ("· 大会员" if info.get("vip") else ""),
            f"B币：{info.get('coins', 0)}",
        ]
        return "\n".join(lines)

    def video_comments(args: dict[str, Any]) -> str:
        count = min(max(int(args.get("count") or 10), 1), MAX_COMMENT_COUNT)
        sort = "time" if str(args.get("sort") or "hot").strip().lower() in ("time", "new", "最新") else "hot"
        page = max(1, int(args.get("page") or 1))
        items = api.video_comments(str(args["target"]), count, sort=sort, page=page)
        header = f"评论（{'最新' if sort == 'time' else '热门'}，第 {page} 页）："
        if not items:
            return header + "没有拿到评论（评论可能关闭，或接口未开放给未登录用户）。"
        return "\n".join(
            [header]
            + [
                "- {user}{like}：{content}".format(
                    user=item.get("user", ""),
                    like=f"（{item['like']} 赞）" if item.get("like") else "",
                    content=item.get("content", ""),
                )
                + (
                    f" [评论ID {item['rpid']}，{item['reply_count']} 条回复]"
                    if item.get("rpid") and item.get("reply_count")
                    else (f" [评论ID {item['rpid']}]" if item.get("rpid") else "")
                )
                for item in items
            ]
        )

    def comment_replies(args: dict[str, Any]) -> str:
        """楼中楼：读某条评论下的回复（先用 bilibili_video_comments 拿评论ID）。"""
        rpid = str(args.get("rpid") or args.get("reply_id") or "").strip()
        if not rpid:
            return "缺少评论ID（rpid）：先用 bilibili_video_comments 拿到。"
        count = min(max(int(args.get("count") or 10), 1), MAX_COMMENT_COUNT)
        items = api.comment_replies(str(args["target"]), rpid, count)
        if not items:
            return "这个评论区没有回复（评论可能关闭，或这条评论下还没有回复）。"
        return "\n".join(
            "- {user}{to}：{content}".format(
                user=item.get("user", ""),
                to=f" 回复 {item['to_user']}" if item.get("to_user") else "",
                content=item.get("content", ""),
            )
            for item in items
        )

    def user_search(args: dict[str, Any]) -> str:
        """找用户（拿 UID 与昵称，供艾特使用）。"""
        keyword = str(args.get("keyword") or "").strip()
        if not keyword:
            return "关键词不能为空。"
        limit = min(max(int(args.get("limit") or 5), 1), 20)
        rows = api.user_search(keyword, limit)
        if not rows:
            return f"没有找到与「{keyword}」匹配的用户。"
        return "\n".join(
            "- {name}（UID {uid}）{sign}".format(
                name=row.get("name", ""),
                uid=row.get("uid", ""),
                sign=("· " + row["sign"]) if row.get("sign") else "",
            )
            for row in rows
        )

    def video_frames(args: dict[str, Any]) -> str:
        """抽帧拼长图：模型一次看完整个视频的画面。"""
        target = str(args["target"])
        count = min(max(int(args.get("count") or frames.DEFAULT_FRAMES), 1), frames.MAX_FRAMES)
        width = min(max(int(args.get("width") or frames.DEFAULT_WIDTH), frames.MIN_WIDTH), frames.MAX_WIDTH)
        url = api.video_stream(target)
        temporary = cache_root / "video" if cache_root is not None else None
        if temporary is not None:
            temporary.mkdir(parents=True, exist_ok=True)
        with tempfile.TemporaryDirectory(prefix="omni-frames-", dir=temporary) as tmp:
            video = Path(tmp) / "video.mp4"
            api.download_binary(url, video)
            picked = frames.extract_frames(video, count=count, width=width)
        if not picked:
            return "没能从视频里抽出画面（可能是纯音频流，或解码失败）。"
        sheet = frames.contact_sheet(picked)
        session_id, run_id = _artifact_context()
        text = save_screenshot(
            artifacts,
            sheet,
            session_id=session_id,
            run_id=run_id,
            kind="video_frames",
            source="bilibili_video_frames",
            can_view=can_view,
            label="视频抽帧长图",
        )
        stamps = "、".join(frames.format_time(timestamp) for timestamp, _ in picked)
        return f"{text}\n共 {len(picked)} 帧，时间点：{stamps}（左→右、上→下按时间排列）。"

    def video_subtitle(args: dict[str, Any]) -> str:
        text = api.video_subtitle(str(args["target"]))
        if not text:
            return (
                "该视频没有可获取的字幕（未登录时 AI 字幕通常不可见）。"
                "可改用 bilibili_video_transcript 转写音频（更慢但通用），"
                "或结合简介与热门评论理解内容。"
            )
        return text

    def video_transcript(args: dict[str, Any]) -> str:
        cfg = asr_config or Asr()
        target = str(args["target"])
        audio_url = api.audio_stream(target)
        temporary = cache_root / "audio" if cache_root is not None else None
        if temporary is not None:
            temporary.mkdir(parents=True, exist_ok=True)
        with tempfile.TemporaryDirectory(prefix="omni-asr-", dir=temporary) as tmp:
            audio = Path(tmp) / "audio.m4s"
            api.download_binary(audio_url, audio)
            return asr.transcribe(
                audio,
                model_name=cfg.model,
                device=cfg.device,
                compute_type=cfg.compute_type,
                **({"download_root": cache_root / "models"} if cache_root is not None else {}),
            )

    registry.register(
        Tool(
            name="bilibili_video_info",
            description=(
                "获取B站视频的基本信息：标题、UP主、时长、发布日期、播放/点赞/投币/收藏数据、简介。"
                "入参 target 支持 BV号、av号、完整视频链接或 b23.tv 短链"
            ),
            parameters={
                "type": "object",
                "properties": {"target": {"type": "string", "description": "视频标识或链接"}},
                "required": ["target"],
            },
            func=video_info,
            toolset="bilibili",
            risk=Risk.SAFE,
            read_only=True,
        )
    )
    registry.register(
        Tool(
            name="bilibili_account_info",
            description=(
                "查询当前B站登录账号：UID、昵称、等级、是否大会员。发评论、发私信前先用它确认「现在登录的是哪个号」"
            ),
            parameters={"type": "object", "properties": {}, "required": []},
            func=account_info,
            toolset="bilibili",
            risk=Risk.SAFE,
            read_only=True,
        )
    )
    registry.register(
        Tool(
            name="bilibili_video_frames",
            description=(
                "把B站视频按时间点抽成多帧，并拼成**一张**长图存为产物 [artifact:id]，"
                "多模态模型一次就能看完整个视频的画面（谁在讲、画面里有什么）。"
                "适合需要看画面而不是读字幕的场景；文字内容仍建议配合字幕/转写工具"
            ),
            parameters={
                "type": "object",
                "properties": {
                    "target": {"type": "string", "description": "视频标识或链接"},
                    "count": {
                        "type": "integer",
                        "description": f"抽帧数（1-{frames.MAX_FRAMES}，默认{frames.DEFAULT_FRAMES}）",
                    },
                    "width": {
                        "type": "integer",
                        "description": f"单帧宽度（{frames.MIN_WIDTH}-{frames.MAX_WIDTH}，默认{frames.DEFAULT_WIDTH}）",
                    },
                },
                "required": ["target"],
            },
            func=video_frames,
            toolset="bilibili",
            risk=Risk.SAFE,
            read_only=True,
        )
    )
    registry.register(
        Tool(
            name="bilibili_video_comments",
            description=(
                "获取B站视频评论（默认10条，含点赞数与评论ID；可按最新排序或翻页）。评论ID可用于继续读楼中楼回复"
            ),
            parameters={
                "type": "object",
                "properties": {
                    "target": {"type": "string", "description": "视频标识或链接"},
                    "count": {"type": "integer", "description": f"评论条数（1-{MAX_COMMENT_COUNT}，默认10）"},
                    "sort": {
                        "type": "string",
                        "enum": ["hot", "time"],
                        "description": "排序：hot 热度（默认）/ time 最新",
                    },
                    "page": {"type": "integer", "description": "页码（从1起，默认1）"},
                },
                "required": ["target"],
            },
            func=video_comments,
            toolset="bilibili",
            risk=Risk.SAFE,
            read_only=True,
        )
    )
    registry.register(
        Tool(
            name="bilibili_comment_replies",
            description="读取B站某条评论下的回复（楼中楼）；先用 bilibili_video_comments 拿到评论ID",
            parameters={
                "type": "object",
                "properties": {
                    "target": {"type": "string", "description": "视频标识或链接"},
                    "rpid": {"type": "string", "description": "评论ID（rpid）"},
                    "count": {"type": "integer", "description": f"回复条数（1-{MAX_COMMENT_COUNT}，默认10）"},
                },
                "required": ["target", "rpid"],
            },
            func=comment_replies,
            toolset="bilibili",
            risk=Risk.SAFE,
            read_only=True,
        )
    )
    registry.register(
        Tool(
            name="bilibili_user_search",
            description="按昵称或关键词搜索B站用户，返回UID与昵称（发评论艾特前先用它确认UID）",
            parameters={
                "type": "object",
                "properties": {
                    "keyword": {"type": "string", "description": "昵称或关键词"},
                    "limit": {"type": "integer", "description": "返回条数（1-20，默认5）"},
                },
                "required": ["keyword"],
            },
            func=user_search,
            toolset="bilibili",
            risk=Risk.SAFE,
            read_only=True,
        )
    )
    registry.register(
        Tool(
            name="bilibili_video_subtitle",
            description="获取B站视频的字幕全文（如有）。返回提示说明没有可获取的字幕时，改用 bilibili_video_transcript",
            parameters={
                "type": "object",
                "properties": {"target": {"type": "string", "description": "视频标识或链接"}},
                "required": ["target"],
            },
            func=video_subtitle,
            toolset="bilibili",
            risk=Risk.SAFE,
            read_only=True,
        )
    )
    registry.register(
        Tool(
            name="bilibili_video_transcript",
            description=(
                "获取B站视频的完整语音转写（下载音频流并用本地 ASR 识别，无需字幕存在）。"
                "适用于没有字幕的视频；速度取决于视频长度（约为时长的 1/3~1 倍），"
                "返回带 [分:秒] 时间戳的文本"
            ),
            parameters={
                "type": "object",
                "properties": {"target": {"type": "string", "description": "视频标识或链接"}},
                "required": ["target"],
            },
            func=video_transcript,
            toolset="bilibili",
            risk=Risk.SAFE,
            read_only=True,
        )
    )
