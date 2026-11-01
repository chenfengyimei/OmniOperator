"""D36：记忆容量三档告警（抄 surfcore 记忆卡的 80/90/100 判定逻辑）。

参考实现：`xuexi/surfcore` 的 `src/memory-card.js`

    MEMORY_CAPACITY_THRESHOLDS = [0.8, 0.9, 1.0]
    level = 0
    if percentRatio >= THRESHOLDS[2] level = 3
    else if percentRatio >= THRESHOLDS[1] level = 2
    else if percentRatio >= THRESHOLDS[0] level = 1

    // 同一档 12 小时只通知一次
    shouldNotifyOwner(card, level, now, cooldownMs = 12h):
        return now - card.alerts.lastNotified[level] >= cooldownMs

**抄判定逻辑，不抄形态**。三处差异在 `memory/capacity.py` 的模块文档里写清了：
它有一张统一的大卡（两个参照：maxChars / warnChars），我们分容器各有限额；
它会自动合并记忆，我们只告警（我们有出处锚点，自动合并会破坏可回查的链）；
它的告警写进记忆卡 JSON，我们只做判定、处置交给调用方。

**全部离线**：不联网、不碰真实账号与真实目录、不调模型。
"""
from __future__ import annotations

from omni.memory.capacity import (
    COOLDOWN_SECONDS,
    TIER_CRITICAL,
    TIER_FULL,
    TIER_OK,
    TIER_WARN,
    CapacityUsage,
    collect_tiers,
    describe_usage,
    should_notify,
    tier_label,
    tier_message,
)


# ---------- 档位判定 ----------


def test_tiers_follow_80_90_100_thresholds() -> None:
    """三档阈值就是 80% / 90% / 100%。"""
    assert CapacityUsage("档案", 799, 1000).tier == TIER_OK
    assert CapacityUsage("档案", 800, 1000).tier == TIER_WARN
    assert CapacityUsage("档案", 899, 1000).tier == TIER_WARN
    assert CapacityUsage("档案", 900, 1000).tier == TIER_CRITICAL
    assert CapacityUsage("档案", 999, 1000).tier == TIER_CRITICAL
    assert CapacityUsage("档案", 1000, 1000).tier == TIER_FULL
    assert CapacityUsage("档案", 1200, 1000).tier == TIER_FULL


def test_unlimited_container_is_always_ok() -> None:
    """不限额 = 没有「到线」这回事（未知不是 0）。"""
    usage = CapacityUsage("档案", 999999, 0)
    assert usage.ratio is None
    assert usage.remaining_chars is None
    assert usage.tier == TIER_OK


def test_remaining_chars_is_not_zero_when_unlimited() -> None:
    """「不限」和「写满了」是两回事，不能都显示成 0。"""
    assert CapacityUsage("档案", 500, 0).remaining_chars is None
    assert CapacityUsage("档案", 500, 1000).remaining_chars == 500
    assert CapacityUsage("档案", 1500, 1000).remaining_chars == 0


# ---------- 收集与排序 ----------


def test_collect_tiers_picks_and_sorts() -> None:
    """只挑出到线的，并按严重度排序：full > critical > warn。"""
    usages = [
        CapacityUsage("常驻", 100, 1000),      # ok，不该出现
        CapacityUsage("档案", 850, 1000),      # warn
        CapacityUsage("技能", 1000, 1000),     # full
        CapacityUsage("人物", 950, 1000),      # critical
    ]
    hits = collect_tiers(usages)
    assert [u.container for u in hits] == ["技能", "人物", "档案"]
    assert [u.tier for u in hits] == [TIER_FULL, TIER_CRITICAL, TIER_WARN]


def test_collect_tiers_is_stable_for_same_inputs() -> None:
    """同样的输入永远同样的顺序——便于断言与展示。"""
    usages = [
        CapacityUsage("甲", 900, 1000),
        CapacityUsage("乙", 900, 1000),
        CapacityUsage("丙", 950, 1000),
    ]
    first = [u.container for u in collect_tiers(usages)]
    second = [u.container for u in collect_tiers(list(reversed(usages)))]
    assert first == second


# ---------- 文案 ----------


def test_describe_usage_without_limit_does_not_invent_percentage() -> None:
    """没有分母的比例是编出来的——不限额时只说已用多少。"""
    text = describe_usage(CapacityUsage("档案", 500, 0))
    assert "已用 500 字" in text
    assert "%" not in text


def test_describe_usage_with_limit_shows_ratio() -> None:
    text = describe_usage(CapacityUsage("档案", 850, 1000))
    assert "850 / 1000 字" in text
    assert "85.0%" in text


def test_tier_message_distinguishes_three_levels() -> None:
    """三档的**处置动作不同**，文案必须分开说。"""
    text = tier_message([
        CapacityUsage("档案", 1000, 1000),
        CapacityUsage("常驻", 950, 1000),
        CapacityUsage("人物", 850, 1000),
    ])
    assert "100%" in text
    assert "90%" in text
    assert "80%" in text
    assert "写不进去了" in text
    assert "建议现在就合并" in text
    assert "可以开始合并了" in text


def test_tier_message_is_empty_when_all_ok() -> None:
    assert tier_message([CapacityUsage("档案", 10, 1000)]) == ""


def test_tier_labels_are_distinct() -> None:
    labels = {tier_label(t) for t in (TIER_OK, TIER_WARN, TIER_CRITICAL, TIER_FULL)}
    assert len(labels) == 4


# ---------- 提醒冷却 ----------


def test_notify_respects_cooldown_per_level() -> None:
    """同一档 12 小时内不重复提醒；每档各记各的时间戳。

    `last_notified` 是**该档自己**的上次提醒时间——调用方按档分别存储
    （抄参考实现 `card.alerts.lastNotified[level]`）。所以升到新的一档时，
    新档的时间戳是 0（从没提醒过），立刻就该提醒。
    """
    now = 1_000_000.0
    # 从没提醒过 → 要提醒
    assert should_notify(1, 0, now)
    # 刚提醒过同一档 → 不提醒
    assert not should_notify(1, now - 60, now)
    # 冷却期过了 → 再提醒
    assert should_notify(1, now - COOLDOWN_SECONDS - 1, now)
    # 换了一档（新档自己的时间戳是 0）→ 立刻提醒，不受上一档的冷却影响
    assert should_notify(2, 0, now)
    # 新档自己刚提醒过 → 不提醒
    assert not should_notify(2, now - 60, now)


def test_notify_skips_level_zero() -> None:
    """ok 档没有需要提醒的事。"""
    assert not should_notify(0, 0, 1_000_000.0)
    assert not should_notify(-1, 0, 1_000_000.0)


def test_cooldown_is_twelve_hours() -> None:
    """抄参考实现的 12 小时冷却。"""
    assert COOLDOWN_SECONDS == 12 * 60 * 60


# ---------- 集成：容量提示真的出现在查看输出里 ----------


def _owner(tmp_path, *, archive_total: int = 100, resident_total: int = 100):
    """构造一个小额度的 owner 门面，好把用量推到各档。"""
    from omni.memory.owner import OwnerMemory
    from omni.memory.people import PersonStore
    from omni.memory.store import MemoryStore

    db = tmp_path / "m.db"
    store = MemoryStore(db)
    people = PersonStore(db)
    owner = OwnerMemory(people, archive_total=archive_total, resident_total=resident_total)
    return store, people, owner


def test_archive_view_shows_capacity_notice_at_80_percent(tmp_path):
    """八成就该提醒合并——不是等写不进去才说。"""
    store, people, owner = _owner(tmp_path)
    try:
        owner.profile_add("占位用的档案条目，需要足够长才能把水位撑到八成" * 2)
        used = owner.profile_usage_chars()
        # 精确落在 [80%, 90%) 区间，否则断言的档位标签会随字数漂移
        filler = "补" * max(1, 80 - used)
        owner.profile_add(filler)
        ratio = owner.profile_usage_chars() / 100
        assert 0.8 <= ratio < 0.9, f"用量没落在八成档：{ratio}"
        notice = owner.capacity_notice("archive")
        assert "接近满（80%）" in notice
        assert f"{owner.profile_usage_chars()}/100 字符" in notice
    finally:
        store.close()
        people.close()


def test_capacity_notice_is_empty_below_threshold(tmp_path):
    store, people, owner = _owner(tmp_path)
    try:
        owner.profile_add("短")
        assert owner.capacity_notice("archive") == ""
        assert owner.capacity_notice("resident") == ""
    finally:
        store.close()
        people.close()


def test_resident_view_includes_capacity_notice(tmp_path):
    """常驻记忆的 view 也要带容量提示（它每轮都进提示词，最该早点整理）。"""
    store, people, owner = _owner(tmp_path, resident_total=60)
    try:
        while owner.resident_usage_chars() < 48:
            owner.add("占位用的一条常驻笔记内容，需要足够长才能撑起水位" * 2)
        view = owner.view()
        assert "容量" in view
        assert "接近满（80%）" in view
    finally:
        store.close()
        people.close()


def test_profile_tool_view_surfaces_capacity(tmp_path):
    """profile_manage 的 view 也要把容量提示给模型看。"""
    from omni.memory.people import PersonStore
    from omni.memory.store import MemoryStore
    from omni.tools.personal.profile import register_profile_tools
    from omni.tools.registry import ToolRegistry

    db = tmp_path / "m.db"
    store = MemoryStore(db)
    people = PersonStore(db)
    from omni.memory.owner import OwnerMemory

    owner = OwnerMemory(people, archive_total=100)
    try:
        owner.profile_add("占位用的档案条目，需要足够长才能把水位撑到八成" * 2)
        owner.profile_add("补" * max(1, 80 - owner.profile_usage_chars()))
        ratio = owner.profile_usage_chars() / 100
        assert 0.8 <= ratio < 0.9, f"用量没落在八成档：{ratio}"
        registry = ToolRegistry()
        register_profile_tools(registry, owner)
        out = registry.dispatch("profile_manage", {"operation": "view"})
        assert "容量" in out
        assert "接近满（80%）" in out
    finally:
        store.close()
        people.close()
