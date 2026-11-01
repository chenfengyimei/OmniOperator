"""活跃时段统计与「建议安静时段」推导（抄 surfcore 的作息窗口**形态**，不是它的学习能力）。

**先说清楚参考实现的真实能力**（核实结论，不是推测）：

`xuexi/surfcore/plugins/surfcore/lib/scheduler.js` 的「生物钟窗口」是
**用户手动配置的固定窗口**（`cfg.night.windows`），不是从数据里学出来的；
`client.js` 的设置页也是「作息窗口编辑器」，字段全是手动填的。
也就是说：**参考实现没有「学作息」这回事**，我们并不落后于它。

那我们为什么还做？因为本项目的形态不一样：

- 它的作息窗口是**主动分享的叫醒计划**（什么时候该让 B 站鲸鱼醒来发言）；
- 我们的安静时段是**别打扰用户**的闸门（什么时候不要主动私聊）。

「什么时候不要打扰」这件事，用户自己填的时间窗往往不准（填 22:00 但人还在忙），
而我们手上有**用户真实发消息的时间分布**——这是比手动配置更准的证据。

**所以本模块只做一件事**：从历史对话里统计用户活跃的小时分布，
据此**推导一个建议的安静时段**。

**关键纪律：只建议，不自动生效。**

- 推导结果**不会**写进 config.yaml，**不会**改变任何闸门行为；
- 它只作为一个**可查的参考值**呈现给用户，由用户决定要不要填进配置；
- 理由：作息统计会出错（用户某天加班、某天倒时差、出差一周），
  而自动改安静时段会**直接改变系统对用户的打扰频率**——
  猜错的方向如果是「拦多了」，用户会以为系统坏了。
  这类推断只该给建议，不该自动生效。

**为什么要放在 `safety`（叶子层）**：判定是纯函数（输入是小时计数，
输出是建议区间），不碰 IO、不读配置。读数据、落盘、呈现都在调用方。
"""
from __future__ import annotations

from dataclasses import dataclass, field

# 建议安静时段的最小长度：比这更短的区间没有「别打扰」的意义。
MIN_QUIET_HOURS = 3

# **静默小时数占比**的上限：超过它说明「几乎全天都不活跃」，
# 多半是数据不足（比如用户只在工作日用、周末完全不用），
# 不该给建议。
#
# 注意这不是「区间长度」的上限——**夜间区间本来就长**（19:00–09:00 就是 14 小时），
# 那正是最该建议的场景。判据是「全天里有多大比例不活跃」，
# 而不是「这一段有多长」。
MAX_QUIET_DAY_RATIO = 0.75

# 判定「这个小时用户不活跃」所需的最少样本数（不同天的同一小时）。
# 少于这个数就认为「这个小时的数据不足」，不据此判静默。
MIN_SAMPLES_PER_HOUR = 3

# 一个连续静默区间的最小天数占比：连续这么多天都在同一段没活动，才敢建议。
MIN_QUIET_DAY_RATIO = 0.6


@dataclass(frozen=True)
class ActivityProfile:
    """用户的活跃时段画像。

    `hour_counts[h]` 是「第 h 小时里有活动的天数数」（不是消息条数——
    条数会被一次长谈拉偏，天数才代表「通常这个点醒着」）。
    `observed_days` 是有数据的天数。
    """

    hour_counts: dict[int, int] = field(default_factory=dict)
    observed_days: int = 0

    def activity_rate(self, hour: int) -> float | None:
        """某个小时「通常醒着」的比例。

        **语义要分清两件事**：

        - `hour_counts[h] < MIN_SAMPLES_PER_HOUR`（这个小时只在少数几天出现）
          → 样本不足，比例不可靠，返回 None；
        - 这个小时**一次都没出现过** → 它不是"样本不足"，而是
          **确凿地没有活动**（用户在这些天里从没在这个点发过消息），
          活跃比例就是 0。

        把这两种情况混为一谈会得出完全相反的结论：
        如果把"没出现过"当成"样本不足"从而判成活跃，
        那么只要用户每天在同一批小时里活动，全天都会被判成活跃，
        **永远找不到静默区间**——作息画像就废了。
        """
        if self.observed_days <= 0:
            return None
        count = self.hour_counts.get(int(hour) % 24, 0)
        if count == 0:
            return 0.0  # 确凿地没有活动
        if count < MIN_SAMPLES_PER_HOUR:
            return None  # 样本不足
        return count / self.observed_days


@dataclass(frozen=True)
class QuietSuggestion:
    """建议的安静时段。`ok=False` 表示数据不足，给不出建议。"""

    ok: bool
    start: str = ""
    end: str = ""
    reason: str = ""
    confidence: float = 0.0

    def describe(self) -> str:
        if not self.ok:
            return f"给不出建议：{self.reason}"
        return (
            f"建议安静时段 {self.start} – {self.end}"
            f"（依据 {self.confidence * 100:.0f}% 的天数在这个区间没有活动）"
        )


def build_profile(hours_by_day: dict[str, set[int]]) -> ActivityProfile:
    """从「每天出现过活动的小时集合」构造画像。

    输入形如 `{"2026-11-01": {9, 10, 14}, ...}`。
    """
    counts: dict[int, int] = {}
    days = 0
    for hours in hours_by_day.values():
        if not hours:
            continue
        days += 1
        for hour in hours:
            key = int(hour) % 24
            counts[key] = counts.get(key, 0) + 1
    return ActivityProfile(hour_counts=counts, observed_days=days)


def suggest_quiet_hours(profile: ActivityProfile) -> QuietSuggestion:
    """按活跃画像推导建议的安静时段。

    判据：一个小时的**活跃率**低于 50% 就算静默（用户通常不在）。
    活跃率为 None（样本不足）的小时既不算活跃也不算静默——
    它不能撑起一个静默区间，但也不能**打断**一个区间（否则一个样本不足的
    小时会把 22:00–07:00 这种连续区间切成两半）。

    找**最长**的连续静默区间（跨零点也算连续），但要求它：
    - 至少 `MIN_QUIET_HOURS` 小时（更短的区间没有意义）；
    - 全天的静默小时占比不超过 `MAX_QUIET_DAY_RATIO`
      （超过说明「几乎全天不活跃」，多半是数据不足而不是作息规律）。

    找不到合格区间就返回 `ok=False` 与原因——**宁可不给建议，也不给一个
    看着有模有样但其实不可靠的区间**。
    """
    days = profile.observed_days
    if days <= 0:
        return QuietSuggestion(False, reason="还没有对话记录，无法统计活跃时段")
    if days < MIN_SAMPLES_PER_HOUR:
        return QuietSuggestion(
            False, reason=f"只有 {days} 天的记录，至少要 {MIN_SAMPLES_PER_HOUR} 天才谈得上作息"
        )

    rates: dict[int, float | None] = {h: profile.activity_rate(h) for h in range(24)}
    # 静默 = 活跃率已知且低于 50%
    quiet = {h: (r is not None and r < 0.5) for h, r in rates.items()}

    # 找最长连续静默区间（考虑跨零点：把序列复制一遍，扫 48 个小时）
    best_start = -1
    best_len = 0
    run_start = -1
    run_len = 0
    for index in range(48):
        hour = index % 24
        if quiet[hour]:
            if run_len == 0:
                run_start = index
            run_len += 1
            if run_len > best_len:
                best_len = run_len
                best_start = run_start
        elif rates[hour] is None:
            # 样本不足的小时：**不打断**区间（保留连续性），但不计入长度。
            # 这是刻意的：否则 22:00–07:00 会被一个稀有小时切成两段，
            # 明明用户整个夜晚都不在，却被判成「没有连续静默区间」。
            continue
        else:
            run_len = 0

    if best_len < MIN_QUIET_HOURS:
        return QuietSuggestion(
            False,
            reason=(
                f"没有找到足够长的连续不活跃时段（最长 {best_len} 小时，"
                f"至少要 {MIN_QUIET_HOURS} 小时；用户可能几乎全天都在用）"
            ),
        )
    quiet_ratio = sum(1 for h in range(24) if quiet[h]) / 24
    if quiet_ratio > MAX_QUIET_DAY_RATIO:
        return QuietSuggestion(
            False,
            reason=(
                f"全天有 {quiet_ratio * 100:.0f}% 的小时都没有活动，超过 "
                f"{MAX_QUIET_DAY_RATIO * 100:.0f}% 上限——"
                "更像是记录不完整而不是作息规律"
            ),
        )

    start_hour = best_start % 24
    end_hour = (best_start + best_len) % 24
    # 置信度 = 该区间内所有小时的「不活跃比例」的均值
    values = []
    for index in range(best_start, best_start + best_len):
        hour = index % 24
        rate = rates[hour]
        values.append(1.0 - (rate if rate is not None else 0.0))
    confidence = sum(values) / len(values) if values else 0.0

    return QuietSuggestion(
        True,
        start=f"{start_hour:02d}:00",
        end=f"{end_hour:02d}:00",
        reason="按历史对话的小时分布推导",
        confidence=confidence,
    )
