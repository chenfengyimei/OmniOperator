"""通道层冷却位：刚聊过就潜水，别急着主动搭话。

**抄的是语义，不是形态。** surfcore 的 `conversation-closure.js` 用一整套对话收尾
状态机（六信号打分、`requireNoOpenQuestion`、摘要与下次开场白）来回答
「这段可以结束了」。我们是一问一答的无状态模型，那套前提不成立：
抄一个前提不成立的机制只会得到没人调、没人敢删的死代码。

所以这里只保留它真正起作用的那一条判定：**收到消息之后的一段时间内，
压制「主动发起」，但不压制「被叫到就回应」**。对应 surfcore
`closureSuppressesWake` 的 `SOFT_WAKE_REASONS`——只压软唤醒，
被 @ / 叫名字 / 提问 / 私聊一律照常醒。surfcore 那句注释说得很准：
「结束不是把人关掉」。

**为什么压制范围天然只落在主动路径上**：QQ 收到消息后的必然回复走
`channels/qq.py` 的入站处理，根本不经过主动闸门；而定时投递与模型主动私聊
两条主动路径都经过 `safety/proactive.py` 的同一份判定。把冷却位挂在这份判定上，
就自动做到「潜水期照常回话、不主动搭话」——不需要在入站路径上再写一遍例外。

**唤醒理由分层**（P5-e 的骨架）：本模块只区分「软唤醒可被压制 / 硬唤醒永不被压制」。
软唤醒 = 自己决定开口（定时投递、模型主动私聊、将来的概率唤醒）；
硬唤醒 = 用户明确叫到（被 @、叫名字、提问、拍一拍、私聊）。

判定是纯函数：不碰 IO、不读时钟、不读配置，调用方把时间与窗口喂进来。
这样它可以完全离线单测——真出问题时不靠真机也能复现。
"""
from __future__ import annotations

import time
from dataclasses import dataclass

# 软唤醒：自己决定开口，可以被冷却位压制。
# 判据是「我不请自来发消息」：定时投递、模型主动私聊将来的概率唤醒都算。
# cron 也算软唤醒——surfcore 目的引擎的 `idleThresholdMs=30min`
# （"主人刚聊过就不插话"）管的就是这一类；而且 cron 路径被压制时是
# **延迟补投**（留在 outbox 里每分钟重查策略）而不是丢弃，提醒不会消失。
# 代价是定时提醒最多晚一个冷却窗口送达——这条要写进台账的剩余限制。
SOFT_WAKE_REASONS = frozenset({"cron", "proactive", "probability"})

# 硬唤醒：用户明确叫到，任何情况下都不被冷却压制。
HARD_WAKE_REASONS = frozenset({"direct", "reply", "mention", "private"})


@dataclass(frozen=True)
class CooldownDecision:
    """冷却判定结果。

    state: allow（不在冷却期）/ suppress（冷却期内，软唤醒被压制）
    reason: 人可读原因，直接进拒绝文案
    remaining_s: 剩余冷却秒数（0 表示不在冷却期）
    """

    state: str
    reason: str = ""
    remaining_s: float = 0.0


def is_soft_wake(reason: str) -> bool:
    """这个唤醒理由算不算「自己决定开口」。

    未知理由按硬唤醒处理（fail-closed 地保守）：宁可多回一句，
    也不要因为认错了理由把用户明确叫到的请求压掉。
    """
    return str(reason) in SOFT_WAKE_REASONS


def cooldown_decision(
    *,
    last_inbound_at: float | None,
    now: float | None = None,
    window_s: float = 0.0,
    reason: str = "proactive",
) -> CooldownDecision:
    """该不该因为「刚聊过」而压制这次唤醒。

    `last_inbound_at` 是该场景最近一次收到消息的时间（epoch 秒）；
    `None` 表示这个场景从没收到过消息（无从谈冷却）。
    `window_s` 是冷却窗口秒数，`<= 0` 表示冷却位关闭。
    """
    if not is_soft_wake(reason):
        return CooldownDecision("allow", "硬唤醒不压制")
    if window_s <= 0:
        return CooldownDecision("allow", "冷却位未启用")
    if last_inbound_at is None:
        return CooldownDecision("allow", "该场景没有收到过消息")
    stamp = time.time() if now is None else float(now)
    elapsed = stamp - float(last_inbound_at)
    if elapsed < 0:
        # 时钟回拨：按「刚收到」处理，不因此放行主动搭话。
        elapsed = 0.0
    if elapsed >= window_s:
        return CooldownDecision("allow", "冷却期已过")
    remaining = float(window_s) - elapsed
    return CooldownDecision(
        "suppress",
        f"刚在 {int(elapsed // 60)} 分钟前聊过，冷却中（约 {int((remaining + 59) // 60)} 分钟后再说）",
        remaining,
    )
