"""子任务委派（Hermes delegate_task 同款）：把独立子任务交给一个干净会话去跑。

设计：
- 子 run 复用同一 Agent 实例（同一注册表、同一审批链：子任务的 CONFIRM
  照走确认，不是逃逸通道）；
- 独立会话（bindings: delegate:<label>-<时分秒>）——子任务历史不污染主
  对话上下文，主 agent 可用 search_history 找回完整现场；
- 防嵌套：thread-local 深度计数，委派内部再委派直接拒绝（嵌套会让上下文
  与步数预算失控）；
- 子 run 带父 run id，**步数不超过父的剩余步数**，父取消立刻传染给子；
- 作用域继承：群聊里发起的委派拿不到私人上下文（否则一句"帮我委派"就绕过了群聊限制）；
- 会话引用保存/恢复：子 run 会把 SessionRef 切到子会话，返回前必须还原，
  否则主回合后续工具（todo 等）会写错会话。
"""
from __future__ import annotations

import threading
import uuid
from typing import Any

from ..memory.store import MemoryStore
from .registry import Risk, Tool, ToolRegistry
from .runctx import RunRef

_MAX_SUB_RESULT_CHARS = 4000

_local = threading.local()


def _depth() -> int:
    return getattr(_local, "delegate_depth", 0)


def register_delegate_tools(
    registry: ToolRegistry,
    store: MemoryStore,
    agent_holder: dict[str, Any],
    run_ref: RunRef | None = None,
) -> None:
    """agent_holder 延迟绑定：组装根在 Agent 构造后填 {"agent": agent}
    （工具注册先于 Agent 构造，与 SessionRef 同属"组装根注入、运行期使用"）。"""

    def delegate_task(args: dict[str, Any]) -> str:
        agent = agent_holder.get("agent")
        if agent is None:
            return "错误：委派目标未就绪（agent 尚未装配）"
        prompt = str(args.get("prompt", "")).strip()
        if not prompt:
            return "错误：prompt 不能为空（说清子任务的目标与验收标准）"
        if _depth():
            return (
                "错误：已处于委派任务中，不支持嵌套委派——"
                "请把子任务拆到主循环顺序执行"
            )
        parent = run_ref.get() if run_ref is not None else None
        if parent is not None and parent.cancelled:
            return "错误：父运行已取消，不再启动新的子任务"
        label = str(args.get("label", "")).strip() or "task"
        # 时分秒会在同秒并发或跨天同一时刻碰撞，导致本应干净的子任务读到旧历史。
        key = f"{label[:60]}-{uuid.uuid4().hex}"
        session_id = store.get_or_create_binding("delegate", key)
        _local.delegate_depth = 1
        try:
            ref = agent.session_ref
            prev = ref.get() if ref is not None else None
            try:
                # 子 run：继承父的步数上限与作用域（群聊里委派也不会拿到私人记忆）
                runner = getattr(agent, "run_turn", None)
                if runner is None:  # 只有 chat 的最小实现（嵌入式/单测）
                    result = agent.chat(prompt, session_id=session_id)
                else:
                    turn = runner(
                        prompt,
                        session_id=session_id,
                        channel=f"{getattr(parent, 'channel', 'cli')}:delegate",
                        principal=(
                            getattr(parent, "principal", "owner") if parent else "owner"
                        ),
                        personal_context=(
                            getattr(parent, "scope", "private") == "private"
                        ),
                        parent=parent,
                    )
                    result = turn.answer
                    if turn.status == "cancelled":
                        return f"子任务被取消（{turn.run_id}）：{turn.answer}"
            finally:
                if ref is not None:
                    ref.set(prev)  # 还原主回合的会话指向
            result = (result or "").strip()
            if len(result) > _MAX_SUB_RESULT_CHARS:
                result = (
                    result[:_MAX_SUB_RESULT_CHARS]
                    + f"…（子任务结果过长已截断；完整现场在会话 delegate:{key}）"
                )
            return f"子任务完成（会话 delegate:{key}）：\n{result}"
        except Exception as exc:  # noqa: BLE001 —— 失败现场留在子会话供排查
            return f"子任务执行失败（现场已保留在会话 delegate:{key}）：{exc}"
        finally:
            _local.delegate_depth = 0

    registry.register(
        Tool(
            name="delegate_task",
            description=(
                "把一个独立子任务委派给干净会话去执行（独立上下文，不污染当前对话；"
                "子任务的写操作仍走审批）。适用：步骤多且与主线无关的调查/整理/"
                "批量操作。不要为小事委派；委派中不能再委派。返回子任务的最终结果"
            ),
            parameters={
                "type": "object",
                "properties": {
                    "prompt": {
                        "type": "string",
                        "description": "子任务指令（自包含：目标、约束、验收标准）",
                    },
                    "label": {
                        "type": "string",
                        "description": "短标签（英文/数字），用于会话命名，如 research",
                    },
                },
                "required": ["prompt"],
            },
            func=delegate_task,
            toolset="core",
            risk=Risk.SAFE,
        )
    )
