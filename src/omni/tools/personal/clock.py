"""个人能力：clock；工具仅使用注入依赖，不构造运行时。"""
from __future__ import annotations

import datetime as dt
from typing import Any
from ..registry import Risk, Tool, ToolRegistry


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
            read_only=True,
        )
    )
