"""LiteLLM 统一网关：一套 OpenAI 格式接所有供应商（海外/国产/本地 Ollama）。

模型串格式为 provider/model，例如：
- deepseek/deepseek-chat      （需 DEEPSEEK_API_KEY）
- anthropic/claude-sonnet-4-5 （需 ANTHROPIC_API_KEY）
- ollama/qwen2.5:7b           （本地，无需密钥）
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

import litellm

from ..config import Models

litellm.suppress_debug_info = True  # 关掉 import 时打印的 provider 调试信息


class GatewayError(RuntimeError):
    """LLM 调用失败（密钥缺失 / 模型名错误 / 网络不通等）。"""


@dataclass
class Reply:
    """归一化的模型回复。"""

    text: str = ""
    tool_calls: list[Any] = field(default_factory=list)  # LiteLLM ToolCall 对象


class Gateway:
    def __init__(self, models: Models) -> None:
        self.models = models

    def resolve(self, route: str) -> str:
        """路由名（chat/vision/local）→ 配置的模型串。"""
        try:
            return getattr(self.models, route)
        except AttributeError as exc:
            raise GatewayError(f"未知模型路由: {route}") from exc

    def complete(
        self,
        messages: list[dict[str, Any]],
        *,
        model: str,
        tools: list[dict[str, Any]] | None = None,
    ) -> Reply:
        try:
            response = litellm.completion(model=model, messages=messages, tools=tools)
        except Exception as exc:  # noqa: BLE001 —— 统一包装成可读错误
            raise GatewayError(f"模型 {model} 调用失败: {exc}") from exc
        message = response.choices[0].message
        return Reply(
            text=message.content or "",
            tool_calls=list(getattr(message, "tool_calls", None) or []),
        )
