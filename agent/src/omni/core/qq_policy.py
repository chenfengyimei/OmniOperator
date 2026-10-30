"""主动提醒策略：显式所有者、安静时段和独立每日额度；不做网络发送。"""
from __future__ import annotations

import datetime as dt
from dataclasses import dataclass
from zoneinfo import ZoneInfo

from ..safety.gates import private_target_allowed


@dataclass(frozen=True)
class ProactiveDecision:
    state: str  # allow / defer / deny
    reason: str
    day: str = ""
    limit: int = 0


def proactive_decision(policy, target: int, *, now: dt.datetime | None = None) -> ProactiveDecision:
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
