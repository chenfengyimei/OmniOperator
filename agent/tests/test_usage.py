"""费用预算与调用流水（D10）。

预算是**确定性代码约束**，所以测试只认两件事：
1. 超额的请求**一次都没有发出去**（假 `_completion` 的调用次数为零）；
2. 账本上的数字对得上（预留退回、实付入账、未知记 NULL 而不是 0）。
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

import pytest

from omni.config import AppConfig, LLM, LLMBudget, Limits, Models, ModelCapability
from omni.core.agent import Agent
from omni.core.run import RunBudget
from omni.llm import gateway as gw
from omni.llm import errors
from omni.llm.budget import RunBudget as RunBudgetFromLlm
from omni.llm.usage import MICRO
from omni.memory.state import StateStore
from omni.memory.store import MemoryStore
from omni.memory.usage import UsageLedger
from omni.tools.registry import ToolRegistry

# ---------------------------------------------------------------- 假响应


@dataclass
class _Msg:
    content: str = "OK"
    tool_calls: list[Any] | None = None


@dataclass
class _Usage:
    prompt_tokens: int
    completion_tokens: int


@dataclass
class _Resp:
    choices: list[Any] = field(default_factory=lambda: [_Msg()])
    usage: Any = None


def _resp(text: str = "OK", *, prompt: int = 10, completion: int = 5) -> _Resp:
    usage = _Usage(prompt, completion) if prompt is not None else None
    return _Resp(choices=[type("_C", (), {"message": _Msg(content=text)})()], usage=usage)


def _exc(name: str, text: str = "boom") -> Exception:
    return type(name, (Exception,), {})(text)


@pytest.fixture()
def state(tmp_path):
    store = StateStore(tmp_path / "state.db")
    yield store
    store.close()


@pytest.fixture()
def ledger(state: StateStore) -> UsageLedger:
    return UsageLedger(state)


# ---------------------------------------------------------------- 账本本体


def test_reserve_is_bounded_and_atomic(ledger: UsageLedger) -> None:
    ok, used = ledger.reserve(day="2026-01-01", micro=60, limit_micro=100)
    assert ok and used == 60
    ok, used = ledger.reserve(day="2026-01-01", micro=50, limit_micro=100)
    assert ok is False and used == 60  # 不会因为失败而改变已用量
    ok, _ = ledger.reserve(day="2026-01-01", micro=40, limit_micro=100)
    assert ok is True


def test_reserve_is_atomic_across_connections(tmp_path) -> None:
    """两个进程（两个连接）同时抢最后一份额度，只有一个能拿到。"""
    a = StateStore(tmp_path / "state.db")
    b = StateStore(tmp_path / "state.db")
    try:
        la, lb = UsageLedger(a), UsageLedger(b)
        first, _ = la.reserve(day="d", micro=60, limit_micro=100)
        second, used = lb.reserve(day="d", micro=60, limit_micro=100)
        assert (first, second) == (True, False)
        assert used == 60
    finally:
        a.close()
        b.close()


def test_settle_releases_reservation_and_books_actual(ledger: UsageLedger) -> None:
    ledger.reserve(day="d", micro=100, limit_micro=1000)
    ledger.settle(day="d", reserved_micro=100, charged_micro=30)
    totals = ledger.day_total("d")
    assert totals == {"reserved_micro": 0, "spent_micro": 30, "used_micro": 30}


def test_settle_after_day_rollover_still_books(ledger: UsageLedger) -> None:
    ledger.reserve(day="d1", micro=100, limit_micro=1000)
    ledger.settle(day="d2", reserved_micro=100, charged_micro=42)
    assert ledger.day_total("d2")["spent_micro"] == 42
    assert ledger.day_total("d1")["reserved_micro"] == 100  # 旧账留给人工核对


def test_unknown_price_is_null_not_zero(ledger: UsageLedger) -> None:
    ledger.record(purpose="chat", model="foo/bar", cost_micro=None, cost_source="unknown")
    record = ledger.recent(1)[0]
    assert record.cost_micro is None
    assert record.brief()["cost_known"] is False
    summary = ledger.summary(days=1)
    assert summary["cost_micro"] == 0 and summary["unknown_cost"] == 1


def test_summary_groups_by_purpose(ledger: UsageLedger) -> None:
    ledger.record(purpose="chat", model="m", cost_micro=100)
    ledger.record(purpose="review", model="m", cost_micro=50)
    ledger.record(purpose="review", model="m", cost_micro=None, cost_source="unknown")
    summary = ledger.summary(days=1)
    by = {item["purpose"]: item for item in summary["by_purpose"]}
    assert by["chat"]["cost_micro"] == 100
    assert by["review"]["calls"] == 2 and by["review"]["unknown_cost"] == 1
    assert summary["calls"] == 3


# ---------------------------------------------------------------- 网关 + 预算


def _gateway(monkeypatch, script, llm: LLM, ledger: UsageLedger) -> gw.Gateway:
    monkeypatch.setattr(gw, "_completion", script)
    return gw.Gateway(
        Models(chat="deepseek/deepseek-chat"),
        llm=llm,
        ledger=ledger,
        sleep=lambda _s: None,
        clock=lambda: 0.0,
    )


class _Script:
    def __init__(self, *outcomes: Any) -> None:
        self.outcomes = list(outcomes)
        self.calls: list[dict[str, Any]] = []

    def __call__(self, **kwargs: Any) -> Any:
        self.calls.append(kwargs)
        outcome = self.outcomes.pop(0)
        if isinstance(outcome, BaseException):
            raise outcome
        return outcome


def test_budget_blocks_before_any_call(monkeypatch, ledger: UsageLedger) -> None:
    # 100 micro = $0.0001；deepseek 输入 $0.28/M，预留必然超过上限
    llm = LLM(budget=LLMBudget(daily_usd=1e-4))
    script = _Script(_resp(), _resp())
    gateway = _gateway(monkeypatch, script, llm, ledger)
    with pytest.raises(gw.GatewayError) as caught:
        gateway.complete([{"role": "user", "content": "hi"}])
    assert caught.value.code == errors.BUDGET
    assert script.calls == []
    assert ledger.day_total(__import__("datetime").date.today().isoformat())["used_micro"] == 0


def test_successful_call_books_actual_cost(monkeypatch, ledger: UsageLedger) -> None:
    llm = LLM(budget=LLMBudget(daily_usd=1.0))
    script = _Script(_resp(prompt=1000, completion=200))
    gateway = _gateway(monkeypatch, script, llm, ledger)
    reply = gateway.complete([{"role": "user", "content": "hi"}], purpose="chat")
    assert reply.cost_source == "reported" and reply.cost_micro and reply.cost_micro > 0
    record = ledger.recent(1)[0]
    assert record.purpose == "chat" and record.cost_micro == reply.cost_micro
    assert record.prompt_tokens == 1000 and record.completion_tokens == 200


def test_rate_limit_releases_the_reservation(monkeypatch, ledger: UsageLedger) -> None:
    llm = LLM(budget=LLMBudget(daily_usd=1.0))
    script = _Script(_exc("RateLimitError"))
    gateway = _gateway(monkeypatch, script, llm, ledger)
    with pytest.raises(gw.GatewayError):
        gateway.complete([{"role": "user", "content": "hi"}])
    today = ledger.day_total(__import__("datetime").date.today().isoformat())
    assert today["used_micro"] == 0  # 限流确定没计费：预留全退回
    assert ledger.recent(1)[0].cost_source == "not_billed"


def test_timeout_keeps_the_reservation_conservatively(monkeypatch, ledger: UsageLedger) -> None:
    """超时可能已经被供应商处理并计费：保守按预留记账，不当成 0。"""
    llm = LLM(budget=LLMBudget(daily_usd=1.0))
    # 超时会按有界重试打到上限（默认 3 次），每次都可能已被供应商计费
    script = _Script(*[_exc("Timeout", "read timeout")] * 3)
    gateway = _gateway(monkeypatch, script, llm, ledger)
    with pytest.raises(gw.GatewayError):
        gateway.complete([{"role": "user", "content": "hi"}])
    record = ledger.recent(1)[0]
    assert record.cost_source == "reserved" and (record.cost_micro or 0) > 0
    today = ledger.day_total(__import__("datetime").date.today().isoformat())
    assert today["spent_micro"] == record.cost_micro


def test_missing_usage_keeps_reservation(monkeypatch, ledger: UsageLedger) -> None:
    llm = LLM(budget=LLMBudget(daily_usd=1.0))
    script = _Script(_resp(prompt=None))
    gateway = _gateway(monkeypatch, script, llm, ledger)
    reply = gateway.complete([{"role": "user", "content": "hi"}])
    assert reply.usage.reported is False
    assert reply.cost_source == "reserved" and (reply.cost_micro or 0) > 0


def test_unpriced_model_refused_when_a_limit_exists(monkeypatch, ledger: UsageLedger) -> None:
    llm = LLM(budget=LLMBudget(daily_usd=1.0))
    script = _Script(_resp())
    gateway = _gateway(monkeypatch, script, llm, ledger)
    with pytest.raises(gw.GatewayError) as caught:
        gateway.complete([{"role": "user", "content": "hi"}], model="foo/bar")
    assert caught.value.code == errors.PRICE_UNKNOWN
    assert script.calls == []


def test_unpriced_model_allowed_without_limit_but_cost_is_null(monkeypatch, ledger: UsageLedger) -> None:
    """没有上限时放行，但账上必须是"未知"，不能替供应商宣布免费。"""
    llm = LLM()
    script = _Script(_resp())
    gateway = _gateway(monkeypatch, script, llm, ledger)
    reply = gateway.complete([{"role": "user", "content": "hi"}], model="foo/bar")
    assert reply.cost_micro is None and reply.cost_source == "unknown"
    assert ledger.recent(1)[0].cost_micro is None


def test_local_model_is_billed_as_free_not_unknown(monkeypatch, ledger: UsageLedger) -> None:
    llm = LLM(budget=LLMBudget(daily_usd=1.0))
    script = _Script(_resp())
    gateway = _gateway(monkeypatch, script, llm, ledger)
    reply = gateway.complete([{"role": "user", "content": "hi"}], model="ollama/qwen2.5:7b")
    assert reply.cost_micro == 0 and reply.cost_source == "reported"


def test_run_budget_is_shared_with_children() -> None:
    parent = RunBudget(limit_micro=100)
    child = parent.child()
    assert child is parent  # 共享同一个实例，累计限制才成立
    assert parent.try_reserve(80) is True
    assert child.try_reserve(80) is False


def test_run_budget_blocks_the_call(monkeypatch, ledger: UsageLedger) -> None:
    llm = LLM()
    script = _Script(_resp())
    gateway = _gateway(monkeypatch, script, llm, ledger)
    budget = RunBudgetFromLlm(limit_micro=1)  # 连一次都预留不下
    with pytest.raises(gw.GatewayError) as caught:
        gateway.complete([{"role": "user", "content": "hi"}], run_budget=budget)
    assert caught.value.code == errors.BUDGET
    assert script.calls == []


def test_ledger_failure_refuses_to_spend(state: StateStore, tmp_path) -> None:
    """账本坏了就不敢花钱：宁可这次不调用，也不记账不清地调用。"""
    llm = LLM(budget=LLMBudget(daily_usd=1.0))

    class _Broken:
        def reserve(self, **_kw):
            raise RuntimeError("database is locked")

        def settle(self, **_kw):
            raise RuntimeError("database is locked")

        def record(self, **_kw):
            raise RuntimeError("database is locked")

    gateway = gw.Gateway(Models(chat="deepseek/deepseek-chat"), llm=llm, ledger=_Broken(), sleep=lambda _s: None)
    with pytest.raises(gw.GatewayError) as caught:
        gateway.complete([{"role": "user", "content": "hi"}])
    assert caught.value.code == errors.BUDGET
    assert "账本不可用" in str(caught.value)


# ---------------------------------------------------------------- Agent 语义


def test_agent_turns_budget_block_into_an_explanation(monkeypatch, tmp_path) -> None:
    """预算拦截是一种**结果**，不是异常：回合要如实说明，且没有发出请求。"""
    script = _Script(_resp())
    monkeypatch.setattr(gw, "_completion", script)
    config = AppConfig(
        limits=Limits(max_agent_steps=2),
        llm=LLM(budget=LLMBudget(per_run_usd=1e-6)),
    )
    state = StateStore(tmp_path / "state.db")
    store = MemoryStore(tmp_path / "m.db")
    ledger = UsageLedger(state)
    gateway = gw.Gateway(Models(chat="deepseek/deepseek-chat"), llm=config.llm, ledger=ledger)
    sid = store.new_session()
    agent = Agent(config, gateway, store, ToolRegistry(), sid)

    turn = agent.run_turn("你好")

    assert turn.status == "failed" and turn.error_code == errors.BUDGET
    assert "费用上限" in turn.answer
    assert script.calls == []
    assert store.recent_messages(sid, 10)[-1][1] == turn.answer  # 回答如实落库
    state.close()
