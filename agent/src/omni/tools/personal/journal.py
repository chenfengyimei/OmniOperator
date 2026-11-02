"""记忆系统工具（二）：日/周/月笔记的读取（M3/M4/M5）。

笔记是整理后的压缩记忆：模型想快速知道"某天/某周发生了什么"先看笔记，
需要细节再用 dialogue_read/session_log_read 下钻——这正是分层读取的意义。
"""

from __future__ import annotations

from typing import Any

from ...memory.journal import KINDS
from ..registry import Risk, Tool, ToolRegistry

_KIND_LABELS = {"daily": "每日", "weekly": "每周", "monthly": "每月"}


def register_journal_tools(registry: ToolRegistry, journal: Any) -> None:
    def journal_view(args: dict[str, Any]) -> str:
        kind = str(args.get("kind", "daily")).strip()
        if kind not in KINDS:
            return f"错误：kind 必须是 {'/'.join(KINDS)}"
        period = str(args.get("period", "") or "").strip()
        if period:
            note = journal.note(kind, period)
            if note is None:
                return f"没有 {_KIND_LABELS[kind]}笔记 {period}（或尚未整理）。"
            if note["status"] != "done":
                return f"{_KIND_LABELS[kind]}笔记 {period} 状态是 {note['status']}（{note['error'] or '整理中'}）。"
            return (
                f"{_KIND_LABELS[kind]}笔记 {period}"
                f"（{note['finished_at'][:19]} 由 {note['model'] or '未知模型'} 整理）：\n\n"
                f"{note['content']}"
            )
        note = journal.latest_note(kind)
        if note is None or note["status"] != "done":
            return f"还没有已完成的{_KIND_LABELS[kind]}笔记（整理由心跳在每天结束时自动进行）。"
        return (
            f"最近一篇{_KIND_LABELS[kind]}笔记 {note['period_key']}"
            f"（{note['finished_at'][:19]} 整理）：\n\n{note['content']}"
        )

    registry.register(
        Tool(
            name="journal_view",
            description=(
                "查看记忆系统整理的笔记：daily=每日笔记（那天做了什么）、"
                "weekly=每周大纲、monthly=每月时间点索引。"
                "想快速了解某天/某周发生了什么先看笔记；要细节再下钻 dialogue_read。"
                "period 格式：daily 2026-10-30 / weekly 2026-W44 / monthly 2026-10；不传=最近一篇"
            ),
            parameters={
                "type": "object",
                "properties": {
                    "kind": {"type": "string", "description": "daily / weekly / monthly（默认 daily）"},
                    "period": {"type": "string", "description": "时期；不传看最近一篇"},
                },
                "required": [],
            },
            func=journal_view,
            toolset="personal",
            risk=Risk.SAFE,
            read_only=True,
        )
    )
