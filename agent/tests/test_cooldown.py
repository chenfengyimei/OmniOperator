"""通道层冷却位（P5-e）单测。

这个特性最容易写错的地方不是判定本身，而是**它该压制谁、不该压制谁**：
压制「主动搭话」很容易，压过头把「用户正在跟你说话」也压掉就废了。
所以这里分三层锁：

1. 判定层（`safety/cooldown.py`）：软唤醒才被压；硬唤醒和**未知理由**都不压
   （认错了理由就宁可多回一句，也不要把用户明确叫到的请求压掉）；
2. 状态层（`memory/qq.py`）：冷却锚点取自真实的入站记录，**零 schema 变更**；
3. 接线层（`core/qq_policy.py` + cron + tools）：两条主动路径共用同一个闸门，
   而**入站回复路径完全不受压制**——潜水期照常回话。

每一条都断言"该发生什么"，而不只是"没报错"。
"""
from __future__ import annotations

import asyncio
import time
from types import SimpleNamespace

import pytest

from omni.config import QQ
from omni.core.cron import make_runtime_deliver
from omni.core.qq_policy import (
    COOLDOWN_CODE,
    dm_scene,
    proactive_gate,
    runtime_last_inbound_at,
)
from omni.memory.cron import Job
from omni.memory.qq import QQMessageStore
from omni.memory.state import OutboxStore, StateStore
from omni.safety.cooldown import (
    HARD_WAKE_REASONS,
    SOFT_WAKE_REASONS,
    cooldown_decision,
    is_soft_wake,
)
from omni.safety.proactive import ProactiveDecision
from omni.tools.qq.client import OneBotClient
from omni.tools.qq.tools import register_qq_tools
from omni.tools.registry import ToolRegistry
from test_qq_scope import store  # noqa: F401  （复用真实 QQ 入站 harness 的 fixture）

NOW = 1_800_000_000.0  # 固定基准时间，避免测试依赖真实时钟


# ================================================================ 判定层


def test_soft_wake_reasons_cover_proactive_paths_only() -> None:
    """软唤醒 = 「我不请自来发消息」。cron 也算：被压制时是延迟补投，不是丢弃。"""
    assert SOFT_WAKE_REASONS == {"cron", "proactive", "probability"}
    assert HARD_WAKE_REASONS == {"direct", "reply", "mention", "private"}
    # 两集合不相交，分类不会同时命中
    assert not (SOFT_WAKE_REASONS & HARD_WAKE_REASONS)


def test_unknown_reason_is_treated_as_hard_wake() -> None:
    """认错了理由就宁可多回一句——把用户明确叫到的请求压掉是更糟的错。"""
    assert is_soft_wake("probability") is True
    assert is_soft_wake("mention") is False
    assert is_soft_wake("某个没定义过的理由") is False


def test_cooldown_suppresses_soft_wake_right_after_inbound() -> None:
    decision = cooldown_decision(
        last_inbound_at=NOW - 60, now=NOW, window_s=1200, reason="proactive"
    )
    assert decision.state == "suppress"
    assert decision.remaining_s == pytest.approx(1140)
    assert "冷却中" in decision.reason


def test_cooldown_does_not_suppress_hard_wake() -> None:
    """被 @ / 叫名字 / 提问 / 私聊一律照常醒——「结束不是把人关掉」。"""
    for reason in sorted(HARD_WAKE_REASONS):
        decision = cooldown_decision(
            last_inbound_at=NOW - 60, now=NOW, window_s=1200, reason=reason
        )
        assert decision.state == "allow", reason


def test_cooldown_allows_when_window_disabled_or_no_inbound() -> None:
    """窗口关掉、或这个场景从没收到过消息 → 都不压制。"""
    assert cooldown_decision(
        last_inbound_at=NOW, now=NOW, window_s=0, reason="proactive"
    ).state == "allow"
    assert cooldown_decision(
        last_inbound_at=None, now=NOW, window_s=1200, reason="proactive"
    ).state == "allow"


def test_cooldown_expires_after_window() -> None:
    decision = cooldown_decision(
        last_inbound_at=NOW - 1201, now=NOW, window_s=1200, reason="cron"
    )
    assert decision.state == "allow"
    assert "冷却期已过" in decision.reason


def test_cooldown_treats_clock_rollback_as_just_arrived() -> None:
    """时钟回拨不能变成放行主动搭话的漏洞。"""
    decision = cooldown_decision(
        last_inbound_at=NOW + 500, now=NOW, window_s=1200, reason="proactive"
    )
    assert decision.state == "suppress"


# ================================================================ 状态层


def test_last_inbound_at_reads_real_inbound_record(tmp_path) -> None:
    """锚点取自 qq_messages.created_at——已有列，不新建表。"""
    state = StateStore(tmp_path / "state.db")
    inbox = QQMessageStore(state, clock=lambda: NOW)
    assert inbox.last_inbound_at("dm:10001") is None
    assert inbox.claim(10000, "dm:10001", "m1") is True
    assert inbox.last_inbound_at("dm:10001") == pytest.approx(NOW)
    # 别的场景不受影响
    assert inbox.last_inbound_at("dm:20002") is None
    state.close()


def test_last_inbound_at_ignores_recalled_messages(tmp_path) -> None:
    """撤回的消息算「这段对话没发生过」，不该重置冷却窗口。"""
    state = StateStore(tmp_path / "state.db")
    inbox = QQMessageStore(state, clock=lambda: NOW)
    inbox.claim(10000, "dm:10001", "m1")
    assert inbox.recall(10000, "dm:10001", "m1") is True
    assert inbox.last_inbound_at("dm:10001") is None
    state.close()


def test_last_inbound_at_can_filter_by_bot(tmp_path) -> None:
    state = StateStore(tmp_path / "state.db")
    inbox = QQMessageStore(state, clock=lambda: NOW)
    inbox.claim(10000, "dm:10001", "m1")
    assert inbox.last_inbound_at("dm:10001", bot=10000) == pytest.approx(NOW)
    assert inbox.last_inbound_at("dm:10001", bot=20000) is None
    state.close()


# ================================================================ 组合闸门


def _policy(**overrides) -> QQ:
    base = dict(
        allowed_users=[10001],
        proactive_enabled=True,
        owner_qq=10001,
        quiet_start="00:00",
        quiet_end="00:00",
        proactive_daily_limit=5,
        proactive_cooldown_minutes=20,
    )
    base.update(overrides)
    return QQ(**base)


def test_dm_scene_matches_channel_scene_key() -> None:
    """scene 键必须和 channels/qq.py 的命名一致，否则永远查不到入站时间。"""
    assert dm_scene(10001) == "dm:10001"


def test_gate_defers_with_cooldown_code_when_just_chatted() -> None:
    decision = proactive_gate(
        _policy(),
        10001,
        reason="proactive",
        last_inbound_at=NOW - 60,
        now_ts=NOW,
    )
    assert decision.state == "defer"
    assert decision.code == COOLDOWN_CODE
    assert decision.code != ""  # 与安静时段区分得开
    assert "冷却中" in decision.reason


def test_gate_allows_after_cooldown_expires() -> None:
    decision = proactive_gate(
        _policy(),
        10001,
        reason="proactive",
        last_inbound_at=NOW - 1201,
        now_ts=NOW,
    )
    assert decision.state == "allow"
    assert decision.code == ""


def test_gate_quiet_hours_and_cooldown_are_distinguishable() -> None:
    """两种 defer 必须能分开：安静时段 code 为空，冷却有专用码。"""
    import datetime as dt

    quiet = proactive_gate(
        _policy(quiet_start="22:00", quiet_end="08:00"),
        10001,
        now=dt.datetime(2026, 11, 1, 23, 0, tzinfo=dt.timezone(dt.timedelta(hours=8))),
    )
    assert quiet.state == "defer"
    assert quiet.code == ""
    assert "安静时段" in quiet.reason


def test_gate_denies_non_owner_before_cooldown() -> None:
    """策略优先级高于冷却：不是所有者就没有「该不该插话」的讨论。"""
    decision = proactive_gate(
        _policy(allowed_users=[10001, 10002]),
        10002,
        last_inbound_at=NOW,
        now_ts=NOW,
    )
    assert decision.state == "deny"


def test_runtime_last_inbound_at_tolerates_missing_store(tmp_path) -> None:
    """取不到锚点就退回旧行为（不压制），而不是把主动发言整个堵死。"""
    assert runtime_last_inbound_at(SimpleNamespace(), 10001) is None
    state = StateStore(tmp_path / "state.db")
    inbox = QQMessageStore(state, clock=lambda: NOW)
    inbox.claim(10000, "dm:10001", "m1")
    got = runtime_last_inbound_at(SimpleNamespace(qq_messages=inbox), 10001)
    assert got == pytest.approx(NOW)
    state.close()


# ================================================================ 接线层：cron 主动投递


class _FakeQQ:
    connected = True

    def __init__(self) -> None:
        self.sent: list[tuple[int, str]] = []

    async def send_private(self, user_id: int, text: str) -> None:
        self.sent.append((user_id, text))


def _cron_runtime(tmp_path, *, inbound_age_s: float | None):
    state = StateStore(tmp_path / "state.db")
    clock = (lambda: time.time() - inbound_age_s) if inbound_age_s is not None else time.time
    inbox = QQMessageStore(state, clock=clock)
    if inbound_age_s is not None:
        inbox.claim(10000, "dm:10001", "m1")
    qq = _FakeQQ()
    runtime = SimpleNamespace(
        qq=qq,
        qq_policy=lambda: _policy(),
        state=state,
        outbox=OutboxStore(state),
        qq_messages=inbox,
    )
    return runtime, qq, state


def _job() -> Job:
    return Job(name="test-job", prompt="报个到", every_minutes=30, deliver_to_qq=10001)


def test_cron_delivery_is_deferred_not_dropped_while_cooldown_active(tmp_path) -> None:
    """冷却期内定时投递 → 进队列等补投，**不丢内容**。"""
    runtime, qq, state = _cron_runtime(tmp_path, inbound_age_s=None)
    inbox = runtime.qq_messages
    inbox.claim(10000, "dm:10001", "m1")

    detail = asyncio.run(make_runtime_deliver(runtime)(_job(), "该喝水了"))
    assert qq.sent == []
    assert "待投递队列" in detail
    assert "冷却" in detail
    # 消息还在队列里，等冷却结束由补投循环发出
    assert runtime.outbox.pending()
    state.close()


def test_cron_delivery_goes_out_when_cooldown_expired(tmp_path) -> None:
    """冷却期过了 → 正常投递。"""
    runtime, qq, state = _cron_runtime(tmp_path, inbound_age_s=3600)
    detail = asyncio.run(make_runtime_deliver(runtime)(_job(), "该喝水了"))
    assert qq.sent == [(10001, "该喝水了")]
    assert "已投递" in detail
    state.close()


def test_cron_delivery_unchanged_without_runtime_message_store(tmp_path) -> None:
    """runtime 上没有 qq_messages（嵌入式/旧 fake）→ 退回旧行为，照常投递。"""
    state = StateStore(tmp_path / "state.db")
    qq = _FakeQQ()
    runtime = SimpleNamespace(
        qq=qq,
        qq_policy=lambda: _policy(),
        state=state,
        outbox=OutboxStore(state),
    )
    detail = asyncio.run(make_runtime_deliver(runtime)(_job(), "hi"))
    assert qq.sent == [(10001, "hi")]
    assert "已投递" in detail
    state.close()


# ================================================================ 接线层：模型主动私聊


def _tool_result(gate):
    registry = ToolRegistry()
    register_qq_tools(
        registry, OneBotClient("ws://x"), allowed_users=[10001], active_gate=gate
    )
    return registry.execute("qq_send_message", {"user_id": 10001, "text": "hi"})


def test_tool_reports_cooldown_with_its_own_error_code() -> None:
    """工具路径没有队列，冷却期只能直接拒——但错误码要和安静时段分开。"""
    gate = lambda target: ProactiveDecision(
        "defer", "刚在 1 分钟前聊过，冷却中", code=COOLDOWN_CODE
    )
    result = _tool_result(gate)
    assert result.status == "error"
    assert result.error_code == "proactive_cooldown"
    assert "冷却" in result.text


def test_tool_keeps_quiet_hours_error_code() -> None:
    """既有契约不能被破坏：安静时段仍然是 proactive_quiet_hours。"""
    gate = lambda target: ProactiveDecision("defer", "安静时段，等待下一次补投")
    result = _tool_result(gate)
    assert result.error_code == "proactive_quiet_hours"
    assert "安静时段" in result.text


# ================================================================ 入站路径不受压制


def test_inbound_reply_is_not_suppressed_by_cooldown(store) -> None:
    """潜水期照常回话：用户发来的消息必须照常回，不能被自己的冷却位堵住。

    复用 test_qq_scope 的真实 harness（真 SQLite + 真 QQ 通道入站路径），
    这样"入站不受压制"是跑出来的，不是断言出来的。
    """
    from omni.channels.qq import _handle_message
    from test_qq_scope import _ChanRuntime, _RecordingClient, _private

    runtime = _ChanRuntime(store, users=[10001], groups=[])
    client = _RecordingClient()
    asyncio.run(_handle_message(runtime, client, _private(10001), {}))

    # 回了话：入站路径根本不经过主动闸门
    assert runtime.agent.calls, "入站消息必须照常处理"
    assert client.sent and client.sent[0][0] == "private"

    # 而同一次入站把「刚聊过」记下来了 → 之后的主动搭话会被压制
    anchor = runtime.qq_messages.last_inbound_at("dm:10001")
    assert anchor is not None
    decision = proactive_gate(_policy(), 10001, last_inbound_at=anchor)
    assert decision.state == "defer"
    assert decision.code == COOLDOWN_CODE
