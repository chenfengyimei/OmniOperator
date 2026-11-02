"""主动提醒策略：显式所有者、安静时段、独立每日额度，以及通道层冷却位；不做网络发送。

判定逻辑已下沉到 `safety/proactive.py` 与 `safety/cooldown.py`（叶子层），原因见那里的说明：
tools 层不能 import core，而「模型自己调 qq_send_message 主动发消息」
这条路径原先完全绕过了主动策略——同一个动作两条路，只有一条有闸门。

这里保留一层薄转发，`core/cron.py` 与 `channels/web.py` 的既有调用点不用改。

**新增的 `proactive_gate` 是这两份判定的组合闸门**，也是主动路径唯一的入口：
先过策略（白名单 / 开关 / 仅所有者 / 安静时段 / 每日额度），策略放行后再过
冷却位（刚聊过就别插话）。cron 投递与模型主动私聊共用它，避免两条路各判一次
出现「一边放行一边压制」的不一致。

冷却位命中时返回 `state="defer"` 而不是 `deny`——两条主动路径对 defer 的处置不同：
- cron 路径有 outbox，defer = 留在队列里每分钟重查策略，冷却结束自然补投（不丢内容）；
- 工具路径没有队列，defer 会被转成一次明确的拒绝（错误码 `proactive_cooldown`）。
用 deny 会让 cron 路径把消息直接丢掉，所以这里必须是 defer。
"""

from __future__ import annotations

import datetime as dt

from ..memory.state import StateUnavailable
from ..safety.cooldown import cooldown_decision
from ..safety.proactive import ProactiveDecision, proactive_decision

__all__ = [
    "ProactiveDecision",
    "proactive_decision",
    "proactive_gate",
    "dm_scene",
    "runtime_last_inbound_at",
    "COOLDOWN_CODE",
]

# 冷却位命中的机器可读码。与「安静时段」区分开：两者都是 defer，
# 但对模型是两件事——一个是「等一会儿再说」，一个是「今晚别打扰」。
COOLDOWN_CODE = "proactive_cooldown"


def dm_scene(target: int | str) -> str:
    """主动私聊目标对应的 scene 键。

    与 `channels/qq.py::_resolve_scope` 的命名保持一致（私聊 `dm:<uid>`），
    否则冷却位查不到入站时间，会一直判定「没聊过」。
    """
    return f"dm:{target}"


def proactive_gate(
    policy,
    target: int,
    *,
    reason: str = "proactive",
    last_inbound_at: float | None = None,
    now: dt.datetime | None = None,
    now_ts: float | None = None,
) -> ProactiveDecision:
    """主动发言的组合闸门：策略 + 冷却位。

    `last_inbound_at` 是该场景最近一次收到消息的时间（epoch 秒），
    `None` 表示没收到过。调用方负责取（`QQMessageStore.last_inbound_at`），
    本函数只做判定，不碰 IO。
    """
    decision = proactive_decision(policy, target, now=now)
    if decision.state != "allow":
        return decision
    minutes = float(getattr(policy, "proactive_cooldown_minutes", 0) or 0)
    cooled = cooldown_decision(
        last_inbound_at=last_inbound_at,
        now=now_ts,
        window_s=minutes * 60.0,
        reason=reason,
    )
    if cooled.state == "suppress":
        return ProactiveDecision(
            "defer",
            cooled.reason,
            decision.day,
            decision.limit,
            code=COOLDOWN_CODE,
        )
    return decision


def runtime_last_inbound_at(runtime, target: int | str) -> float | None:
    """从 runtime 上取「这个私聊最近一次收到消息的时间」，取不到就当没聊过。

    容错是刻意的：冷却是**加分项**不是前提条件。runtime 未必挂着
    `qq_messages`（单元测试里的 fake runtime 就没有），状态库也可能临时不可用。
    这些情况下返回 None → 判定为「没收到过消息」→ 冷却不生效 → 退回旧行为，
    而不是把主动发言整个堵死。
    """
    inbox = getattr(runtime, "qq_messages", None)
    if inbox is None:
        return None
    try:
        return inbox.last_inbound_at(dm_scene(target))
    except (StateUnavailable, AttributeError, TypeError, ValueError):
        return None
