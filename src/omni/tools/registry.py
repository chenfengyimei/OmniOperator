"""工具注册表：LLM function calling 的统一入口（Registry 模式）。

职责边界：
- 只负责"注册 / 查找 / 规格生成 / 派发"，不实现任何具体工具
  （具体工具见 builtin.py 与后续 bilibili/ qq/ android/ 等能力域包）；
- 派发前过审批闸门（safety.Approver，策略由外部上下文注入），
  拒绝与执行错误一律以字符串回传给 LLM，让它自行换路径——不在循环里抛异常；
- 工具按 toolset 分组（core/bilibili/qq/android…），后续做能力开关时以组为单位。
"""
from __future__ import annotations

import json
import threading
from dataclasses import dataclass
from contextlib import contextmanager
from collections.abc import Iterator
from typing import Any, Callable

from ..safety.gates import Approver, DenyApprover, Risk


class SessionRef:
    """跨层共享的"当前会话"引用（thread-local，多通道并发各看各的）。

    bootstrap 把同一个实例注入 Agent（chat 开始时 set）与会话级工具
    （todo 等，派发时 get）。放 tools 层：core 可以依赖 tools，反向不行。
    """

    def __init__(self) -> None:
        self._local = threading.local()

    def set(self, session_id: int | None) -> None:
        self._local.sid = session_id

    def get(self) -> int | None:
        return getattr(self._local, "sid", None)

    @contextmanager
    def bind(self, session_id: int) -> Iterator[None]:
        """回合内绑定；正常返回、异常、嵌套子回合均恢复调用者的上下文。"""
        previous = self.get()
        self.set(session_id)
        try:
            yield
        finally:
            self.set(previous)


@dataclass
class Tool:
    name: str
    description: str
    parameters: dict[str, Any]  # JSON Schema
    func: Callable[[dict[str, Any]], str]
    toolset: str = "core"       # 能力组：core / bilibili / qq / android …
    risk: Risk = Risk.SAFE      # safe / confirm / dangerous
    requires_human: bool = False  # 通用写工具可发布/支付，smart 不得替人批准


class ToolRegistry:
    def __init__(self) -> None:
        self._tools: dict[str, Tool] = {}

    def register(self, tool: Tool) -> None:
        self._tools[tool.name] = tool

    def get(self, name: str) -> Tool | None:
        return self._tools.get(name)

    def names(self, toolsets: set[str] | None = None) -> list[str]:
        return [t.name for t in self._iter(toolsets)]

    def toolsets(self) -> list[str]:
        return sorted({t.toolset for t in self._tools.values()})

    def specs(self, toolsets: set[str] | None = None) -> list[dict[str, Any]]:
        """生成 LiteLLM/OpenAI 格式的 tools 参数（可按能力组过滤）。"""
        return [
            {
                "type": "function",
                "function": {
                    "name": t.name,
                    "description": t.description,
                    "parameters": t.parameters,
                },
            }
            for t in self._iter(toolsets)
        ]

    def dispatch(
        self,
        name: str,
        arguments: str | dict[str, Any],
        *,
        approver: Approver | None = None,
    ) -> str:
        tool = self._tools.get(name)
        if tool is None:
            return f"错误：没有名为 {name} 的工具"
        if isinstance(arguments, str):
            try:
                arguments = json.loads(arguments or "{}")
            except json.JSONDecodeError as exc:
                return f"错误：工具参数不是合法 JSON（{exc}）"
        if not isinstance(arguments, dict):
            return "错误：工具参数必须是 JSON 对象"
        # 缺省策略与无人值守一致；审批器故障也不能绕过闸门。
        try:
            decision = (approver or DenyApprover()).check(tool, arguments)
        except Exception:  # noqa: BLE001
            return f"工具 {name} 未获批准：审批失败，已拒绝执行"
        if not decision.allowed:
            reason = decision.reason or "审批拒绝"
            return f"工具 {name} 未获批准：{reason}。请改用不需要该操作的方式，或向用户说明需要授权。"
        try:
            return tool.func(arguments or {})
        except Exception as exc:  # noqa: BLE001 —— 回传错误让 LLM 自行调整
            return f"工具 {name} 执行出错: {exc}"

    def _iter(self, toolsets: set[str] | None):
        for tool in self._tools.values():
            if toolsets is None or tool.toolset in toolsets:
                yield tool
