"""Agent 核心循环：组装上下文 → 调 LLM → 执行工具 → 直到给出回答。"""
from __future__ import annotations

import contextlib
import datetime as dt
from typing import Any

from ..config import AppConfig
from ..llm.gateway import Gateway, to_openai_tool_call
from ..memory.resident import ResidentMemory
from ..memory.runs import RunStore
from ..memory.store import MemoryStore
from ..safety.gates import Approver
from ..tools.registry import SessionRef, ToolRegistry
from ..tools.runctx import RunRef
from .compress import assert_invariants, compress_if_needed
from .run import ActiveRuns, CancelToken, RunCancelled, RunContext, RunIdentity, TurnResult

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
        "skill_history",
        "skill_rollback",
        "delegate_task",
    }
)


class _NullRunStore:
    """没有注入 RunStore 时的空实现（嵌入式用法/单测）：不落库，语义不变。"""

    def append_event(self, run_id: str, kind: str, data: dict[str, Any] | None = None) -> int:
        return -1

    def touch(self, run_id: str, **_kwargs: Any) -> bool:
        return True

    def finish(self, run_id: str, status: str, **_kwargs: Any) -> bool:
        return True

    def get(self, run_id: str) -> None:
        return None


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
        runs: RunStore | None = None,
        active_runs: ActiveRuns | None = None,
        run_ref: RunRef | None = None,
        owner: str = "",
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
        self._runs = runs
        self._active_runs = active_runs
        self._run_ref = run_ref
        self._owner = owner or "cli:0"
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

    # ---------------------------------------------------------------- 回合入口

    def chat(
        self,
        user_text: str,
        *,
        session_id: int | None = None,
        personal_context: bool = True,
        channel: str = "cli",
        principal: str = "owner",
    ) -> str:
        """处理一条用户消息，返回最终回答（中间工具循环不落库）。

        session_id 覆盖用于多通道并发：QQ 等通道按各自绑定传会话，
        不依赖 self.session_id（那是 CLI/Web 的当前会话，可能被随时切换）。

        personal_context=False 用于他人可见的场景（群聊）：不注入档案/常驻笔记，
        并隐藏只服务所有者的个人记忆工具。
        """
        return self.run_turn(
            user_text,
            session_id=session_id,
            personal_context=personal_context,
            channel=channel,
            principal=principal,
        ).answer

    def run_turn(
        self,
        user_text: str,
        *,
        session_id: int | None = None,
        personal_context: bool = True,
        channel: str = "cli",
        principal: str = "owner",
        parent: RunContext | None = None,
    ) -> TurnResult:
        """跑一个完整回合并记录 run（状态、阶段、事件）。

        返回值里带 run_id：通道用它查事件流、界面用它显示进度。
        取消不会抛给调用方——被取消的回合返回 `status="cancelled"` 的回答文本，
        让通道决定怎么展示（网页刷新历史时也能看到"这轮被取消了"）。
        """
        sid = session_id if session_id is not None else self.session_id
        run = self._start_run(
            sid,
            channel=channel,
            principal=principal,
            personal_context=personal_context,
            parent=parent,
        )
        if parent is not None:
            parent.cancel_token.link_child(run.cancel_token)
        try:
            # 会话指向与运行作用域都要在本回合内绑定，结束（含异常）自动恢复
            with contextlib.ExitStack() as stack:
                if self._session_ref is not None:
                    stack.enter_context(self._session_ref.bind(sid))
                if self._run_ref is not None:
                    stack.enter_context(self._run_ref.bind(run))
                answer = self._chat(
                    user_text, sid, personal_context=personal_context, run=run
                )
            run.event("answer", chars=len(answer))
            run.finish("done")
            return TurnResult(answer=answer, run_id=run.run_id, status="done")
        except RunCancelled as exc:
            note = f"（已取消：{exc.reason}。本轮未继续执行后续步骤；已发生的操作不会回滚）"
            self.store.append_message(sid, "assistant", note, auto_title=False)
            run.event("cancelled", reason=exc.reason)
            run.finish("cancelled", error_code="run_cancelled", error_text=exc.reason)
            return TurnResult(
                answer=note, run_id=run.run_id, status="cancelled", error_code="run_cancelled"
            )
        except Exception as exc:  # noqa: BLE001 —— 状态要留痕，异常照旧上抛
            run.event("failed", error=str(exc)[:300])
            run.finish("failed", error_code="exception", error_text=str(exc)[:300])
            raise
        finally:
            if parent is not None:
                parent.cancel_token.unlink_child(run.cancel_token)
            if self._active_runs is not None:
                self._active_runs.unregister(run.run_id)

    def _start_run(
        self,
        sid: int | None,
        *,
        channel: str,
        principal: str,
        personal_context: bool,
        parent: RunContext | None,
    ) -> RunContext:
        steps = max(1, int(self.config.limits.max_agent_steps))
        if parent is not None:
            # 子 run 只能缩小预算：父剩几步，子最多几步
            steps = max(1, min(steps, parent.steps_left))
        identity = RunIdentity.new(
            session_id=sid,
            channel=channel,
            principal=principal,
            parent_run_id=parent.run_id if parent is not None else None,
            scope="private" if personal_context else "group",
        )
        if self._runs is None:
            run = RunContext(identity=identity, store=_NullRunStore(), max_steps=steps)
        else:
            self._runs.create(
                run_id=identity.run_id,
                session_id=sid,
                channel=channel,
                principal=principal,
                owner=self._owner,
                parent_run_id=identity.parent_run_id,
                status="running",
            )
            run = RunContext(identity=identity, store=self._runs, max_steps=steps)
        run.event(
            "run_started",
            channel=channel,
            principal=principal,
            scope=identity.scope,
            parent_run_id=identity.parent_run_id,
            max_steps=steps,
        )
        if self._active_runs is not None:
            self._active_runs.register(run)
        return run

    # ---------------------------------------------------------------- 循环

    def _chat(
        self,
        user_text: str,
        sid: int,
        *,
        personal_context: bool = True,
        run: RunContext | None = None,
    ) -> str:
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
        cancel = run.cancel_token if run is not None else None

        for _ in range(self.config.limits.max_agent_steps):
            if run is not None:
                run.check()  # 模型调用前的检查点
                if not run.consume_step():
                    break  # 步数预算用完 → 走下面的收敛调用
                run.phase("model")
                run.event("model_start", step=run.steps_used)
            messages = self._maybe_compress(messages)
            assert_invariants(messages)
            reply = self.gateway.complete(
                messages, model=self.model, tools=self.registry.specs(exclude=denied)
            )
            if run is not None:
                # 取消不能杀线程：模型请求会照常返回，这里再决定是否继续
                run.check()
                run.event(
                    "model_end",
                    text_chars=len(reply.text or ""),
                    tool_calls=[c.function.name for c in reply.tool_calls],
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
                if run is not None:
                    run.check()  # 每个工具调用前的检查点
                    run.phase("tool")
                result = self.registry.execute(
                    call.function.name,
                    call.function.arguments,
                    approver=self.approver,
                    denied_tools=denied,
                    cancel=cancel,
                )
                if run is not None:
                    run.event(
                        "tool", name=call.function.name, **result.brief()
                    )
                messages.append(
                    {
                        "role": "tool",
                        "tool_call_id": call.id,
                        "content": result.to_message(),
                    }
                )

        if run is not None:
            run.check()
            run.phase("final")
            run.event("final_call", reason="step_budget")
        # 步数预算耗尽：一次无工具的 grace call，强制收敛出最终回答
        messages = self._maybe_compress(messages)
        assert_invariants(messages)
        reply = self.gateway.complete(messages, model=self.model, tools=None)
        answer = reply.text.strip() or "（达到工具调用轮数上限，先停在这里）"
        self.store.append_message(sid, "assistant", answer, auto_title=False)
        return answer
