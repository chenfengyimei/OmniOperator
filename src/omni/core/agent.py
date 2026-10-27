"""Agent 核心循环：组装上下文 → 调 LLM → 执行工具 → 直到给出回答。"""
from __future__ import annotations

import datetime as dt
from typing import Any

from ..config import AppConfig
from ..llm.gateway import Gateway, to_openai_tool_call
from ..memory.resident import ResidentMemory
from ..memory.store import MemoryStore
from ..safety.gates import Approver
from ..tools.registry import SessionRef, ToolRegistry
from .compress import assert_invariants, compress_if_needed


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
        session_ref: SessionRef | None = None,
        resident: ResidentMemory | None = None,
    ) -> None:
        self.config = config
        self.gateway = gateway
        self.store = store
        self.registry = registry
        self.approver = approver
        self.session_id = session_id
        self.model = gateway.resolve("chat")
        self._session_ref = session_ref
        self._resident = resident
        # 压缩摘要用廉价模型（同回合后 review 的选型逻辑）
        review_model = config.models.review.strip()
        self._summary_model = review_model if review_model else self.model

    @property
    def session_ref(self) -> SessionRef | None:
        """会话引用（delegate_task 等需要临时切换/还原会话指向的工具用）。"""
        return self._session_ref

    def _system_prompt(self) -> str:
        now = dt.datetime.now().astimezone().strftime("%Y-%m-%d %H:%M:%S %A")
        prompt = self.config.persona.system_prompt.replace("{now}", now)
        # 常驻小记忆冻结快照（本轮内字节稳定；中途写入只落盘，下一轮生效）
        if self._resident is not None:
            memory_block = self._resident.block()
            if memory_block:
                prompt = f"{prompt}\n\n{memory_block}"
        profile_block = self.store.profile_block()  # 人物档案快照（有内容才注入）
        if profile_block:
            prompt = f"{prompt}\n\n{profile_block}"
        return prompt

    def _maybe_compress(self, messages: list[dict[str, Any]]) -> list[dict[str, Any]]:
        """长会话防膨胀：超阈值时压旧块成摘要（Hermes compression 最小版）。"""
        comp = self.config.compression
        if not comp.enabled:
            return messages
        return compress_if_needed(
            messages,
            gateway=self.gateway,
            model=self._summary_model,
            trigger_chars=comp.trigger_chars,
            keep_recent=comp.keep_recent,
            min_messages=comp.min_messages,
        )

    def chat(self, user_text: str, *, session_id: int | None = None) -> str:
        """处理一条用户消息，返回最终回答（中间工具循环不落库）。

        session_id 覆盖用于多通道并发：QQ 等通道按各自绑定传会话，
        不依赖 self.session_id（那是 CLI/Web 的当前会话，可能被随时切换）。
        """
        sid = session_id if session_id is not None else self.session_id
        # 会话级工具（todo 等）经 SessionRef 读当前会话；thread-local 保证
        # 多通道并发时各线程看到自己的 sid
        if self._session_ref is not None:
            self._session_ref.set(sid)
        self.store.append_message(sid, "user", user_text)
        history = self.store.recent_messages(
            sid, self.config.limits.max_history_messages
        )
        messages: list[dict[str, Any]] = [
            {"role": "system", "content": self._system_prompt()}
        ]
        messages += [{"role": role, "content": content} for role, content in history]
        messages = self._maybe_compress(messages)

        for _ in range(self.config.limits.max_agent_steps):
            messages = self._maybe_compress(messages)
            assert_invariants(messages)
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
        messages = self._maybe_compress(messages)
        assert_invariants(messages)
        reply = self.gateway.complete(messages, model=self.model, tools=None)
        answer = reply.text.strip() or "（达到工具调用轮数上限，先停在这里）"
        self.store.append_message(sid, "assistant", answer, auto_title=False)
        return answer
