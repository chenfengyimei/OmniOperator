"""上下文压缩单测：不变量断言 / 原子块切分 / 压缩触发与降级 / Agent 集成。"""
from types import SimpleNamespace

import pytest

from omni.config import AppConfig
from omni.core.agent import Agent
from omni.core.compress import (
    SUMMARY_HEADER,
    assert_invariants,
    atomic_blocks,
    compress_if_needed,
    context_chars,
)
from omni.memory.store import MemoryStore
from omni.tools.builtin import register_core_tools
from omni.tools.registry import ToolRegistry


def _pair(i: int, size: int = 300) -> list[dict]:
    """一个工具调用原子块：assistant(tool_calls) + tool 结果。"""
    return [
        {
            "role": "assistant",
            "content": "",
            "tool_calls": [
                {
                    "id": f"c{i}",
                    "type": "function",
                    "function": {"name": "recall", "arguments": "{}"},
                }
            ],
        },
        {"role": "tool", "tool_call_id": f"c{i}", "content": "x" * size},
    ]


def _pairs_intact(messages: list[dict]) -> bool:
    """每个 tool 结果都能找到带同 id tool_calls 的 assistant（对永不拆开）。"""
    called = {
        tc["id"] for m in messages for tc in (m.get("tool_calls") or [])
    }
    answered = {m["tool_call_id"] for m in messages if m.get("role") == "tool"}
    return called == answered


# ---------- 不变量 ----------

def test_invariants_accept_valid_sequence():
    msgs = [
        {"role": "system", "content": "s"},
        {"role": "user", "content": "u1"},
        {"role": "assistant", "content": "a1"},
        {"role": "user", "content": "u2"},
        *_pair(1),
        {"role": "assistant", "content": "a2"},
    ]
    assert_invariants(msgs)  # 不抛即通过


def test_invariants_reject_bad_sequences():
    with pytest.raises(AssertionError):
        assert_invariants([{"role": "user", "content": "没有 system"}])
    with pytest.raises(AssertionError):
        assert_invariants(
            [
                {"role": "system", "content": "s"},
                {"role": "system", "content": "s2"},
            ]
        )

    with pytest.raises(AssertionError):
        # tool 直接跟在 user 后（前面没有带 tool_calls 的 assistant）
        assert_invariants(
            [
                {"role": "system", "content": "s"},
                {"role": "user", "content": "u"},
                {"role": "tool", "tool_call_id": "c1", "content": "t"},
            ]
        )


def _multi_call_block():
    first, answer1 = _pair(1)
    second, answer2 = _pair(2)
    first["tool_calls"] += second["tool_calls"]
    return first, answer1, answer2


def test_invariants_accept_multiple_tool_results_and_compression():
    block = list(_multi_call_block())
    messages = _long_history(12) + block
    assert_invariants(messages)
    assert atomic_blocks(block) == [block]
    compressed = compress_if_needed(messages, gateway=SummaryGateway(), model="fake", trigger_chars=1000, keep_recent=1)
    assert compressed[-3:] == block
    assert_invariants(compressed)


@pytest.mark.parametrize("mutation", ["missing", "duplicate", "unknown", "interrupted", "duplicate-call-id", "empty-call-id"])
def test_invariants_require_exactly_one_result_per_call(mutation):
    call, first, second = _multi_call_block()
    block = [call, first, second]
    if mutation == "missing":
        block.pop()
    elif mutation == "duplicate":
        block.append(first)
    elif mutation == "unknown":
        second["tool_call_id"] = "not-called"
    elif mutation == "interrupted":
        block.insert(2, {"role": "assistant", "content": "太早结束"})
    elif mutation == "duplicate-call-id":
        call["tool_calls"][1]["id"] = "c1"
    else:
        call["tool_calls"][1]["id"] = ""
    with pytest.raises(AssertionError):
        assert_invariants([{"role": "system", "content": "s"}, *block])


def test_atomic_blocks_group_tool_pairs():
    body = [
        {"role": "user", "content": "u1"},
        *_pair(1),
        *_pair(2),
        {"role": "assistant", "content": "a"},
        {"role": "user", "content": "u2"},
    ]
    blocks = atomic_blocks(body)
    assert [len(b) for b in blocks] == [1, 2, 2, 1, 1]


# ---------- 压缩 ----------

def _long_history(n: int) -> list[dict]:
    msgs: list[dict] = [{"role": "system", "content": "system prompt"}]
    for i in range(n):
        msgs.append({"role": "user", "content": f"用户消息{i}，" + "长" * 400})
        msgs.append({"role": "assistant", "content": f"回答{i}，" + "应" * 400})
    return msgs


class SummaryGateway:
    """压缩调用（tools=None）返回固定摘要；其余返回空转回答。"""

    def __init__(self, fail: bool = False):
        self.calls: list[list[dict]] = []
        self.fail = fail

    def resolve(self, route: str) -> str:
        return "fake/model"

    def complete(self, messages, *, model, tools=None, **kwargs):
        self.calls.append([dict(m) for m in messages])
        if self.fail:
            raise RuntimeError("模型超时")
        return SimpleNamespace(text="要点：聊了很多长内容。", tool_calls=[])


def test_compress_skipped_under_threshold():
    gw = SummaryGateway()
    msgs = _long_history(3)
    out = compress_if_needed(
        msgs, gateway=gw, model="m", trigger_chars=10**9, keep_recent=4
    )
    assert out is msgs and gw.calls == []


def test_compress_replaces_old_blocks_with_summary():
    gw = SummaryGateway()
    msgs = _long_history(12)
    msgs += _pair(99)
    out = compress_if_needed(
        msgs, gateway=gw, model="m", trigger_chars=3000, keep_recent=6
    )
    assert out is not msgs
    assert out[0]["role"] == "system"
    assert SUMMARY_HEADER in out[1]["content"] and "要点" in out[1]["content"]
    # 摘要走的是廉价模型且无工具
    assert gw.calls and gw.calls[0][-1]["role"] == "user"
    # 不变量与工具调用对完整
    assert_invariants(out)
    assert _pairs_intact(out)
    # 保留窗内的原文未被动过（最后一条 assistant 原样在尾部）
    assert out[-1] == msgs[-1]


def test_compress_merges_summary_into_leading_user():
    """recent 以 user 开头时摘要并入首条，避免连续 user 破坏交替。"""
    gw = SummaryGateway()
    msgs = [{"role": "system", "content": "s"}]
    msgs.append({"role": "user", "content": "开场白"})
    msgs += _long_history(12)
    out = compress_if_needed(
        msgs, gateway=gw, model="m", trigger_chars=3000, keep_recent=6
    )
    assert_invariants(out)
    # 不存在连续 user（开场白被并入或被压缩；摘要并入首条 user）
    for a, b in zip(out, out[1:]):
        assert not (a["role"] == "user" and b["role"] == "user")
    assert SUMMARY_HEADER in out[1]["content"]


def test_compress_degrades_on_gateway_failure():
    gw = SummaryGateway(fail=True)
    msgs = _long_history(12)
    out = compress_if_needed(
        msgs, gateway=gw, model="m", trigger_chars=3000, keep_recent=6
    )
    assert out is msgs  # 降级：原样返回


def test_context_chars_counts_tool_payload():
    msgs = [{"role": "system", "content": "ab"}, *_pair(1, size=500)]
    assert context_chars(msgs) > 500


def test_threshold_uses_d10_token_estimate():
    """D11 收口：阈值按 estimate_tokens 的 token 口径折算（trigger_chars/2）。

    消息包装开销与工具调用序列化计入体量——短消息堆积在字符口径下"很小"，
    在 token 口径下会超预算触发压缩（旧字符口径不会）。
    """
    gw = SummaryGateway()
    many = [{"role": "system", "content": "s"}]
    for i in range(260):
        many.append({"role": "user" if i % 2 == 0 else "assistant", "content": f"m{i}"})
    assert len("".join(str(m.get("content")) for m in many)) < 4000  # 字符口径在阈值之下
    out = compress_if_needed(many, gateway=gw, model="m", trigger_chars=4000, keep_recent=6)
    assert out is not many and gw.calls, "消息开销应计入 token 预算（统一口径的行为变化）"

    # 既有量级下触发行为保持稳定：长内容远超阈值 → 压缩；远低于阈值 → 不压缩
    gw2 = SummaryGateway()
    long_msgs = _long_history(20)  # ~16200 字 / ~8600 token
    assert compress_if_needed(long_msgs, gateway=gw2, model="m", trigger_chars=16000, keep_recent=6) is not long_msgs
    gw3 = SummaryGateway()
    assert compress_if_needed(long_msgs, gateway=gw3, model="m", trigger_chars=32000, keep_recent=6) is long_msgs
    assert gw3.calls == []


def test_agent_compresses_long_history(tmp_path):
    """端到端：store 里的长历史在进入模型前被压缩（落库内容不受影响）。"""
    store = MemoryStore(tmp_path / "m.db")
    sid = store.new_session()
    for i in range(30):
        store.append_message(
            sid, "user", ("长内容" * 500) + str(i), auto_title=False
        )
        store.append_message(
            sid, "assistant", ("回复" * 500) + str(i), auto_title=False
        )

    cfg = AppConfig()
    cfg.compression.trigger_chars = 5000
    cfg.compression.keep_recent = 6
    cfg.compression.min_messages = 10

    class GW:
        def __init__(self):
            self.chat_messages: list[list[dict]] = []

        def resolve(self, route: str) -> str:
            return "fake/model"

        def complete(self, messages, *, model, tools=None, **kwargs):
            if tools is None:  # 压缩摘要调用
                return SimpleNamespace(text="摘要：聊了很多长内容。", tool_calls=[])
            self.chat_messages.append([dict(m) for m in messages])
            return SimpleNamespace(text="收到。", tool_calls=[])

    gw = GW()
    registry = ToolRegistry()
    register_core_tools(registry, store)
    agent = Agent(cfg, gw, store, registry, sid)
    assert agent.chat("还在吗") == "收到。"

    # 发给模型的对话里带摘要头
    assert any(
        SUMMARY_HEADER in m["content"] for m in gw.chat_messages[0][1:]
    )
    # 落库不受压缩影响：60 条旧消息 + 本轮 user/assistant
    assert len(store.list_messages(sid, 1000)) == 62
    store.close()
