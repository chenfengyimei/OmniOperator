"""内置工具集（toolset=core）：时间 / 记住 / 回忆。

新增能力域（bilibili/qq/android…）时各自建包，接入规则见 docs/ARCHITECTURE.md §5。
"""
from __future__ import annotations

import datetime as dt
from typing import Any

from ..memory.store import MemoryStore
from .registry import Risk, Tool, ToolRegistry


def register_core_tools(registry: ToolRegistry, store: MemoryStore) -> None:
    """把 core 工具集注册进给定注册表。

    组装根（bootstrap）持有唯一的 ToolRegistry，各能力域只负责往里注册，
    这样 P1+ 的 bilibili/qq/android 等工具集可以无冲突合并
    （接入规则见 docs/ARCHITECTURE.md §5）。
    P0 全部 safe 级（只读或本地写库），后续写操作工具必须声明更高级别。
    """

    def get_time(_: dict[str, Any]) -> str:
        return dt.datetime.now().astimezone().strftime("%Y-%m-%d %H:%M:%S %A")

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
            description="从长期记忆里检索相关信息。query 建议用简短关键词（如'猫 名字'、'拿铁'），也支持整句提问",
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
