"""QQ 工具集（toolset=qq）：主动发消息。

P2 安全边界：只允许发给配置白名单里的用户（owner），给陌生人发消息
属于写操作，待 P3 确认流再放开。
"""
from __future__ import annotations

from typing import Any

from ..registry import Risk, Tool, ToolRegistry
from .client import OneBotClient


def register_qq_tools(
    registry: ToolRegistry, client: OneBotClient, allowed_users: list[int]
) -> None:

    def send_message(args: dict[str, Any]) -> str:
        try:
            user_id = int(args["user_id"])
        except (TypeError, ValueError):
            return "错误：user_id 必须是 QQ 号数字"
        if user_id not in allowed_users:
            return f"错误：只能给白名单用户发消息（{user_id} 不在白名单内）"
        text = str(args.get("text", "")).strip()
        if not text:
            return "错误：text 不能为空"
        return client.send_private_sync(user_id, text)

    registry.register(
        Tool(
            name="qq_send_message",
            description=(
                "主动给 QQ 用户发私聊消息（目前只允许发给配置白名单内的用户，"
                "比如主动通知/提醒主人）"
            ),
            parameters={
                "type": "object",
                "properties": {
                    "user_id": {"type": "integer", "description": "QQ 号（须在白名单内）"},
                    "text": {"type": "string", "description": "消息内容"},
                },
                "required": ["user_id", "text"],
            },
            func=send_message,
            toolset="qq",
            risk=Risk.SAFE,
        )
    )
