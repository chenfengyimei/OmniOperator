"""内置工具集（toolset=core）：时间 / 记忆 / 历史搜索 / 用户档案。

子注册器可单独复用——回合后 review（core/review.py）只取 memory+profile，
遵循最小权限。新增能力域（bilibili/qq/android…）各自建包，
接入规则见 docs/ARCHITECTURE.md §5。
"""
from __future__ import annotations

import datetime as dt
from typing import Any

from ..memory.store import MemoryStore
from .registry import Risk, Tool, ToolRegistry


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


def register_core_tools(registry: ToolRegistry, store: MemoryStore) -> None:
    """core 工具集总入口：组装根（bootstrap）持有唯一 ToolRegistry。"""
    register_time_tool(registry)
    register_memory_tools(registry, store)
    register_profile_tools(registry, store)
