"""记忆容量的**口径化档位判定**（抄 surfcore `src/memory-card.js` 的判定逻辑）。

参考实现的机制（`memoryCardStatus` + `shouldNotifyOwner`）：

    MEMORY_CAPACITY_THRESHOLDS = [0.8, 0.9, 1.0]
    level = 0
    if percentRatio >= THRESHOLDS[2] level = 3
    else if percentRatio >= THRESHOLDS[1] level = 2
    else if percentRatio >= THRESHOLDS[0] level = 1

    // 同一档 12 小时只通知一次
    shouldNotifyOwner(card, level, now, cooldownMs = 12h):
        last = card.alerts.lastNotified[level]
        return now - last >= cooldownMs

**抄的是判定逻辑，不是形态**。三处差异要说清楚：

1. **口径不同**：它是「记忆卡」（一张渲染出来的大卡，有 `maxChars` 与
   `warnChars` 两个参照）；我们没有一个统一的大卡，而是**分容器**限额——
   所有者档案（2000 字）、所有者常驻（2200 字）、人物档案/常驻/事件各有各的。
   所以我们的档位判定作用在**每个容器各自的用量/上限**上。
2. **阈值参照不同**：它的 `percentRatio` 在「不限制字数」时退回用
   `warnChars` 做参照。我们的容器**都有硬上限**（写入会被拒），
   所以档位直接比硬上限——这更简单，也更诚实：100% 档就是真的写不进去了。
3. **告警通道不同**：它把提醒写进记忆卡 JSON 的 `alerts` 字段，
   由定时任务推给管理员。我们**不在这里做通知**——本模块只说「到了哪一档」，
   说给谁、要不要说由调用方决定（和 `llm/tiers.py` 的「判定与处置分离」同一条纪律）。

**为什么不做「自动压缩」**：它的 `maintainMemoryCard` 会自动合并记忆。
我们的记忆有**出处锚点**（`memory/anchor.py`：每条沉淀都要附逐字原文摘录），
自动合并会破坏「这条结论从哪句话来的」这条可回查的链。
所以我们只告警，不自动改数据——**要合并由模型/用户显式决定**。

**为什么与费用口径（`llm/tiers.py`）分开**：费用按**微美元**记账、有
「已预留 + 已花费」的语义；记忆容量按**字符数**记账。两个口径的量纲、
上限来源、提醒对象都不同，混在一处迟早会有人拿错分母。

三条不变式（与 `llm/tiers.py` 一致）：

- **未知不是 0**：上限 <= 0 表示不限，此时没有「到线」这回事，档位恒为 ok。
- **判定与处置分离**：本模块只判定档位与「该不该再提醒」，不落盘、不发消息。
- **比例算不出来就返回 None**：调用方不得把它当 0% 渲染成「还很宽裕」。
"""

from __future__ import annotations

from dataclasses import dataclass

# 三档阈值：80% 提醒该合并了 / 90% 快满了 / 100% 写不进去了。
# 抄参考实现的 MEMORY_CAPACITY_THRESHOLDS。
CAPACITY_THRESHOLDS: tuple[float, float, float] = (0.8, 0.9, 1.0)

# 档位
TIER_OK = "ok"  # 未到 80%
TIER_WARN = "warn"  # >= 80%：该合并了
TIER_CRITICAL = "critical"  # >= 90%：快满了
TIER_FULL = "full"  # >= 100%：写不进去了

# 同一档的提醒冷却：12 小时内不重复提醒同一档。
# 抄参考实现的 `shouldNotifyOwner(card, level, now, cooldownMs = 12h)`。
COOLDOWN_SECONDS = 12 * 60 * 60


@dataclass(frozen=True)
class CapacityUsage:
    """一个容器的用量与上限。`limit_chars<=0` 表示不限。"""

    container: str
    used_chars: int
    limit_chars: int

    @property
    def limited(self) -> bool:
        return self.limit_chars > 0

    @property
    def ratio(self) -> float | None:
        """已用比例。**不限时返回 None**（未知不是 0）。"""
        if not self.limited:
            return None
        return self.used_chars / self.limit_chars

    @property
    def tier(self) -> str:
        """这一档到了哪儿。

        - 不限 → 永远 ok（没上限就没有「到线」这回事）；
        - >=100% → full；>=90% → critical；>=80% → warn；其余 ok。
        """
        ratio = self.ratio
        if ratio is None:
            return TIER_OK
        if ratio >= CAPACITY_THRESHOLDS[2]:
            return TIER_FULL
        if ratio >= CAPACITY_THRESHOLDS[1]:
            return TIER_CRITICAL
        if ratio >= CAPACITY_THRESHOLDS[0]:
            return TIER_WARN
        return TIER_OK

    @property
    def remaining_chars(self) -> int | None:
        """还能写多少。不限时返回 None（不是 0——「不限」和「写满了」是两回事）。"""
        if not self.limited:
            return None
        return max(0, self.limit_chars - self.used_chars)


def collect_tiers(usages: list[CapacityUsage]) -> list[CapacityUsage]:
    """挑出到了提醒线及以上的容器，按严重度排序。

    排序规则：先 full 后 critical 再 warn；同档按比例从高到低；再同则按容器名，
    保证输出稳定（同样的输入永远同样的顺序，便于断言与展示）。
    """
    hits = [u for u in usages if u.tier != TIER_OK]
    order = {TIER_FULL: 0, TIER_CRITICAL: 1, TIER_WARN: 2}
    hits.sort(key=lambda u: (order.get(u.tier, 3), -(u.ratio or 0.0), u.container))
    return hits


def tier_label(tier: str) -> str:
    """档位的人话标签（给界面/提醒文案用）。"""
    return {
        TIER_OK: "正常",
        TIER_WARN: "接近满（80%）",
        TIER_CRITICAL: "快满了（90%）",
        TIER_FULL: "已满（100%）",
    }.get(tier, "正常")


def describe_usage(usage: CapacityUsage) -> str:
    """人话描述一个容器的用量。

    不限时只说已用多少，不说比例——没有分母的比例是编出来的。
    """
    name = usage.container
    if not usage.limited:
        return f"{name}：已用 {usage.used_chars} 字（未设上限）"
    ratio = usage.ratio or 0.0
    return f"{name}：{usage.used_chars} / {usage.limit_chars} 字（{ratio * 100:.1f}%）"


def tier_message(usages: list[CapacityUsage], *, separator: str = "；") -> str:
    """给用户看的档位说明。

    区分三档是因为**处置动作不同**：warn 提醒合并、critical 催着合并、
    full 已经写不进去了。把它们混成一句话，用户就不知道该做什么。
    """
    full = [u for u in usages if u.tier == TIER_FULL]
    critical = [u for u in usages if u.tier == TIER_CRITICAL]
    warn = [u for u in usages if u.tier == TIER_WARN]
    parts: list[str] = []
    if full:
        parts.append(
            "已写不进去了（100%）："
            + separator.join(describe_usage(u) for u in full)
            + "。请先合并或删减现有条目，再写入新的。"
        )
    if critical:
        parts.append(
            "快满了（90%）："
            + separator.join(describe_usage(u) for u in critical)
            + "。建议现在就合并，别等写不进去再处理。"
        )
    if warn:
        parts.append("接近满（80%）：" + separator.join(describe_usage(u) for u in warn) + "。可以开始合并了。")
    return separator.join(parts)


def should_notify(level: int, last_notified: float, now: float, *, cooldown_seconds: int = COOLDOWN_SECONDS) -> bool:
    """这一档该不该**再**提醒一次。

    抄参考实现的 `shouldNotifyOwner`：同一档在冷却期内只提醒一次。
    `last_notified` 是该档上次提醒的 epoch 秒；0 表示从没提醒过。

    为什么按**档**分别记而不是记一个总时间：档位只会往上走，
    从 warn 升到 critical 是**新的一档**，值得立刻再提醒一次；
    而停在同一档反复提醒只会变成噪音。
    """
    if level <= 0:
        return False
    if last_notified <= 0:
        return True
    return (now - last_notified) >= cooldown_seconds
