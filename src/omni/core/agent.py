"""Agent 核心循环：组装上下文 → 调 LLM → 执行工具 → 直到给出回答。"""
from __future__ import annotations

import datetime as dt
from typing import Any

from ..config import AppConfig
from ..llm.gateway import Gateway
from ..memory.store import MemoryStore
from .tools import ToolRegistry


class Agent:
    def __init__(
        self,
        config: AppConfig,
        gateway: Gateway,
        store: MemoryStore,
        registry: ToolRegistry,
        session_id: int,
    ) -> None:
        self.config = config
        self.gateway = gateway
        self.store = store
        self.registry = registry
        self.session_id = session_id
        self.model = gateway.resolve("chat")

    def _system_prompt(self) -> str:
        now = dt.datetime.now().astimezone().strftime("%Y-%m-%d %H:%M:%S %A")
        return self.config.persona.system_prompt.replace("{now}", now)

    def chat(self, user_text: str) -> str:
        """处理一条用户消息，返回最终回答（中间工具循环不落库）。"""
        self.store.append_message(self.session_id, "user", user_text)
        history = self.store.recent_messages(
            self.session_id, self.config.limits.max_history_messages
        )
        messages: list[dict[str, Any]] = [
            {"role": "system", "content": self._system_prompt()}
        ]
        messages += [{"role": role, "content": content} for role, content in history]

        for _ in range(self.config.limits.max_agent_steps):
            reply = self.gateway.complete(
                messages, model=self.model, tools=self.registry.specs()
            )
            if not reply.tool_calls:
                answer = reply.text.strip() or "（模型没有返回内容）"
                self.store.append_message(
                    self.session_id, "assistant", answer, auto_title=False
                )
                return answer
            # 有工具调用：执行并把结果回填，继续循环
            messages.append(
                {
                    "role": "assistant",
                    "content": reply.text or "",
                    "tool_calls": [self._dump_call(c) for c in reply.tool_calls],
                }
            )
            for call in reply.tool_calls:
                result = self.registry.dispatch(call.function.name, call.function.arguments)
                messages.append(
                    {"role": "tool", "tool_call_id": call.id, "content": result}
                )

        # 达到步数上限：不再给工具，逼出最终回答
        reply = self.gateway.complete(messages, model=self.model, tools=None)
        answer = reply.text.strip() or "（达到工具调用轮数上限，先停在这里）"
        self.store.append_message(
            self.session_id, "assistant", answer, auto_title=False
        )
        return answer

    @staticmethod
    def _dump_call(call: Any) -> dict[str, Any]:
        """LiteLLM ToolCall → OpenAI 消息格式。"""
        return {
            "id": call.id,
            "type": "function",
            "function": {
                "name": call.function.name,
                "arguments": call.function.arguments,
            },
        }
