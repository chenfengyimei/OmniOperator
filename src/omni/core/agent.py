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

# 群聊作用域下不提供的工具：会读全局个人数据或跨会话历史（D12/D22 会换成正式的作用域策略）
GROUP_DENIED_TOOLS = frozenset(
    {
        "recall",
        "search_history",
        "remember",
        "profile_manage",
        "resident_memory",
        "daily_digest",
        "skills_list",
        "skill_view",
        "skill_create",
        "skill_patch",
        "skill_rewrite",
        "delegate_task",
    }
)


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

    def _system_prompt(self, *, include_personal: bool = True) -> str:
        now = dt.datetime.now().astimezone().strftime("%Y-%m-%d %H:%M:%S %A")
        prompt = self.config.persona.system_prompt.replace("{now}", now)
        if not include_personal:
            # 群聊/他人可见场景：只给"它是谁"，不给所有者的私人资料
            return prompt + (
                "\n\n（当前是群聊场景：不注入所有者的个人档案、常驻笔记与其它会话历史。"
                "不要假设自己知道群外信息；涉及私人资料的请求请让用户私下联系。）"
            )
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

    def chat(
        self,
        user_text: str,
        *,
        session_id: int | None = None,
        personal_context: bool = True,
    ) -> str:
        """处理一条用户消息，返回最终回答（中间工具循环不落库）。

        session_id 覆盖用于多通道并发：QQ 等通道按各自绑定传会话，
        不依赖 self.session_id（那是 CLI/Web 的当前会话，可能被随时切换）。

        personal_context=False 用于他人可见的场景（群聊）：不注入档案/常驻笔记，
        并隐藏只服务所有者的个人记忆工具——在 D12 的完整作用域策略落地前先把口子关死。
        """
        sid = session_id if session_id is not None else self.session_id
        if self._session_ref is not None:
            with self._session_ref.bind(sid):
                return self._chat(user_text, sid, personal_context=personal_context)
        return self._chat(user_text, sid, personal_context=personal_context)

    def _chat(self, user_text: str, sid: int, *, personal_context: bool = True) -> str:
        """在已绑定会话的作用域内执行完整回合，不改变通道选中的 session_id。"""
        denied = frozenset() if personal_context else GROUP_DENIED_TOOLS
        self.store.append_message(sid, "user", user_text)
        history = self.store.recent_messages(
            sid, self.config.limits.max_history_messages
        )
        messages: list[dict[str, Any]] = [
            {
                "role": "system",
                "content": self._system_prompt(include_personal=personal_context),
            }
        ]
        messages += [{"role": role, "content": content} for role, content in history]
        messages = self._maybe_compress(messages)

        for _ in range(self.config.limits.max_agent_steps):
            messages = self._maybe_compress(messages)
            assert_invariants(messages)
            reply = self.gateway.complete(
                messages, model=self.model, tools=self.registry.specs(exclude=denied)
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
                    denied_tools=denied,
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
