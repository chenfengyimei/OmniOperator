"""费用额度分口径与阶梯告警的专项测试（P5-c）。

抄的是 surfcore `token-budget.js` 的判定逻辑：口径维度、80%/100% 阶梯、
"每天只提醒一次且落盘"。不抄形态：不写死 qq/bili/other 口径名、不用独立
JSON 文件存提醒状态、不做设置页。

三组：
1. 判定层（纯逻辑，可完全离线跑）；
2. 账本层（口径预留/结算、提醒权落盘、旧库迁移）；
3. 端到端（网关按口径拦截 → 回答里出现提醒 → 第二次不再提醒）。
"""

from __future__ import annotations

import sqlite3

import pytest

from omni.core.budget_alert import ScopeAlertTaker, scope_block_note, scope_warning
from omni.llm.tiers import (
    ALERT_RATIO,
    TIER_BLOCK,
    TIER_OK,
    TIER_WARN,
    ScopeUsage,
    collect_tiers,
    describe_scope,
    describe_tiers,
    normalize_scope,
    tier_message,
)

# ---------------------------------------------------------------- 判定层


def test_scope_usage_tier_is_ok_below_the_alert_line():
    usage = ScopeUsage(scope="qq", used_micro=700_000, limit_micro=1_000_000)
    assert usage.ratio == pytest.approx(0.7)
    assert usage.tier == TIER_OK


def test_scope_usage_tier_warns_at_the_alert_ratio():
    usage = ScopeUsage(scope="qq", used_micro=800_000, limit_micro=1_000_000)
    assert usage.tier == TIER_WARN
    assert usage.ratio == pytest.approx(ALERT_RATIO)


def test_scope_usage_tier_blocks_at_one_hundred_percent():
    usage = ScopeUsage(scope="qq", used_micro=1_000_000, limit_micro=1_000_000)
    assert usage.tier == TIER_BLOCK


def test_unlimited_scope_has_no_ratio_and_never_blocks():
    """未知不是 0：不限 ≠ 用完了 0%。"""
    usage = ScopeUsage(scope="web", used_micro=999_999_999, limit_micro=0)
    assert usage.ratio is None
    assert usage.tier == TIER_OK
    assert usage.remaining_micro is None


def test_remaining_micro_never_goes_negative():
    usage = ScopeUsage(scope="qq", used_micro=1_500_000, limit_micro=1_000_000)
    assert usage.remaining_micro == 0


def test_normalize_scope_folds_case_and_blank():
    assert normalize_scope("QQ") == "qq"
    assert normalize_scope("  web ") == "web"
    assert normalize_scope("") == "other"


def test_collect_tiers_orders_block_before_warn_then_by_ratio():
    usages = [
        ScopeUsage(scope="web", used_micro=820_000, limit_micro=1_000_000),
        ScopeUsage(scope="qq", used_micro=1_000_000, limit_micro=1_000_000),
        ScopeUsage(scope="cli", used_micro=900_000, limit_micro=1_000_000),
        ScopeUsage(scope="cron", used_micro=10, limit_micro=0),
    ]
    hits = collect_tiers(usages)
    assert [u.scope for u in hits] == ["qq", "cli", "web"]
    assert "cron" not in [u.scope for u in hits]  # 不限 = 没有档位可判


def test_collect_tiers_is_stable_for_equal_ratios():
    usages = [
        ScopeUsage(scope="web", used_micro=850_000, limit_micro=1_000_000),
        ScopeUsage(scope="qq", used_micro=850_000, limit_micro=1_000_000),
    ]
    first = [u.scope for u in collect_tiers(usages)]
    second = [u.scope for u in collect_tiers(list(reversed(usages)))]
    assert first == second == ["qq", "web"]


def test_describe_scope_shows_ratio_only_when_limited():
    limited = describe_scope(ScopeUsage(scope="qq", used_micro=850_000, limit_micro=1_000_000))
    assert "85.0%" in limited
    unlimited = describe_scope(ScopeUsage(scope="web", used_micro=100, limit_micro=0))
    assert "未设上限" in unlimited
    assert "%" not in unlimited  # 没有分母的比例是编出来的


def test_tier_message_separates_warn_and_block():
    usages = [
        ScopeUsage(scope="qq", used_micro=1_000_000, limit_micro=1_000_000),
        ScopeUsage(scope="web", used_micro=850_000, limit_micro=1_000_000),
    ]
    message = tier_message(usages)
    assert "已达上限" in message
    assert "八成" in message


def test_describe_tiers_joins_with_separator():
    usages = [
        ScopeUsage(scope="qq", used_micro=850_000, limit_micro=1_000_000),
        ScopeUsage(scope="web", used_micro=900_000, limit_micro=1_000_000),
    ]
    assert "；" in describe_tiers(usages)


# ---------------------------------------------------------------- 账本层


def _ledger(tmp_path):
    from omni.memory.state import StateStore
    from omni.memory.usage import UsageLedger

    return UsageLedger(StateStore(tmp_path / "state.db"))


def test_scope_reserve_accumulates_and_settles(tmp_path):
    ledger = _ledger(tmp_path)
    day = "2026-11-01"
    assert ledger.scope_reserve(day=day, scope="qq", micro=100_000, limit_micro=1_000_000)[0]
    assert ledger.scope_reserve(day=day, scope="qq", micro=200_000, limit_micro=1_000_000)[0]
    assert ledger.scope_total(day, "qq")["used_micro"] == 300_000

    ledger.scope_settle(day=day, scope="qq", reserved_micro=300_000, charged_micro=250_000)
    total = ledger.scope_total(day, "qq")
    assert total["reserved_micro"] == 0
    assert total["spent_micro"] == 250_000


def test_scope_reserve_blocks_when_the_scope_limit_is_reached(tmp_path):
    ledger = _ledger(tmp_path)
    day = "2026-11-01"
    ok, used = ledger.scope_reserve(day=day, scope="qq", micro=900_000, limit_micro=1_000_000)
    assert ok
    assert used == 900_000
    ok, used = ledger.scope_reserve(day=day, scope="qq", micro=200_000, limit_micro=1_000_000)
    assert not ok
    assert used == 900_000  # 没预留进去，账没动


def test_unlimited_scope_records_but_never_blocks(tmp_path):
    ledger = _ledger(tmp_path)
    day = "2026-11-01"
    for _ in range(5):
        ok, _used = ledger.scope_reserve(day=day, scope="web", micro=1_000_000, limit_micro=0)
        assert ok
    assert ledger.scope_total(day, "web")["used_micro"] == 5_000_000


def test_scopes_are_accounted_separately(tmp_path):
    ledger = _ledger(tmp_path)
    day = "2026-11-01"
    ledger.scope_reserve(day=day, scope="qq", micro=300_000, limit_micro=0)
    ledger.scope_reserve(day=day, scope="web", micro=700_000, limit_micro=0)
    totals = ledger.scope_totals(day)
    assert totals["qq"]["used_micro"] == 300_000
    assert totals["web"]["used_micro"] == 700_000


def test_take_scope_alert_grants_the_right_only_once_per_day(tmp_path):
    """抄参考实现 takeAlert 的核心：提醒权当天只发一次，且落盘。"""
    ledger = _ledger(tmp_path)
    day = "2026-11-01"
    assert ledger.take_scope_alert(day=day, scope="qq") is True
    assert ledger.take_scope_alert(day=day, scope="qq") is False


def test_take_scope_alert_is_independent_per_scope(tmp_path):
    """口径之间并列：QQ 提醒过不该让网页端也失去提醒权。"""
    ledger = _ledger(tmp_path)
    day = "2026-11-01"
    assert ledger.take_scope_alert(day=day, scope="qq") is True
    assert ledger.take_scope_alert(day=day, scope="web") is True


def test_take_scope_alert_resets_next_day(tmp_path):
    ledger = _ledger(tmp_path)
    assert ledger.take_scope_alert(day="2026-11-01", scope="qq") is True
    assert ledger.take_scope_alert(day="2026-11-01", scope="qq") is False
    assert ledger.take_scope_alert(day="2026-11-02", scope="qq") is True


def test_scope_alerts_lists_what_was_alerted(tmp_path):
    ledger = _ledger(tmp_path)
    ledger.take_scope_alert(day="2026-11-01", scope="qq")
    ledger.take_scope_alert(day="2026-11-01", scope="web", level="block")
    rows = ledger.scope_alerts("2026-11-01")
    assert {r["scope"] for r in rows} == {"qq", "web"}
    assert {r["level"] for r in rows} == {"warn", "block"}


def test_old_state_db_migrates_keeping_existing_ledger_rows(tmp_path):
    """v14 → v15：存量总额账与调用流水一字不动，新表是纯增量。"""
    from omni.memory.state import StateStore
    from omni.memory.usage import UsageLedger

    path = tmp_path / "state.db"
    conn = sqlite3.connect(path)
    conn.executescript(
        """
        CREATE TABLE meta (key TEXT PRIMARY KEY, value TEXT NOT NULL);
        INSERT INTO meta(key, value) VALUES ('schema_version', '14');
        CREATE TABLE llm_budget (
            day TEXT PRIMARY KEY,
            reserved_micro INTEGER NOT NULL DEFAULT 0,
            spent_micro INTEGER NOT NULL DEFAULT 0
        );
        INSERT INTO llm_budget(day, reserved_micro, spent_micro)
            VALUES ('2026-11-01', 0, 123456);
        CREATE TABLE llm_calls (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            run_id TEXT, session_id INTEGER, purpose TEXT NOT NULL DEFAULT '',
            model TEXT NOT NULL DEFAULT '', prompt_tokens INTEGER, completion_tokens INTEGER,
            cost_micro INTEGER, cost_source TEXT NOT NULL DEFAULT 'unknown',
            outcome TEXT NOT NULL DEFAULT 'ok', error_code TEXT NOT NULL DEFAULT '',
            attempts INTEGER NOT NULL DEFAULT 1, duration_ms INTEGER NOT NULL DEFAULT 0,
            started_at TEXT NOT NULL, detail TEXT NOT NULL DEFAULT ''
        );
        INSERT INTO llm_calls(purpose, model, cost_micro, started_at)
            VALUES ('chat', 'm', 50000, '2026-11-01T10:00:00');
        """
    )
    conn.commit()
    conn.close()

    store = StateStore(path)
    assert store.schema_version == 15
    ledger = UsageLedger(store)
    assert ledger.day_total("2026-11-01")["spent_micro"] == 123456
    assert len(ledger.recent(10)) == 1


# ---------------------------------------------------------------- 告警层


class _FakeLedger:
    """只实现领取提醒权的方法：其余账本能力用不到。"""

    def __init__(self) -> None:
        self.taken: list[tuple[str, str]] = []

    def take_scope_alert(self, *, day: str, scope: str, level: str = "warn") -> bool:
        key = (day, scope)
        if key in self.taken:
            return False
        self.taken.append(key)
        return True


def test_scope_warning_is_empty_when_nothing_reached_the_line():
    usages = [ScopeUsage(scope="qq", used_micro=100_000, limit_micro=1_000_000)]
    assert scope_warning(usages) == ""


def test_scope_warning_appears_once_then_stays_quiet():
    usages = [ScopeUsage(scope="qq", used_micro=850_000, limit_micro=1_000_000)]
    ledger = _FakeLedger()
    taker = ScopeAlertTaker(ledger)
    first = scope_warning(usages, taker=taker)
    assert "额度提醒" in first
    assert "qq" in first
    assert scope_warning(usages, taker=taker) == ""


def test_scope_warning_without_a_taker_always_reports():
    """没有落盘闸门时不拦自己——上层没接账本时至少要让用户看得见。"""
    usages = [ScopeUsage(scope="qq", used_micro=850_000, limit_micro=1_000_000)]
    assert scope_warning(usages) != ""
    assert scope_warning(usages) != ""


def test_scope_warning_ignores_the_blocked_tier():
    """到 100% 的提示由拦截路径给，这里不重复。"""
    usages = [ScopeUsage(scope="qq", used_micro=1_000_000, limit_micro=1_000_000)]
    assert scope_warning(usages) == ""
    assert "已达上限" in scope_block_note(usages)


def test_scope_warning_only_reports_freshly_alerted_scopes():
    usages = [
        ScopeUsage(scope="qq", used_micro=850_000, limit_micro=1_000_000),
        ScopeUsage(scope="web", used_micro=860_000, limit_micro=1_000_000),
    ]
    ledger = _FakeLedger()
    taker = ScopeAlertTaker(ledger, clock=lambda: "2026-11-01")
    ledger.take_scope_alert(day="2026-11-01", scope="qq")  # 同一注入日期内只提醒一次
    text = scope_warning(usages, taker=taker)
    assert "web" in text
    assert "qq" not in text


def test_alert_taker_swallows_ledger_errors():
    """账本坏了就不提醒——宁可漏一次，也不要每个进程各弹一遍。"""

    class _Broken:
        def take_scope_alert(self, **_kw):
            raise RuntimeError("db gone")

    taker = ScopeAlertTaker(_Broken())
    assert taker.take(scope="qq") is False


# ---------------------------------------------------------------- 端到端


def _gateway(tmp_path, *, scope_limits: dict[str, float]):
    from omni.config import LLM, Models
    from omni.llm.gateway import Gateway
    from omni.memory.state import StateStore
    from omni.memory.usage import UsageLedger

    state = StateStore(tmp_path / "state.db")
    ledger = UsageLedger(state)
    llm = LLM()
    llm.budget.scope_daily_usd = dict(scope_limits)
    return Gateway(Models(chat="test-model"), llm=llm, ledger=ledger), ledger


def _reply(monkeypatch, gateway, *, cost_micro: int):
    """造一个假的供应商回复，让网关走完结算。"""
    from omni.llm.gateway import Reply

    class _Msg:
        content = "好的"
        tool_calls = None

    class _Choice:
        message = _Msg()

    class _Response:
        choices = [_Choice()]

    monkeypatch.setattr(gateway, "_call", lambda *a, **k: (_Response(), 1))
    monkeypatch.setattr("omni.llm.gateway.micro_from_usd", lambda usd: cost_micro)
    return Reply


def test_gateway_blocks_when_the_scope_limit_is_reached(tmp_path, monkeypatch):
    """口径上限比这次调用还小 → 硬拦截（口径超了也是硬拦截，和总额一样）。"""
    from omni.llm import errors

    gateway, _ledger = _gateway(tmp_path, scope_limits={"qq": 0.5})
    monkeypatch.setattr("omni.llm.gateway.micro_from_usd", lambda usd: 800_000)
    with pytest.raises(errors.GatewayError) as err:
        gateway.complete([{"role": "user", "content": "hi"}], scope="qq")
    assert err.value.code == errors.BUDGET
    assert "qq" in err.value.message


def test_gateway_allows_a_call_that_fits_the_scope_limit(tmp_path, monkeypatch):
    gateway, ledger = _gateway(tmp_path, scope_limits={"qq": 1.0})
    _reply(monkeypatch, gateway, cost_micro=400_000)
    gateway.complete([{"role": "user", "content": "hi"}], scope="qq")
    total = ledger.scope_total(__import__("datetime").date.today().isoformat(), "qq")
    assert total["spent_micro"] == 400_000


def test_gateway_scope_limit_does_not_block_other_scopes(tmp_path, monkeypatch):
    """QQ 到线不该拦住网页端——这就是分口径的意义。"""
    gateway, ledger = _gateway(tmp_path, scope_limits={"qq": 0.5})
    monkeypatch.setattr("omni.llm.gateway.micro_from_usd", lambda usd: 800_000)
    _reply(monkeypatch, gateway, cost_micro=800_000)
    gateway.complete([{"role": "user", "content": "hi"}], scope="web")
    total = ledger.scope_total(__import__("datetime").date.today().isoformat(), "web")
    assert total["spent_micro"] == 800_000


def test_gateway_records_the_scope_even_without_a_limit(tmp_path, monkeypatch):
    gateway, ledger = _gateway(tmp_path, scope_limits={})
    _reply(monkeypatch, gateway, cost_micro=123_456)
    gateway.complete([{"role": "user", "content": "hi"}], scope="web")
    total = ledger.scope_total(__import__("datetime").date.today().isoformat(), "web")
    assert total["spent_micro"] == 123_456


def test_gateway_scope_alerts_reports_the_tier(tmp_path, monkeypatch):
    gateway, ledger = _gateway(tmp_path, scope_limits={"qq": 1.0})
    day = __import__("datetime").date.today().isoformat()
    ledger.scope_reserve(day=day, scope="qq", micro=850_000, limit_micro=0)
    rows = gateway.scope_alerts()
    assert any(r["scope"] == "qq" and r["tier"] == TIER_WARN for r in rows)
