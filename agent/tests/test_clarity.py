"""清晰度阶梯单测：纯判定层 + 存储层接入。

对应 P5-a（抄 surfcore `memory-card.js` 的**判定逻辑**，不抄形态）。
纯逻辑部分完全不碰数据库与服务，可离线跑；存储部分用临时目录。
"""

import datetime as dt

from omni.memory.clarity import (
    CLARITY_CLEAR,
    CLARITY_FORGOTTEN,
    CLARITY_FUZZY,
    CLARITY_ORDER,
    CLARITY_TRACE,
    CLARITY_VIVID,
    ClarityConfig,
    apply_daily_decay,
    clarity_index,
    is_recallable,
    normalize_clarity,
    review_effect,
    target_clarity,
)
from omni.memory.store import (
    RECALL_CLARITY_CAP,
    MemoryStore,
    clarity_penalty,
)


def _cfg(**kwargs) -> ClarityConfig:
    base = {
        "recent_days": 7,
        "fuzzy_after_days": 30,
        "trace_after_days": 90,
        "forget_after_days": 180,
        "review_grace_days": 7,
    }
    base.update(kwargs)
    return ClarityConfig(**base)


TODAY = dt.date(2026, 11, 1)


# ---------------------------------------------------------------- 纯逻辑


def test_clarity_order_is_five_stages_from_clear_to_forgotten():
    assert CLARITY_ORDER == ("vivid", "clear", "fuzzy", "trace", "forgotten")
    assert clarity_index(CLARITY_VIVID) == 0
    assert clarity_index(CLARITY_CLEAR) == 1
    assert clarity_index(CLARITY_FORGOTTEN) == 4


def test_unknown_clarity_falls_back_to_clear_not_forgotten():
    """脏值按 clear 处理——宁可高估记忆的清楚程度，也不要把用户的记忆判死。"""
    assert normalize_clarity("乱七八糟") == CLARITY_CLEAR
    assert normalize_clarity(None) == CLARITY_CLEAR
    assert normalize_clarity("") == CLARITY_CLEAR
    assert clarity_index("不存在的档位") == clarity_index(CLARITY_CLEAR)


def test_target_clarity_uses_age_since_last_use_not_since_creation():
    """近因比远因重要：三个月前写的、上周刚用过的，比上个月写的更清楚。"""
    cfg = _cfg()
    # 写入于 200 天前（早该忘了），但上周刚被用过 → 仍在复习保护期内
    target = target_clarity(
        created_at="2026-04-01T10:00:00",
        last_used="2026-10-28T10:00:00",
        today=TODAY,
        config=cfg,
    )
    assert target == CLARITY_VIVID
    # 同样老、也从没被用过 → 按写入时间算，确实该忘了
    target = target_clarity(
        created_at="2026-04-01T10:00:00",
        last_used=None,
        today=TODAY,
        config=cfg,
    )
    assert target == CLARITY_FORGOTTEN


def test_target_clarity_thresholds():
    """各档位的天数边界（相对 TODAY 算，避免"日期写错就红"）。"""
    cfg = _cfg()
    # 2 天前：还在 recent_days 内 → vivid
    assert target_clarity(created_at="2026-10-30", last_used=None, today=TODAY, config=cfg) == CLARITY_VIVID
    # 17 天前：超过 recent_days、不到 fuzzy_after_days → clear
    assert target_clarity(created_at="2026-10-15", last_used=None, today=TODAY, config=cfg) == CLARITY_CLEAR
    # 61 天前 → fuzzy
    assert target_clarity(created_at="2026-09-01", last_used=None, today=TODAY, config=cfg) == CLARITY_FUZZY
    # 153 天前 → trace
    assert target_clarity(created_at="2026-06-01", last_used=None, today=TODAY, config=cfg) == CLARITY_TRACE
    # 304 天前 → forgotten
    assert target_clarity(created_at="2026-01-01", last_used=None, today=TODAY, config=cfg) == CLARITY_FORGOTTEN


def test_target_clarity_without_any_timestamp_does_not_judge():
    """连时间都没有 → 不做任何判断（降档需要"多老"这个证据）。"""
    assert target_clarity(created_at=None, last_used=None, today=TODAY, config=_cfg()) == CLARITY_VIVID
    assert target_clarity(created_at="坏时间", last_used=None, today=TODAY, config=_cfg()) == CLARITY_VIVID


def test_daily_decay_moves_at_most_one_step():
    """每天最多降一格——一次跳两格会让用户觉得记忆在乱丢。"""
    cfg = _cfg()
    # clear → 目标 fuzzy（61 天没被用过），本次只降一格
    after = apply_daily_decay(
        CLARITY_CLEAR,
        created_at="2026-09-01",  # 61 天前 → 目标是 fuzzy
        last_used=None,
        today=TODAY,
        config=cfg,
    )
    assert after == CLARITY_FUZZY
    # 到了目标就不再往下掉（目标由年龄决定，年龄不变则档位不变）
    after = apply_daily_decay(
        after,
        created_at="2026-09-01",
        last_used=None,
        today=TODAY,
        config=cfg,
    )
    assert after == CLARITY_FUZZY


def test_daily_decay_steps_toward_a_far_target_one_rung_per_day():
    """目标还很远（早该忘了）时，也是一天只走一格。"""
    cfg = _cfg()
    level = CLARITY_CLEAR
    for _ in range(3):
        level = apply_daily_decay(level, created_at="2020-01-01", last_used=None, today=TODAY, config=cfg)
    # 三天走了三格：clear → fuzzy → trace → forgotten
    assert level == CLARITY_FORGOTTEN


def test_daily_decay_never_jumps_straight_to_forgotten():
    """即使早该忘了，也要一格一格走完。"""
    cfg = _cfg()
    level = CLARITY_CLEAR
    seen = [level]
    for _ in range(6):
        level = apply_daily_decay(level, created_at="2020-01-01", last_used=None, today=TODAY, config=cfg)
        seen.append(level)
    # 第一次衰减绝不直接到 forgotten
    assert seen[1] == CLARITY_FUZZY
    assert CLARITY_FORGOTTEN in seen


def test_daily_decay_does_not_upgrade():
    """降档函数不负责升档——升档是复习效应的事，两条语义分开。"""
    cfg = _cfg()
    after = apply_daily_decay(
        CLARITY_TRACE,
        created_at="2026-10-30",  # 很新 → 目标是 vivid
        last_used=None,
        today=TODAY,
        config=cfg,
    )
    assert after == CLARITY_TRACE


def test_review_effect_upgrades_one_step_and_stops_at_vivid():
    assert review_effect(CLARITY_FUZZY) == CLARITY_CLEAR
    assert review_effect(CLARITY_TRACE) == CLARITY_FUZZY
    assert review_effect(CLARITY_FORGOTTEN) == CLARITY_TRACE
    assert review_effect(CLARITY_VIVID) == CLARITY_VIVID


def test_review_effect_never_goes_beyond_vivid():
    assert review_effect(CLARITY_VIVID) == CLARITY_VIVID


def test_disabled_config_is_a_no_op():
    cfg = _cfg(enabled=False)
    assert target_clarity(created_at="2020-01-01", last_used=None, today=TODAY, config=cfg) == CLARITY_VIVID
    assert (
        apply_daily_decay(CLARITY_CLEAR, created_at="2020-01-01", last_used=None, today=TODAY, config=cfg)
        == CLARITY_CLEAR
    )


def test_forgotten_is_still_recallable():
    """阶梯只降权不排除——库里明明有，答"没有找到"就是谎报。"""
    assert is_recallable(CLARITY_FORGOTTEN) is True
    assert is_recallable(CLARITY_VIVID) is True


# ---------------------------------------------------------------- 排序惩罚


def test_clarity_penalty_is_zero_for_normal_memories():
    assert clarity_penalty(CLARITY_VIVID) == 0.0
    assert clarity_penalty(CLARITY_CLEAR) == 0.0
    assert clarity_penalty(None) == 0.0


def test_clarity_penalty_grows_and_is_capped():
    assert clarity_penalty(CLARITY_FUZZY) > 0.0
    assert clarity_penalty(CLARITY_TRACE) > clarity_penalty(CLARITY_FUZZY)
    assert clarity_penalty(CLARITY_FORGOTTEN) <= RECALL_CLARITY_CAP
    # 陈旧不等于该被埋掉：惩罚封顶，不会把一条记忆从第 1 位压到末尾
    assert RECALL_CLARITY_CAP <= 2.0


# ---------------------------------------------------------------- 存储层


def test_new_fact_starts_vivid(tmp_path):
    store = MemoryStore(tmp_path / "m.db")
    store.save_fact("用户喜欢喝拿铁")
    rows = store.fact_rows()
    assert rows[0]["clarity"] == CLARITY_VIVID
    store.close()


def test_decay_only_downgrades_and_never_deletes(tmp_path):
    store = MemoryStore(tmp_path / "m.db")
    store.save_fact("很久以前说过的事")
    # 把这条事实的写入时间挪到很久以前，模拟"从没被用过的老记忆"
    store._conn.execute("UPDATE facts SET created_at='2020-01-01T00:00:00', last_used=NULL")
    store._conn.commit()
    before = store.fact_rows()
    assert len(before) == 1

    result = store.decay_facts_clarity(today=TODAY)
    assert result["changed"] == 1
    assert result["total"] == 1
    # 行还在——阶梯不删数据
    assert len(store.fact_rows()) == 1
    # 新写入是 vivid，一格衰减 → clear（要到 forgotten 得再降几格）
    assert store.fact_rows()[0]["clarity"] == CLARITY_CLEAR
    store.close()


def test_decay_reports_distribution(tmp_path):
    store = MemoryStore(tmp_path / "m.db")
    store.save_fact("A")
    store.save_fact("B")
    result = store.decay_facts_clarity(today=TODAY)
    assert result["day"] == "2026-11-01"
    assert result["total"] == 2
    assert sum(result["by_clarity"].values()) == 2
    store.close()


def test_decay_runs_at_most_once_per_day(tmp_path):
    """整理循环每 5 分钟一轮——没有每日闸门，"每天一格"会变成"每 5 分钟一格"。

    这是真实缺陷的回归测试：衰减必须在同一天内幂等。
    """
    store = MemoryStore(tmp_path / "m.db")
    store.save_fact("很久以前说过的事")
    store._conn.execute("UPDATE facts SET created_at='2020-01-01T00:00:00', last_used=NULL")
    store._conn.commit()

    first = store.decay_facts_clarity(today=TODAY)
    assert first["changed"] == 1
    assert first["skipped"] is False
    # 同一天再跑（整理循环下一轮）：必须空转，不能再降一格
    second = store.decay_facts_clarity(today=TODAY)
    assert second["changed"] == 0
    assert second["skipped"] is True
    assert store.fact_rows()[0]["clarity"] == CLARITY_CLEAR
    store.close()


def test_decay_next_day_moves_another_rung(tmp_path):
    """跨天才继续降——闸门是"一天一次"，不是"永不降"。"""
    store = MemoryStore(tmp_path / "m.db")
    store.save_fact("很久以前说过的事")
    store._conn.execute("UPDATE facts SET created_at='2020-01-01T00:00:00', last_used=NULL")
    store._conn.commit()
    store.decay_facts_clarity(today=TODAY)
    assert store.fact_rows()[0]["clarity"] == CLARITY_CLEAR
    result = store.decay_facts_clarity(today=TODAY + dt.timedelta(days=1))
    assert result["changed"] == 1
    assert result["skipped"] is False
    assert store.fact_rows()[0]["clarity"] == CLARITY_FUZZY
    store.close()


def test_decay_force_bypasses_the_daily_gate(tmp_path):
    """手工补跑用 force=True 绕过闸门（运维/测试需要立即看到效果）。"""
    store = MemoryStore(tmp_path / "m.db")
    store.save_fact("很久以前说过的事")
    store._conn.execute("UPDATE facts SET created_at='2020-01-01T00:00:00', last_used=NULL")
    store._conn.commit()
    store.decay_facts_clarity(today=TODAY)
    forced = store.decay_facts_clarity(today=TODAY, force=True)
    assert forced["changed"] == 1
    assert forced["skipped"] is False
    assert store.fact_rows()[0]["clarity"] == CLARITY_FUZZY
    store.close()


def test_decay_leaves_fresh_memories_alone(tmp_path):
    store = MemoryStore(tmp_path / "m.db")
    store.save_fact("刚写的事")
    result = store.decay_facts_clarity(today=TODAY)
    assert result["changed"] == 0
    assert store.fact_rows()[0]["clarity"] == CLARITY_VIVID
    store.close()


def test_review_clarity_upgrades_on_recall(tmp_path):
    store = MemoryStore(tmp_path / "m.db")
    store.save_fact("用户养了一只猫叫煤球")
    store._conn.execute("UPDATE facts SET clarity='fuzzy'")
    store._conn.commit()
    changed = store.review_facts_clarity(["用户养了一只猫叫煤球"], scope="private")
    assert changed == 1
    assert store.fact_rows()[0]["clarity"] == CLARITY_CLEAR
    store.close()


def test_review_clarity_respects_scope(tmp_path):
    """群作用域的记忆不该被私聊的召回升档。"""
    store = MemoryStore(tmp_path / "m.db")
    store.save_fact("群里的事", scope="group")
    store._conn.execute("UPDATE facts SET clarity='fuzzy'")
    store._conn.commit()
    changed = store.review_facts_clarity(["群里的事"], scope="private")
    assert changed == 0
    assert store.fact_rows()[0]["clarity"] == CLARITY_FUZZY
    store.close()


def test_review_clarity_is_idempotent_at_vivid(tmp_path):
    store = MemoryStore(tmp_path / "m.db")
    store.save_fact("已经是vivid")
    assert store.review_facts_clarity(["已经是vivid"], scope="private") == 0
    assert store.fact_rows()[0]["clarity"] == CLARITY_VIVID
    store.close()


def test_review_clarity_upgrades_at_most_once_per_day(tmp_path):
    """一轮对话里模型可能连着 recall 同一批事实多次——每次都升档就成"刷"出来的。

    升档每天最多一次：反复召回不会把一条 forgotten 记忆几分钟拉回 vivid。
    """
    store = MemoryStore(tmp_path / "m.db")
    store.save_fact("用户养了一只猫叫煤球")
    store._conn.execute("UPDATE facts SET clarity='forgotten', clarity_reviewed_at=NULL")
    store._conn.commit()
    # 第一次召回：升一格
    assert store.review_facts_clarity(["用户养了一只猫叫煤球"], scope="private") == 1
    assert store.fact_rows()[0]["clarity"] == CLARITY_TRACE
    # 同一天再召回：不再升
    assert store.review_facts_clarity(["用户养了一只猫叫煤球"], scope="private") == 0
    assert store.fact_rows()[0]["clarity"] == CLARITY_TRACE
    store.close()


def test_review_clarity_can_upgrade_again_next_day(tmp_path):
    """跨天可以继续升档——闸门是"一天一次"，不是"只能升一次"。"""
    store = MemoryStore(tmp_path / "m.db")
    store.save_fact("用户养了一只猫叫煤球")
    store._conn.execute("UPDATE facts SET clarity='forgotten', clarity_reviewed_at='2020-01-01T00:00:00'")
    store._conn.commit()
    assert store.review_facts_clarity(["用户养了一只猫叫煤球"], scope="private") == 1
    assert store.fact_rows()[0]["clarity"] == CLARITY_TRACE
    store.close()


def test_clarity_demotes_but_never_hides(tmp_path):
    """陈旧记忆仍可被召回（"查得到"是硬约定），只是排在后面。"""
    store = MemoryStore(tmp_path / "m.db")
    store.save_fact("用户喜欢喝拿铁")
    store.save_fact("用户养了一只猫叫煤球")
    store._conn.execute("UPDATE facts SET clarity='forgotten' WHERE content LIKE '%拿铁%'")
    store._conn.commit()
    hits = store.search_facts("拿铁 猫", limit=10)
    assert any("拿铁" in h for h in hits), "陈旧记忆不得从结果里消失"
    assert any("煤球" in h for h in hits)


def test_equal_clarity_keeps_pure_chain_order(tmp_path):
    """全部记忆清晰度相同时，排序与纯链序一致——冻结检索评测不受影响。"""
    store = MemoryStore(tmp_path / "m.db")
    for text in ("用户喜欢喝拿铁", "用户养了一只猫叫煤球", "项目发布会安排在周五下午三点"):
        store.save_fact(text)
    rows = store.search_fact_rows("用户 猫 拿铁 发布会", limit=10)
    hits = store.search_facts("用户 猫 拿铁 发布会", limit=10)
    assert [r["content"] for r in rows] == hits
    store.close()


def test_fact_clarities_lookup(tmp_path):
    store = MemoryStore(tmp_path / "m.db")
    store.save_fact("甲")
    store.save_fact("乙")
    store._conn.execute("UPDATE facts SET clarity='trace' WHERE content='甲'")
    store._conn.commit()
    got = store.fact_clarities(["甲", "乙", "不存在"], scope="private")
    assert got["甲"] == CLARITY_TRACE
    assert got["乙"] == CLARITY_VIVID
    assert "不存在" not in got
    store.close()


def test_old_database_gets_clarity_column(tmp_path):
    """旧库补列：存量事实统一是 clear，不会因升级突然"变旧"。"""
    import sqlite3

    path = tmp_path / "m.db"
    conn = sqlite3.connect(path)
    conn.execute(
        "CREATE TABLE facts (id INTEGER PRIMARY KEY AUTOINCREMENT, content TEXT NOT NULL,"
        " created_at TEXT NOT NULL, updated_at TEXT, source TEXT NOT NULL DEFAULT 'chat',"
        " scope TEXT NOT NULL DEFAULT 'private', session_id INTEGER, superseded_by INTEGER,"
        " hits INTEGER NOT NULL DEFAULT 0, last_used TEXT)"
    )
    conn.execute(
        "INSERT INTO facts(content, created_at, source, scope) VALUES ('旧事实','2026-01-01','chat','private')"
    )
    conn.commit()
    conn.close()

    store = MemoryStore(path)
    rows = store.fact_rows()
    assert rows[0]["clarity"] == CLARITY_CLEAR
    # 复习戳列也补上了（否则旧库调用复习会因缺列报错）
    assert store.review_facts_clarity(["旧事实"], scope="private") == 1
    assert store.fact_rows()[0]["clarity"] == CLARITY_VIVID
    store.close()
