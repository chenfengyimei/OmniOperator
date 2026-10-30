"""个人内置能力的注册入口：事实、档案、常驻、待办、摘要和整理。"""
from __future__ import annotations

from typing import Any
from ...memory.store import MemoryStore
from ..registry import ToolRegistry
from .clock import register_time_tool
from .facts import register_memory_tools
from .profile import register_profile_tools
from .digest import register_digest_tool
from .todos import register_todo_tools
from .resident import register_resident_tools
from .refine import register_refine_tool


def register_core_tools(
    registry: ToolRegistry,
    store: MemoryStore,
    *,
    guard: Any = None,
    run_ref: Any = None,
    session_ref: Any = None,
    recall_prefix: bool = False,
    source: str = "chat",
    retriever: Any = None,
) -> None:
    """core 工具集总入口：组装根（bootstrap）持有唯一 ToolRegistry。"""
    register_time_tool(registry)
    register_memory_tools(
        registry,
        store,
        guard=guard,
        run_ref=run_ref,
        session_ref=session_ref,
        recall_prefix=recall_prefix,
        source=source,
        retriever=retriever,
    )
    register_profile_tools(registry, store, guard=guard, source=source)

__all__ = ['register_time_tool', 'register_memory_tools', 'register_profile_tools', 'register_digest_tool', 'register_todo_tools', 'register_resident_tools', 'register_refine_tool', 'register_core_tools']
