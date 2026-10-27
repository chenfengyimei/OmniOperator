"""内置工具集（toolset=core）：时间 / 记忆 / 历史搜索 / 用户档案。

子注册器可单独复用——回合后 review（core/review.py）只取 memory+profile，
遵循最小权限。新增能力域（bilibili/qq/android…）各自建包，
接入规则见 docs/ARCHITECTURE.md §5。
"""
from __future__ import annotations

import datetime as dt
from typing import Any

from ..memory.store import PROFILE_MAX_CHARS, MemoryStore
from .registry import Risk, SessionRef, Tool, ToolRegistry


def register_time_tool(registry: ToolRegistry) -> None:
    def get_time(_: dict[str, Any]) -> str:
        return dt.datetime.now().astimezone().strftime("%Y-%m-%d %H:%M:%S %A")

    registry.register(
        Tool(
            name="get_time",
            description="获取当前时间",
            parameters={"type": "object", "properties": {}, "required": []},
            func=get_time,
            toolset="core",
            risk=Risk.SAFE,
        )
    )


def register_memory_tools(registry: ToolRegistry, store: MemoryStore) -> None:
    def remember(args: dict[str, Any]) -> str:
        content = str(args.get("text", "")).strip()
        if not content:
            return "错误：text 不能为空"
        store.save_fact(content)
        return f"已记住：{content}"

    def recall(args: dict[str, Any]) -> str:
        query = str(args.get("query", "")).strip()
        hits = store.search_facts(query, limit=8)
        if not hits:
            return "没有找到相关记忆。"
        return "\n".join(f"- {h}" for h in hits)

    def search_history(args: dict[str, Any]) -> str:
        query = str(args.get("query", "")).strip()
        hits = store.search_messages(query, limit=8)
        if not hits:
            return "没有在历史对话里找到相关内容。"
        return "\n".join(
            f"- [会话 #{sid} · {role} · {created[:16]}] {content[:120]}"
            for sid, role, content, created in hits
        )

    registry.register(
        Tool(
            name="remember",
            description="把重要信息写入长期记忆（用户偏好、约定、结论等），跨会话保存",
            parameters={
                "type": "object",
                "properties": {
                    "text": {
                        "type": "string",
                        "description": "要记住的内容，一句自包含的话",
                    }
                },
                "required": ["text"],
            },
            func=remember,
            toolset="core",
            risk=Risk.SAFE,
        )
    )
    registry.register(
        Tool(
            name="recall",
            description=(
                "从长期记忆里检索相关信息。query 建议用简短关键词"
                "（如'猫 名字'、'拿铁'），也支持整句提问"
            ),
            parameters={
                "type": "object",
                "properties": {
                    "query": {"type": "string", "description": "检索关键词或问题，尽量简短"}
                },
                "required": ["query"],
            },
            func=recall,
            toolset="core",
            risk=Risk.SAFE,
        )
    )
    registry.register(
        Tool(
            name="search_history",
            description="搜索过去的对话记录（哪怕没有显式记住过的内容也能找到）",
            parameters={
                "type": "object",
                "properties": {
                    "query": {"type": "string", "description": "检索关键词或问题，尽量简短"}
                },
                "required": ["query"],
            },
            func=search_history,
            toolset="core",
            risk=Risk.SAFE,
        )
    )


def register_profile_tools(registry: ToolRegistry, store: MemoryStore) -> None:
    def profile_manage(args: dict[str, Any]) -> str:
        op = str(args.get("operation", "view"))
        if op == "view":
            lines = store.profile_lines()
            usage = store.profile_usage_chars()
            if not lines:
                return f"用户档案为空（{usage} 字符额度）。用 add 添加第一条。"
            body = "\n".join(f"[{lid}] {text}" for lid, text in lines)
            return f"用户档案（{usage} 字符已用）：\n{body}"
        if op == "add":
            return store.profile_add(str(args.get("content", "")))[1]
        if op == "edit":
            try:
                line_id = int(args.get("line_id", 0))
            except (TypeError, ValueError):
                return "错误：line_id 必须是整数"
            content = str(args.get("content", "")).strip()
            if not content:
                return "错误：edit 需要非空 content"
            ok = store.profile_edit(line_id, content)
            return "已更新。" if ok else f"错误：不存在第 {line_id} 条（先 view 查看编号）"
        if op == "delete":
            try:
                line_id = int(args.get("line_id", 0))
            except (TypeError, ValueError):
                return "错误：line_id 必须是整数"
            ok = store.profile_delete(line_id)
            return "已删除。" if ok else f"错误：不存在第 {line_id} 条（先 view 查看编号）"
        return f"错误：未知操作 {op}（支持 view/add/edit/delete）"

    registry.register(
        Tool(
            name="profile_manage",
            description=(
                "维护用户长期档案（职位、稳定偏好、性格等核心特征；有字符额度限制）。"
                "operation: view 查看 / add 添加 / edit 修改 / delete 删除；"
                "写满时 add 会返回当前档案与剩余额度，此时应先合并再写"
            ),
            parameters={
                "type": "object",
                "properties": {
                    "operation": {
                        "type": "string",
                        "enum": ["view", "add", "edit", "delete"],
                        "description": "操作类型",
                    },
                    "content": {"type": "string", "description": "档案行内容（add/edit 用）"},
                    "line_id": {
                        "type": "integer",
                        "description": "行编号（edit/delete 用，view 里有编号）",
                    },
                },
                "required": ["operation"],
            },
            func=profile_manage,
            toolset="core",
            risk=Risk.SAFE,
        )
    )


def register_digest_tool(registry: ToolRegistry, store: MemoryStore) -> None:
    """daily_digest（SAFE）：夜间整理的素材入口（活动统计）。

    刻意独立于 register_memory_tools——回合后 review 的白名单保持最小
    （memory+profile），digest 由组装根接到主注册表，只在主对话/心跳可用。
    """

    def daily_digest(args: dict[str, Any]) -> str:
        try:
            hours = int(args.get("hours", 24))
        except (TypeError, ValueError):
            return "错误：hours 必须是整数（1-168）"
        hours = max(1, min(hours, 168))
        since = (
            dt.datetime.now().astimezone() - dt.timedelta(hours=hours)
        ).isoformat(timespec="seconds")
        d = store.digest_since(since)
        if not d["sessions"] and d["facts_new"] == 0 and not d["reviews_recent"]:
            return f"过去 {hours} 小时没有会话、记忆或 review 活动，无需整理。"
        lines = [f"过去 {hours} 小时活动（自 {since[:16]} 起）："]
        for s in d["sessions"]:
            lines.append(f"- 会话 #{s['id']}「{s['title']}」{s['messages']} 条消息")
        lines.append(f"- 新增长期记忆 {d['facts_new']} 条：")
        for fact in d["facts_recent"][:10]:
            lines.append(f"  · {fact[:80]}")
        if d["reviews_recent"]:
            lines.append(
                "- 最近 review：" + " / ".join(x[:60] for x in d["reviews_recent"])
            )
        lines.append(
            f"- 用户档案已用 {d['profile_chars']} 字符"
            f"（额度 {PROFILE_MAX_CHARS}，临近额度应先合并再写）"
        )
        return "\n".join(lines)

    registry.register(
        Tool(
            name="daily_digest",
            description=(
                "查看过去 N 小时的活动摘要：活跃会话与消息量、新增长期记忆、"
                "最近 review、档案额度占用。夜间整理/写日报前先调它（hours 默认 24）"
            ),
            parameters={
                "type": "object",
                "properties": {
                    "hours": {
                        "type": "integer",
                        "description": "回看小时数（1-168，默认 24）",
                    }
                },
                "required": [],
            },
            func=daily_digest,
            toolset="core",
            risk=Risk.SAFE,
        )
    )


def register_todo_tools(
    registry: ToolRegistry, store: MemoryStore, session_ref: SessionRef
) -> None:
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


def register_core_tools(registry: ToolRegistry, store: MemoryStore) -> None:
    """core 工具集总入口：组装根（bootstrap）持有唯一 ToolRegistry。"""
    register_time_tool(registry)
    register_memory_tools(registry, store)
    register_profile_tools(registry, store)
