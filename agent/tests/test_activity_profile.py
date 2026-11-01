"""D36：活跃时段统计与「建议安静时段」推导。

**核实结论先说清楚**：参考实现 `xuexi/surfcore` 的「生物钟窗口」是
**用户手动配置的固定窗口**（`plugins/surfcore/lib/scheduler.js` 的
`cfg.night.windows` + `client.js` 的「作息窗口编辑器」），
**不是从数据里学出来的**。所以「学作息」这件事我们并不落后于参考实现。

那为什么还做？因为形态不同：它的窗口管的是「什么时候让 B 站鲸鱼醒来发言」，
我们的安静时段管的是「什么时候别主动打扰用户」。后者用用户真实发消息的
时间分布来推导，比手动填的时间窗更准。

**关键纪律：只建议，不自动生效。** 作息推断会出错（加班、倒时差、出差），
而自动改安静时段会直接改变打扰频率——猜错方向如果是「拦多了」，
用户会以为系统坏了。所以推导结果只作为可查的参考值。

**全部离线**：不联网、不碰真实账号与真实目录、不调模型。
"""
from __future__ import annotations

import pytest

from omni.safety.activity import (
    MAX_QUIET_DAY_RATIO,
    MIN_QUIET_HOURS,
    MIN_SAMPLES_PER_HOUR,
    ActivityProfile,
    build_profile,
    suggest_quiet_hours,
)


def _days(patterns: dict[int, list[int]]) -> dict[str, set[int]]:
    """构造「第几天有哪些小时有活动」的样本。"""
    return {f"2026-11-{index:02d}": set(hours) for index, hours in enumerate(patterns.values(), 1)}


# ---------- 画像构造 ----------


def test_profile_counts_days_not_messages() -> None:
    """按「天」统计，不是按条数——一条长谈不该把某个小时拉满。"""
    profile = build_profile({
        "2026-11-01": {9, 10},
        "2026-11-02": {9},
        "2026-11-03": {9, 10, 14},
    })
    assert profile.observed_days == 3
    assert profile.hour_counts[9] == 3
    assert profile.hour_counts[10] == 2
    assert profile.hour_counts[14] == 1


def test_activity_rate_distinguishes_absent_from_insufficient() -> None:
    """**「一次都没出现过」与「样本不足」是两件事**，不能混。

    混为一谈会得出完全相反的结论：把"没出现过"当成"样本不足"从而判成活跃，
    那么用户每天在同一批小时里活动时，全天都会被判成活跃，
    永远找不到静默区间——作息画像直接废了。
    """
    profile = build_profile({
        "2026-11-01": {9, 10},
        "2026-11-02": {9, 10},
        "2026-11-03": {9, 10},
    })
    # 3 点：一次都没出现过 → 确凿地没有活动 → 0.0
    assert profile.activity_rate(3) == 0.0
    # 20 点：只在 1 天出现（样本不足 3 天）→ 不可靠 → None
    thin = build_profile({
        "2026-11-01": {9, 20},
        "2026-11-02": {9},
        "2026-11-03": {9},
    })
    assert thin.activity_rate(20) is None
    assert thin.activity_rate(9) == 1.0


def test_activity_rate_is_none_without_any_data() -> None:
    assert ActivityProfile().activity_rate(10) is None
    assert ActivityProfile().observed_days == 0


# ---------- 建议推导 ----------


def test_suggests_quiet_window_for_a_night_owl() -> None:
    """典型作息：白天 9-18 点活跃，夜间无活动 → 建议夜间安静。"""
    patterns = {day: list(range(9, 19)) for day in range(1, 8)}
    profile = build_profile(_days(patterns))
    suggestion = suggest_quiet_hours(profile)
    assert suggestion.ok
    assert suggestion.start == "19:00"
    assert suggestion.end == "09:00"
    assert suggestion.confidence > 0.9


def test_suggests_quiet_window_across_midnight() -> None:
    """跨零点的区间也算连续（23:00 – 07:00）。"""
    patterns = {day: list(range(8, 23)) for day in range(1, 8)}
    profile = build_profile(_days(patterns))
    suggestion = suggest_quiet_hours(profile)
    assert suggestion.ok
    assert suggestion.start == "23:00"
    assert suggestion.end == "08:00"


def test_refuses_when_data_is_too_few_days() -> None:
    """数据太少就**不给**建议——宁可不给，不给一个看着像样但不可靠的值。"""
    profile = build_profile({"2026-11-01": {10, 11, 12}})
    suggestion = suggest_quiet_hours(profile)
    assert not suggestion.ok
    assert str(MIN_SAMPLES_PER_HOUR) in suggestion.reason
    assert "至少" in suggestion.describe()


def test_refuses_when_no_data_at_all() -> None:
    suggestion = suggest_quiet_hours(ActivityProfile())
    assert not suggestion.ok
    assert "没有对话记录" in suggestion.reason


def test_refuses_when_user_is_active_all_day() -> None:
    """用户几乎全天活跃 → 没有连续静默区间 → 不给建议。"""
    patterns = {day: list(range(24)) for day in range(1, 8)}
    profile = build_profile(_days(patterns))
    suggestion = suggest_quiet_hours(profile)
    assert not suggestion.ok
    assert "连续" in suggestion.reason


def test_refuses_when_silent_window_is_too_wide() -> None:
    """全天几乎都不活跃 → 更像记录不完整 → 不给建议。

    判据是**静默小时占比**而不是「区间长度」：夜间区间本来就长
    （19:00–09:00 是 14 小时），那正是最该建议的场景；
    「几乎全天不活跃」才是数据不足的信号。
    """
    # 每天只有 3 个小时有活动，其余 21 小时都不活跃 → 静默占比 87.5%
    patterns = {day: [9, 14, 18] for day in range(1, 8)}
    profile = build_profile(_days(patterns))
    suggestion = suggest_quiet_hours(profile)
    assert not suggestion.ok
    assert str(int(MAX_QUIET_DAY_RATIO * 100)) in suggestion.reason


def test_long_night_window_is_still_suggested() -> None:
    """夜间长是正常的，不该被「区间太长」拒掉。"""
    patterns = {day: list(range(9, 19)) for day in range(1, 8)}
    profile = build_profile(_days(patterns))
    suggestion = suggest_quiet_hours(profile)
    assert suggestion.ok
    assert suggestion.start == "19:00"
    assert suggestion.end == "09:00"


def test_quiet_window_shorter_than_minimum_is_refused() -> None:
    """太短的静默区间没有「别打扰」的意义 → 不给。"""
    # 每天只有 22 点前后的两小时不活跃，其余都活跃
    patterns = {day: [h for h in range(24) if h not in (23, 0)] for day in range(1, 8)}
    profile = build_profile(_days(patterns))
    suggestion = suggest_quiet_hours(profile)
    assert not suggestion.ok
    assert str(MIN_QUIET_HOURS) in suggestion.reason


def test_partially_active_hour_counts_as_active() -> None:
    """一半天数有活动的小时算**活跃**（保守：宁可少建议，不可多打扰）。"""
    # 20 点：4 天有活动、4 天没有 → 比例 0.5，不算静默
    patterns = {}
    for day in range(1, 9):
        patterns[day] = list(range(9, 19)) + ([20] if day <= 4 else [])
    profile = build_profile(_days(patterns))
    suggestion = suggest_quiet_hours(profile)
    # 20 点不该被划进安静区间
    assert suggestion.ok
    assert not (suggestion.start == "19:00" and suggestion.end == "21:00")


def test_suggestion_is_a_pure_function() -> None:
    """同样的输入永远同样的输出。"""
    patterns = {day: list(range(9, 19)) for day in range(1, 8)}
    profile = build_profile(_days(patterns))
    first = suggest_quiet_hours(profile)
    second = suggest_quiet_hours(profile)
    assert (first.start, first.end, first.confidence) == (second.start, second.end, second.confidence)


def test_describe_mentions_the_window() -> None:
    patterns = {day: list(range(9, 19)) for day in range(1, 8)}
    profile = build_profile(_days(patterns))
    text = suggest_quiet_hours(profile).describe()
    assert "19:00" in text and "09:00" in text
