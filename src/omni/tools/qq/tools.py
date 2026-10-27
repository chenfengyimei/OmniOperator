"""QQ 工具集（toolset=qq）：主动发消息。

安全边界（A03/A04 统一后的语义）：
- 只允许发给私聊白名单里的所有者；**空名单 = 谁都不许**（不是"不限"）；
- 白名单可以是"读时为准"的提供者（bootstrap 注入），撤销后下一条就拒；
- 给陌生人发消息、群发、加好友都不在默认能力内（D22 再单独建模）。
"""
from __future__ import annotations

from collections.abc import Callable
from typing import Any

from ...safety.gates import private_target_allowed
from ..registry import Risk, Tool, ToolRegistry
from .client import OneBotClient

AllowedUsers = list[int] | Callable[[], list[int]]


def _as_provider(allowed_users: AllowedUsers) -> Callable[[], list[int]]:
    if callable(allowed_users):
        return allowed_users
    snapshot = list(allowed_users)
    return lambda: list(snapshot)


def register_qq_tools(
    registry: ToolRegistry, client: OneBotClient, allowed_users: AllowedUsers
) -> None:
    allowed_provider = _as_provider(allowed_users)

    def send_message(args: dict[str, Any]) -> str:
        try:
            user_id = int(args["user_id"])
        except (TypeError, ValueError):
            return "错误：user_id 必须是 QQ 号数字"
        decision = private_target_allowed(allowed_provider(), user_id)
        if not decision.allowed:
            return f"错误：只能给白名单用户发消息（{decision.reason}）"
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
