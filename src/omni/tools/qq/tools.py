"""QQ 工具集（toolset=qq）：主动发消息。

安全边界（A03/A04 统一后的语义）：
- 只允许发给私聊白名单里的所有者；**空名单 = 谁都不许**（不是"不限"）；
- 白名单可以是"读时为准"的提供者（bootstrap 注入），撤销后下一条就拒；
- 给陌生人发消息、群发、加好友都不在默认能力内（D22 再单独建模）。

结果契约（D09 收口）：发送按回执分三类——确定未发出（可换方式再试）、
结果未知（绝不自动重发）、已发送（side_effect_state=applied）。
"""
from __future__ import annotations

from collections.abc import Callable
from typing import Any

from ...safety.gates import private_target_allowed
from ..registry import Risk, Tool, ToolRegistry
from ..result import ToolResult
from .client import DeliveryNotStarted, DeliveryUnknown, OneBotClient, QQNotConnected

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

    def send_message(args: dict[str, Any]) -> ToolResult:
        try:
            user_id = int(args["user_id"])
        except (TypeError, ValueError):
            return ToolResult.fail("错误：user_id 必须是 QQ 号数字", error_code="bad_arguments")
        decision = private_target_allowed(allowed_provider(), user_id)
        if not decision.allowed:
            return ToolResult.fail(
                f"错误：只能给白名单用户发消息（{decision.reason}）",
                error_code="target_not_allowed",
            )
        text = str(args.get("text", "")).strip()
        if not text:
            return ToolResult.fail("错误：text 不能为空", error_code="bad_arguments")
        try:
            ids = client.send_private_outcome(user_id, text)
        except QQNotConnected as exc:
            return ToolResult.fail(f"错误：{exc}", error_code="qq_not_connected")
        except DeliveryNotStarted as exc:
            # 回执明确拒绝/首段未发出：确定没有发出去，可稍后重试或换方式
            return ToolResult.fail(
                f"发送失败（未发出，可稍后重试或换方式）：{exc}",
                error_code="delivery_not_started",
                retryable=True,
            )
        except DeliveryUnknown as exc:
            # 可能已发出一部分：宁可漏发也不自动重发，交人工/模型显式决定
            return ToolResult.unknown(
                f"发送未确认（不要自动重试）：{exc}", error_code="delivery_unknown"
            )
        except Exception as exc:  # noqa: BLE001 —— 跨线程等待等意外失败按"结果未知"处理
            return ToolResult.unknown(
                f"发送未确认（不要自动重试）：{exc}", error_code="delivery_unknown"
            )
        return ToolResult.ok(
            "已发送", side_effect_state="applied", data={"message_ids": list(ids)}
        )

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
