"""主动发言闸门：白名单 + 仅所有者 + 安静时段 + 每日额度上限。

**为什么搬到这里**：判定逻辑原先在 `core/qq_policy.py`，但 tools 层按架构守卫
不能 import core（`tests/test_arch.py` 的分层规则），于是「模型自己调
qq_send_message 主动发消息」这条路径绕过了整套主动策略——白名单之外还在其次，
真正的问题是它**完全不看安静时段、不看每日额度、不看是否只允许所有者**，
而定时投递那条路径（`core/cron.py` → `make_runtime_deliver`）全都看了。
同一个动作有两条路，一条有闸门一条没有，等于闸门没设。

所以判定下沉到叶子层 safety：core 与 tools 都能 import 它，两条路径共用同一份判定。
这是 surfcore 白名单归一化那条教训的同一件事——**多个写入点共用一个归一化纯函数**，
否则配置漂移迟早会把某条路径漏掉。

三条闸门与它们各自的处置：
- **白名单**：不在名单里 → deny（空名单 = 谁都不许，fail-closed）。
- **仅所有者**：主动提醒只允许发给 owner。别人在名单里也不代表该收到主动推送。
- **安静时段**：落在免打扰区间 → defer（定时投递路径入队延后；工具路径直接拒绝，
  因为工具调用没有队列，模型拿到的应该是"现在别发"而不是"稍后再说"）。
- **每日额度**：由调用方用 `state.consume_budget` 原子扣减（这里只给上限值），
  这样额度语义与定时投递完全一致，不会出现两条路径各算各的。

**为什么不在这里扣额度**：闸门是纯判定，不碰 IO；额度扣减要跨进程原子
（`state.db` 的条件 UPDATE），那是 `StateStore` 的事。分开之后判定可以单测，
额度也不会因为判定被多处调用而重复扣。
"""
from __future__ import annotations

import datetime as dt
from dataclasses import dataclass
from zoneinfo import ZoneInfo

from .gates import private_target_allowed


@dataclass(frozen=True)
class ProactiveDecision:
    state: str  # allow / defer / deny
    reason: str
    day: str = ""
    limit: int = 0


def proactive_decision(policy, target: int, *, now: dt.datetime | None = None) -> ProactiveDecision:
    """主动发言是否放行。

    `policy` 是 QQ 策略对象（duck-typing，不 import config 以免倒挂依赖）：
    需要 allowed_users / proactive_enabled / owner_qq / timezone /
    quiet_start / quiet_end / proactive_daily_limit。
    """
    decision = private_target_allowed(policy.allowed_users, target)
    if not decision.allowed:
        return ProactiveDecision("deny", f"白名单撤销或未授权：{decision.reason}")
    if not getattr(policy, "proactive_enabled", False):
        return ProactiveDecision("deny", "所有者主动提醒未启用")
    if int(target) != getattr(policy, "owner_qq", 0):
        return ProactiveDecision("deny", "主动提醒仅允许指定所有者")
    try:
        stamp = (now or dt.datetime.now(dt.timezone.utc)).astimezone(ZoneInfo(policy.timezone))
        minute = stamp.strftime("%H:%M")
        start, end = policy.quiet_start, policy.quiet_end
        quiet = (start <= minute < end) if start < end else (minute >= start or minute < end) if start > end else False
        limit = int(policy.proactive_daily_limit)
    except (ValueError, AttributeError, KeyError, TypeError):
        return ProactiveDecision("deny", "主动提醒策略不可用")
    if quiet:
        return ProactiveDecision("defer", "安静时段，等待下一次补投")
    return ProactiveDecision("allow", "已显式启用的所有者提醒", stamp.date().isoformat(), limit)
