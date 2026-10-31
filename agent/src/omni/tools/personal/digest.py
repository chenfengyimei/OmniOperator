"""个人能力：digest；工具仅使用注入依赖，不构造运行时。"""
from __future__ import annotations

import datetime as dt
from typing import Any
from ...memory.store import PROFILE_MAX_CHARS, MemoryStore
from ..registry import Risk, Tool, ToolRegistry


def register_digest_tool(
    registry: ToolRegistry,
    store: MemoryStore,
    *,
    profile_usage: Any = None,  # Callable[[], int]：数据层统一后档案用量来自 owner 门面
) -> None:
    """daily_digest（SAFE）：夜间整理的素材入口（活动统计）。

    刻意独立于 register_memory_tools——回合后 review 的白名单保持最小
    （memory+profile），digest 由组装根接到主注册表，只在主对话/心跳可用。
    """

    def daily_digest(args: dict[str, Any]) -> str:
        try:
            hours = int(args.get("hours", 24))
        except (TypeError, ValueError):
            return "错误：hours 必须是整数（1-168）"
        hours = max(1, min(hours, 168))
        since = (
            dt.datetime.now().astimezone() - dt.timedelta(hours=hours)
        ).isoformat(timespec="seconds")
        d = store.digest_since(since)
        profile_chars = (
            int(profile_usage()) if callable(profile_usage) else int(d["profile_chars"])
        )
        if not d["sessions"] and d["facts_new"] == 0 and not d["reviews_recent"]:
            return f"过去 {hours} 小时没有会话、记忆或 review 活动，无需整理。"
        lines = [f"过去 {hours} 小时活动（自 {since[:16]} 起）："]
        for s in d["sessions"]:
            lines.append(f"- 会话 #{s['id']}「{s['title']}」{s['messages']} 条消息")
        lines.append(f"- 新增长期记忆 {d['facts_new']} 条：")
        for fact in d["facts_recent"][:10]:
            lines.append(f"  · {fact[:80]}")
        if d["reviews_recent"]:
            lines.append(
                "- 最近 review：" + " / ".join(x[:60] for x in d["reviews_recent"])
            )
        lines.append(
            f"- 用户档案已用 {profile_chars} 字符"
            f"（额度 {PROFILE_MAX_CHARS}，临近额度应先合并再写）"
        )
        return "\n".join(lines)

    registry.register(
        Tool(
            name="daily_digest",
            description=(
                "查看过去 N 小时的活动摘要：活跃会话与消息量、新增长期记忆、"
                "最近 review、档案额度占用。夜间整理/写日报前先调它（hours 默认 24）"
            ),
            parameters={
                "type": "object",
                "properties": {
                    "hours": {
                        "type": "integer",
                        "description": "回看小时数（1-168，默认 24）",
                    }
                },
                "required": [],
            },
            func=daily_digest,
            toolset="core",
            risk=Risk.SAFE,
            read_only=True,
        )
    )
