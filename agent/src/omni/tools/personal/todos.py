"""个人能力：todos；工具仅使用注入依赖，不构造运行时。"""

from __future__ import annotations

from typing import Any

from ...memory.store import MemoryStore
from ..registry import Risk, SessionRef, Tool, ToolRegistry


def register_todo_tools(registry: ToolRegistry, store: MemoryStore, session_ref: SessionRef) -> None:
    """todo（SAFE）：agent 自维护多步任务清单（Hermes 内联工具的等价实现）。

    状态按会话隔离、跨回合保留；"当前会话"由 SessionRef 在派发时刻提供
    （thread-local，多通道并发各看各的），工具函数本身无状态。
    工作台聊天侧栏与它同源展示。
    """

    def todo(args: dict[str, Any]) -> str:
        sid = session_ref.get()
        if sid is None:
            return "错误：会话上下文缺失（todo 只能在对话过程中使用）"
        op = str(args.get("operation", "view"))
        if op == "view":
            rows = store.todo_list(sid)
            if not rows:
                return "（待办清单为空；多步任务建议先 add 拆解步骤，完成一步 done 一步）"
            lines = []
            for tid, content, done in rows:
                mark = "x" if done else " "
                lines.append(f"[{mark}] #{tid} {content}")
            return "待办清单：\n" + "\n".join(lines)
        if op == "add":
            content = str(args.get("content", "")).strip()
            if not content:
                return "错误：content 不能为空"
            tid = store.todo_add(sid, content)
            return f"已加待办 #{tid}：{content}"
        if op in ("done", "undo"):
            try:
                tid = int(args.get("id", 0))
            except (TypeError, ValueError):
                return "错误：id 必须是整数（先 view 查看编号）"
            if not store.todo_set_done(sid, tid, done=(op == "done")):
                return f"错误：不存在 #{tid}（先 view 查看编号）"
            return "已标记完成" if op == "done" else "已撤销完成标记"
        if op == "delete":
            try:
                tid = int(args.get("id", 0))
            except (TypeError, ValueError):
                return "错误：id 必须是整数（先 view 查看编号）"
            return "已删除" if store.todo_delete(sid, tid) else f"错误：不存在 #{tid}"
        if op == "clear":
            n = store.todo_clear(sid, done_only=bool(args.get("done_only", False)))
            scope = "已完成" if args.get("done_only") else ""
            return f"已清空 {n} 条{scope}待办"
        return f"错误：未知操作 {op}（view/add/done/undo/delete/clear）"

    registry.register(
        Tool(
            name="todo",
            description=(
                "维护本会话的多步任务清单（跨回合保留，工作台可见）。"
                "operation: view 查看 / add 添加(content) / done 完成(id) / "
                "undo 撤销完成(id) / delete 删除(id) / clear 清空(done_only=true 只清已完成)。"
                "多步任务开工前先 add 拆解步骤，每完成一步 done 一步"
            ),
            parameters={
                "type": "object",
                "properties": {
                    "operation": {
                        "type": "string",
                        "enum": ["view", "add", "done", "undo", "delete", "clear"],
                    },
                    "content": {"type": "string", "description": "待办内容（add 用）"},
                    "id": {"type": "integer", "description": "待办编号（done/undo/delete 用，view 里有）"},
                    "done_only": {"type": "boolean", "description": "clear 时只清已完成（默认 false）"},
                },
                "required": ["operation"],
            },
            func=todo,
            toolset="core",
            risk=Risk.SAFE,
        )
    )
