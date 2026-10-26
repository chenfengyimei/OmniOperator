"""回合后异步 review：自进化最小闭环（Hermes background_review 的 P1 简化版，
设计出处见 xuexi/NOTES.md §1.3）。

每轮对话结束后，后台线程用（廉价）模型重放最近若干轮，自主决定：
- remember：值得长期记住的事实
- profile_manage(add)：值得写进用户档案的核心特征

纪律：
- 最小权限——只给 memory + profile 两类工具，不给其它能力域；
- review 过程不写入 sessions（不污染用户对话），结果落 reviews 表供观测；
- 消息太少直接跳过；无人值守语义由调用方（P5 心跳）自行决定是否启用。
"""
from __future__ import annotations

import sys
import threading
from typing import Callable

from ..config import AppConfig
from ..llm.gateway import Gateway, to_openai_tool_call
from ..memory.store import MemoryStore
from ..tools.registry import ToolRegistry

_REVIEW_SYSTEM = """\
你是 OmniOperator 的记忆整理模块，在每轮对话结束后于后台静默运行。
任务：审阅最近对话片段，判断有哪些内容值得长期留存。
可用工具：
- remember(text)：记录自包含的重要事实（用户偏好、约定、重要事实、进展）
- profile_manage(operation=add, content=...)：往用户长期档案添加一行
  （只放最核心、稳定的特征；先用 view 查看现有档案，能合并就合并）
- skill_create(name, description, content)：当本轮完成了一个**可复用的
  多步流程**、或踩坑后找到了可行路径时，把它蒸馏成技能。
  content 按 When to Use / Procedure / Pitfalls / Verification 四节组织，
  名字用英文小写连字符；已存在同名技能时不要重复创建
规则：
- 闲聊、临时信息、用户明确说不要记的内容，一律不记
- 技能宁缺毋滥：单次性操作、日常寒暄不值得建技能；只有"以后还会再做、
  且有明确步骤"的事才建
- 写操作总共最多两三次
- 没有值得记录的内容时，直接回复"无需变更"，不要调用任何工具"""


class TurnEndReviewer:
    """需要一个独立的 store 连接工厂（review 跑在后台线程，SQLite 连接不能跨线程）。"""

    def __init__(
        self,
        config: AppConfig,
        gateway: Gateway,
        make_store: Callable[[], MemoryStore],
        make_registry: Callable[[MemoryStore], ToolRegistry],
    ) -> None:
        """make_store / make_registry 都由组装根注入：
        - 每次跑在独立后台线程，需要独立 SQLite 连接；
        - 白名单（只给 memory+profile 工具）是策略，归属组装根而非本模块。
        """
        self._config = config
        self._gateway = gateway
        self._make_store = make_store
        self._make_registry = make_registry
        review_model = config.models.review.strip()
        self._model = review_model if review_model else config.models.chat

    def review_in_background(self, session_id: int) -> None:
        threading.Thread(
            target=self._run_safe, args=(session_id,), daemon=True, name="turn-end-review"
        ).start()

    def _run_safe(self, session_id: int) -> None:
        try:
            self.review_recent(session_id)
        except Exception as exc:  # noqa: BLE001 —— 后台失败只报 stderr，不打断主对话
            print(f"[回合后 review 失败] {exc}", file=sys.stderr, flush=True)

    def review_recent(self, session_id: int) -> str:
        """同步执行一次 review（冒烟/测试直接调这个）；返回结果描述。"""
        store = self._make_store()
        try:
            recent = store.recent_messages(session_id, self._config.review.max_messages)
            if len(recent) < 2:
                return "跳过：对话消息太少"
            transcript = "\n".join(f"[{role}] {content[:300]}" for role, content in recent)
            registry = self._make_registry(store)

            messages = [
                {"role": "system", "content": _REVIEW_SYSTEM},
                {"role": "user", "content": f"最近的对话片段：\n{transcript}"},
            ]
            outcome = ""
            for _ in range(max(self._config.review.steps, 1)):
                reply = self._gateway.complete(
                    messages, model=self._model, tools=registry.specs()
                )
                if not reply.tool_calls:
                    outcome = reply.text.strip() or "无需变更"
                    break
                messages.append(
                    {
                        "role": "assistant",
                        "content": reply.text or "",
                        "tool_calls": [
                            to_openai_tool_call(c) for c in reply.tool_calls
                        ],
                    }
                )
                for call in reply.tool_calls:
                    result = self.registry_result(registry, call)
                    messages.append(
                        {"role": "tool", "tool_call_id": call.id, "content": result}
                    )
            if not outcome:
                outcome = "review 达到步数上限，已终止"
            store.save_review_log(session_id, outcome)
            return outcome
        finally:
            store.close()

    @staticmethod
    def registry_result(registry: ToolRegistry, call) -> str:
        return registry.dispatch(call.function.name, call.function.arguments)
