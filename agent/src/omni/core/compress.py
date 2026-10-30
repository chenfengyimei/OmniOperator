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
from typing import Any, Callable

from ..llm.gateway import Gateway
from ..llm.usage import estimate_tokens
from .context import CONTEXT_HEADER

SUMMARY_HEADER = "（以下是此前对话的压缩摘要，细节可用 search_history 工具找回）"


def _over_threshold(messages: list[dict[str, Any]], trigger_chars: int) -> bool:
    """压缩阈值与 D10 费用预算同一把尺（D11 收口）。

    `trigger_chars` 仍以字符配置（兼容既有配置面），内部按 `estimate_tokens`
    的 2 字符/token 折算成 token 预算再比较——工具调用序列化、多模态文本块
    与每条消息的包装开销都计入体量，与网关预留额度口径一致。
    """
    return estimate_tokens(messages) > max(1, trigger_chars // 2)

_COMPRESS_SYSTEM = (
    "你是 OmniOperator 的上下文压缩器。把旧对话压缩成要点摘要，供 agent 延续"
    "工作。必须保留：用户的目标与约束（尤其是'只能/不要/必须'这类硬要求）、"
    "重要事实（人名/数字/链接/文件路径，作为证据引用）、已完成的步骤、未完成的"
    "待办与下一步、以及用户明确纠正过的结论。用紧凑条目列出，300 字以内，不要评论。"
)


def _summary_message(summary_text: str, first_user: str | None) -> dict[str, Any]:
    """摘要以 user 消息进入上下文；紧跟用户消息时并进去（维持严格角色交替）。"""
    body = f"{SUMMARY_HEADER}\n{summary_text}"
    if first_user:
        return {"role": "user", "content": f"{body}\n\n{first_user}"}
    return {"role": "user", "content": body}


def assemble_context(
    rows: list[tuple[int, str, str]],
    summary: tuple[int, int, int, str, int] | None,
    *,
    gateway: Gateway,
    model: str,
    trigger_chars: int,
    keep_recent: int = 12,
    min_messages: int = 16,
    run_id: str | None = None,
    session_id: int | None = None,
    run_budget: Any = None,
    cancel: Any = None,
    save: Any = None,
    render: Callable[[list[tuple[int, str, str]]], list[dict[str, Any]]] | None = None,
) -> tuple[list[dict[str, Any]], dict[str, Any]]:
    """组装"摘要 + 未被摘要覆盖的消息"，需要时把更老的一段折进摘要（D11）。

    与 `compress_if_needed` 的区别：这里**持久化**摘要及其覆盖的消息区间。
    好处是上下文不必每轮重新压缩、重启后仍然在；代价是要按 id 记账，
    所以调用方传的是带 id 的行（`recent_messages_ex`）。

    `save(content=..., from_msg_id=..., to_msg_id=..., supersedes=...)` 由调用方注入，
    内核层不认识 SQLite。摘要失败时**原样返回**，不写半截摘要（保守降级）。
    """
    info: dict[str, Any] = {"summarized": False, "summary_id": None, "version": None}
    if summary is not None:
        info["summary_id"], info["version"] = summary[0], summary[4]
    uncovered = [
        row for row in rows if summary is None or row[0] > summary[2]
    ]
    if len(uncovered) < min_messages or not _over_threshold(
        _rows_to_messages(uncovered, None, render), trigger_chars
    ):
        return _rows_to_messages(uncovered, summary, render), info

    cut = max(0, len(uncovered) - keep_recent) if keep_recent > 0 else len(uncovered)
    if render is not None:
        # 当前回合的原始请求和快照不可被摘要替换，keep_recent=0 也一样。
        current = next((i for i in range(len(uncovered)-1,-1,-1) if uncovered[i][1]=='user'), None)
        if current is not None:cut=min(cut,current)
    old, recent = uncovered[:cut], uncovered[cut:]
    if not old:
        return _rows_to_messages(uncovered, summary, render), info

    transcript = "\n".join(
        f"[{row[1]}] {row[2][:400]}" for row in old
    )
    try:
        reply = gateway.complete(
            [
                {"role": "system", "content": _COMPRESS_SYSTEM},
                {"role": "user", "content": f"待压缩的旧对话：\n{transcript}"},
            ],
            model=model,
            tools=None,
            purpose="compress",
            run_id=run_id,
            session_id=session_id,
            run_budget=run_budget,
            cancel=cancel,
        )
        fresh = reply.text.strip()
    except Exception:  # noqa: BLE001 —— 降级：宁可不压也不能篡改事实或中断会话
        info["reason"] = "summary_failed"
        return _rows_to_messages(uncovered, summary, render), info
    if not fresh:
        info["reason"] = "summary_empty"
        return _rows_to_messages(uncovered, summary, render), info

    merged = f"{summary[3]}\n{fresh}" if summary is not None else fresh
    from_msg = summary[1] if summary is not None else old[0][0]
    to_msg = old[-1][0]
    if save is not None:
        try:
            new_id, version, created = save(
                content=merged,
                from_msg_id=from_msg,
                to_msg_id=to_msg,
                supersedes=summary[0] if summary is not None else None,
            )
            info.update(
                summarized=created, summary_id=new_id, version=version,
                from_msg_id=from_msg, to_msg_id=to_msg,
            )
        except Exception as exc:  # noqa: BLE001 —— 存不下就只影响本轮，不影响回答
            info["reason"] = f"save_failed:{exc}"
    else:
        info["summarized"] = True
    return _rows_to_messages(recent, (0, from_msg, to_msg, merged, 0), render), info


def _rows_to_messages(
    rows: list[tuple[int, str, str]],
    summary: tuple[int, int, int, str, int] | None,
    render: Callable[[list[tuple[int, str, str]]], list[dict[str, Any]]] | None = None,
) -> list[dict[str, Any]]:
    """行 → 消息；有摘要时把它并于首条 user（或单独成条）。"""
    messages = render(rows) if render else [{"role": row[1], "content": row[2]} for row in rows]
    if summary is None:
        return messages
    first_user = None
    start = 0
    if messages and messages[0]["role"] == "user":
        first_user = messages[0]["content"]
        start = 1
    return [_summary_message(summary[3], first_user), *messages[start:]]


def preview_context(
    rows: list[tuple[int, str, str]],
    summary: tuple[int, int, int, str, int] | None,
    *,
    render: Callable[[list[tuple[int, str, str]]], list[dict[str, Any]]] | None = None,
) -> list[dict[str, Any]]:
    """只读预览保留窗口；不压缩、不调用模型、不保存摘要。"""
    uncovered = [row for row in rows if summary is None or row[0] > summary[2]]
    return _rows_to_messages(uncovered, summary, render)


def assert_invariants(messages: list[dict[str, Any]]) -> None:
    """消息不变量（Hermes 硬约束）。违反立即 AssertionError——坏请求宁可在
    本地炸掉暴露 bug，也不发给对角色交替敏感的模型。"""
    if not messages:
        return
    assert messages[0].get("role") == "system", "消息序列必须以 system 开头"
    assert (
        sum(1 for m in messages if m.get("role") == "system") == 1
    ), "system 只能有一条"
    pending: set[str] = set()
    for i, m in enumerate(messages[1:], start=1):
        if m.get("role") == "tool":
            call_id = m.get("tool_call_id")
            assert call_id in pending, f"第 {i} 条 tool 结果没有对应的待完成调用或已重复"
            pending.remove(call_id)
            continue
        assert not pending, f"第 {i} 条消息打断了尚未返回结果的工具调用"
        calls = m.get("tool_calls") or []
        if calls:
            assert m.get("role") == "assistant", "只有 assistant 可以声明 tool_calls"
            ids = [call.get("id") for call in calls]
            assert all(isinstance(cid, str) and cid for cid in ids), "工具调用必须有非空 id"
            assert len(ids) == len(set(ids)), "同一批工具调用的 id 不可重复"
            pending.update(ids)
    assert not pending, "消息尾部存在缺少结果的工具调用"


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


def _text_of(content: Any) -> str:
    """content 可能是 str，也可能是多部件列表（D15 的图像消息）。

    图像部件按"已发送图像"占位计入体量/转写——base64 绝不能进压缩 prompt：
    一张 1MB 的截图编码后是 1.3MB 文本，会把"压缩上下文"变成"复制巨无霸"。
    """
    if isinstance(content, str):
        return content
    if isinstance(content, list):
        parts = []
        for part in content:
            if isinstance(part, dict):
                if part.get("type") == "text" and isinstance(part.get("text"), str):
                    parts.append(part["text"])
                elif part.get("type") in {"image_url", "image", "input_image"}:
                    parts.append("（此处已向模型发送一张图像）")
            else:
                parts.append(str(part))
        return "\n".join(parts)
    return str(content or "")


def context_chars(messages: list[dict[str, Any]]) -> int:
    """粗算上下文体量（字符口径）：content 字符 + tool_calls 的 JSON。

    压缩阈值自 D11 收口后按 `estimate_tokens` 的 **token 口径**比较（见
    `_over_threshold`）；本函数保留给诊断/测试用字符视图。
    """
    total = 0
    for m in messages:
        total += len(_text_of(m.get("content")))
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
    run_id: str | None = None,
    session_id: int | None = None,
    run_budget: Any = None,
    cancel: Any = None,
) -> list[dict[str, Any]]:
    """超阈值时把旧块压缩成摘要；绝大多数调用原样返回（先查体量再动手）。"""
    if len(messages) < min_messages or not _over_threshold(messages, trigger_chars):
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
    # 回合内工具输出很多时，保留最新运行快照及它之后的完整原子块。
    # 只压较早的前缀；不能为压缩而丢掉当前时间、最新档案或用户约束。
    current_retained = any(block[0].get('role')=='user'
                           and CONTEXT_HEADER in _text_of(block[0].get('content')) for block in recent)
    anchor = None if current_retained else next((i for i in range(len(blocks)-1,-1,-1)
                   if blocks[i][0].get('role')=='user'
                   and CONTEXT_HEADER in _text_of(blocks[i][0].get('content'))), None)
    if anchor is not None:
        recent = blocks[anchor:] + recent
        blocks = blocks[:anchor]
    if not blocks:  # 全在保留窗内，无需压
        return messages

    old = [m for block in blocks for m in block]
    transcript = "\n".join(
        f"[{m['role']}] {_text_of(m.get('content'))[:400]}" for m in old
    )
    try:
        reply = gateway.complete(
            [
                {"role": "system", "content": _COMPRESS_SYSTEM},
                {"role": "user", "content": f"待压缩的旧对话：\n{transcript}"},
            ],
            model=model,
            tools=None,
            purpose="compress",
            run_id=run_id,
            session_id=session_id,
            run_budget=run_budget,
            cancel=cancel,
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
