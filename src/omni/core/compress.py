"""上下文压缩 + 消息不变量（Hermes compression_facade 的最小版，NOTES §1.2）。

为什么：常驻 agent 的会话跨天复用（QQ 绑定会话尤其明显），而
max_history_messages 的硬截断会无声丢掉旧上下文。压缩 = 上下文超阈值时，
把较旧的消息交给廉价模型出一篇摘要替换原文，最近若干条原样保留。

纪律（抄 Hermes，一条不让）：
- 原子块：assistant(tool_calls) 与其后连续的 tool 消息永不拆开，切分只落在块边界；
- 系统提示词不动：摘要以 user 消息进入上下文（且并入首条 user 以维持严格交替）；
- 只压内存视图不落库：store 本就只存 user/assistant 文本，压缩视图随生随灭；
- 失败降级：摘要调用失败就原样返回——压缩是优化，不是正确性的依赖。
"""
from __future__ import annotations

import json
from typing import Any

from ..llm.gateway import Gateway

SUMMARY_HEADER = "（以下是此前对话的压缩摘要，细节可用 search_history 工具找回）"

_COMPRESS_SYSTEM = (
    "你是 OmniOperator 的上下文压缩器。把旧对话压缩成要点摘要，供 agent 延续"
    "工作。必须保留：用户的目标与决定、重要事实（人名/数字/链接）、已完成的"
    "步骤、未完成的待办与下一步。用紧凑条目列出，300 字以内，不要评论。"
)


def assert_invariants(messages: list[dict[str, Any]]) -> None:
    """消息不变量（Hermes 硬约束）。违反立即 AssertionError——坏请求宁可在
    本地炸掉暴露 bug，也不发给对角色交替敏感的模型。"""
    if not messages:
        return
    assert messages[0].get("role") == "system", "消息序列必须以 system 开头"
    assert (
        sum(1 for m in messages if m.get("role") == "system") == 1
    ), "system 只能有一条"
    for i, m in enumerate(messages[1:], start=1):
        if m.get("role") != "tool":
            continue
        prev = messages[i - 1]
        assert (
            prev.get("role") == "assistant" and prev.get("tool_calls")
        ), f"第 {i} 条 tool 消息必须紧跟在带 tool_calls 的 assistant 之后"


def atomic_blocks(messages: list[dict[str, Any]]) -> list[list[dict[str, Any]]]:
    """按原子块切分（传进来前先去掉 system）：

    assistant(tool_calls) 与其后连续 tool 消息是一块；user / 纯文本
    assistant 各自一块。压缩的保留/摘切只允许按块进行。
    """
    blocks: list[list[dict[str, Any]]] = []
    for m in messages:
        if (
            m.get("role") == "tool"
            and blocks
            and blocks[-1][0].get("tool_calls")
        ):
            blocks[-1].append(m)
            continue
        blocks.append([m])
    return blocks


def context_chars(messages: list[dict[str, Any]]) -> int:
    """粗算上下文体量：content 字符 + tool_calls 的 JSON（近似 token）。"""
    total = 0
    for m in messages:
        total += len(m.get("content") or "")
        for tc in m.get("tool_calls") or []:
            total += len(json.dumps(tc, ensure_ascii=False))
    return total


def compress_if_needed(
    messages: list[dict[str, Any]],
    *,
    gateway: Gateway,
    model: str,
    trigger_chars: int,
    keep_recent: int = 12,
    min_messages: int = 16,
) -> list[dict[str, Any]]:
    """超阈值时把旧块压缩成摘要；绝大多数调用原样返回（先查体量再动手）。"""
    if len(messages) < min_messages or context_chars(messages) <= trigger_chars:
        return messages
    system, body = messages[0], messages[1:]
    blocks = atomic_blocks(body)

    # 从尾部按块凑 keep_recent 条（凑整块，绝不切开工具调用对）
    recent: list[list[dict[str, Any]]] = []
    taken = 0
    while blocks and taken < keep_recent:
        block = blocks.pop()
        recent.insert(0, block)
        taken += len(block)
    if not blocks:  # 全在保留窗内，无需压
        return messages

    old = [m for block in blocks for m in block]
    transcript = "\n".join(
        f"[{m['role']}] {str(m.get('content') or '')[:400]}" for m in old
    )
    try:
        reply = gateway.complete(
            [
                {"role": "system", "content": _COMPRESS_SYSTEM},
                {"role": "user", "content": f"待压缩的旧对话：\n{transcript}"},
            ],
            model=model,
            tools=None,
        )
        summary = reply.text.strip()
    except Exception:  # noqa: BLE001 —— 降级：宁可不压也不能让会话中断
        return messages
    if not summary:
        return messages

    summary_block = {
        "role": "user",
        "content": f"{SUMMARY_HEADER}\n{summary}",
    }
    flat_recent = [m for block in recent for m in block]
    # 维持严格角色交替：recent 以 user 开头时把摘要并入首条，避免连续 user
    if flat_recent and flat_recent[0].get("role") == "user":
        merged = dict(flat_recent[0])
        merged["content"] = (
            f"{summary_block['content']}\n\n{merged.get('content') or ''}"
        )
        return [system, merged, *flat_recent[1:]]
    return [system, summary_block, *flat_recent]
