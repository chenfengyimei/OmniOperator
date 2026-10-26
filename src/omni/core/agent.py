"""Agent 核心循环：组装上下文 → 调 LLM → 执行工具 → 直到给出回答。"""
from __future__ import annotations

import datetime as dt
from typing import Any

from ..config import AppConfig
from ..llm.gateway import Gateway, to_openai_tool_call
from ..memory.store import MemoryStore
from ..safety.gates import Approver
from ..tools.registry import ToolRegistry


class Agent:
    def __init__(
        self,
        config: AppConfig,
        gateway: Gateway,
        store: MemoryStore,
        registry: ToolRegistry,
        session_id: int,
        *,
        approver: Approver | None = None,
    ) -> None:
        self.config = config
        self.gateway = gateway
        self.store = store
        self.registry = registry
        self.approver = approver
        self.session_id = session_id
        self.model = gateway.resolve("chat")

    def _system_prompt(self) -> str:
        now = dt.datetime.now().astimezone().strftime("%Y-%m-%d %H:%M:%S %A")
        prompt = self.config.persona.system_prompt.replace("{now}", now)
        profile_block = self.store.profile_block()  # 人物档案快照（有内容才注入）
        if profile_block:
            prompt = f"{prompt}\n\n{profile_block}"
        return prompt

    def chat(self, user_text: str, *, session_id: int | None = None) -> str:
        """处理一条用户消息，返回最终回答（中间工具循环不落库）。

        session_id 覆盖用于多通道并发：QQ 等通道按各自绑定传会话，
        不依赖 self.session_id（那是 CLI/Web 的当前会话，可能被随时切换）。
        """
        sid = session_id if session_id is not None else self.session_id
        self.store.append_message(sid, "user", user_text)
        history = self.store.recent_messages(
            sid, self.config.limits.max_history_messages
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
                self.store.append_message(sid, "assistant", answer, auto_title=False)
                return answer
            # 有工具调用：执行并把结果回填，继续循环
            messages.append(
                {
                    "role": "assistant",
                    "content": reply.text or "",
                    "tool_calls": [to_openai_tool_call(c) for c in reply.tool_calls],
                }
            )
            for call in reply.tool_calls:
                result = self.registry.dispatch(
                    call.function.name,
                    call.function.arguments,
                    approver=self.approver,
                )
                messages.append(
                    {"role": "tool", "tool_call_id": call.id, "content": result}
                )

        # 步数预算耗尽：一次无工具的 grace call，强制收敛出最终回答
        reply = self.gateway.complete(messages, model=self.model, tools=None)
        answer = reply.text.strip() or "（达到工具调用轮数上限，先停在这里）"
        self.store.append_message(sid, "assistant", answer, auto_title=False)
        return answer
