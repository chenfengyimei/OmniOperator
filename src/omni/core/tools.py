"""工具注册表：把 Python 函数暴露为 LLM function calling（P0 极简版）。

约定：工具执行出错不抛异常，而是把错误字符串回传给 LLM，让它自行调整重试。
"""
from __future__ import annotations

import datetime as dt
import json
from dataclasses import dataclass
from typing import Any, Callable

from ..memory.store import MemoryStore


@dataclass
class Tool:
    name: str
    description: str
    parameters: dict[str, Any]  # JSON Schema
    func: Callable[[dict[str, Any]], str]


class ToolRegistry:
    def __init__(self) -> None:
        self._tools: dict[str, Tool] = {}

    def register(self, tool: Tool) -> None:
        self._tools[tool.name] = tool

    def names(self) -> list[str]:
        return list(self._tools)

    def specs(self) -> list[dict[str, Any]]:
        """生成 LiteLLM/OpenAI 格式的 tools 参数。"""
        return [
            {
                "type": "function",
                "function": {
                    "name": t.name,
                    "description": t.description,
                    "parameters": t.parameters,
                },
            }
            for t in self._tools.values()
        ]

    def dispatch(self, name: str, arguments: str | dict[str, Any]) -> str:
        tool = self._tools.get(name)
        if tool is None:
            return f"错误：没有名为 {name} 的工具"
        if isinstance(arguments, str):
            try:
                arguments = json.loads(arguments or "{}")
            except json.JSONDecodeError as exc:
                return f"错误：工具参数不是合法 JSON（{exc}）"
        try:
            return tool.func(arguments or {})
        except Exception as exc:  # noqa: BLE001 —— 回传错误让 LLM 自行调整
            return f"工具 {name} 执行出错: {exc}"


def build_default_tools(store: MemoryStore) -> ToolRegistry:
    """P0 内置工具：时间、记住、回忆。P1 起按能力域拆分到 tools/ 各包。"""
    registry = ToolRegistry()

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
        )
    )
    registry.register(
        Tool(
            name="recall",
            description="从长期记忆里检索相关信息",
            parameters={
                "type": "object",
                "properties": {"query": {"type": "string", "description": "检索关键词"}},
                "required": ["query"],
            },
            func=recall,
        )
    )
    return registry
