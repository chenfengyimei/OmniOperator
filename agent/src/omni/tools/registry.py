"""工具注册表：LLM function calling 的统一入口（Registry 模式）。

职责边界：
- 只负责"注册 / 查找 / 规格生成 / 派发"，不实现任何具体工具
  （具体工具见 builtin.py 与后续 bilibili/ qq/ android/ 等能力域包）；
- 派发前过审批闸门（safety.Approver，策略由外部上下文注入），
  拒绝与执行错误一律回传给 LLM，让它自行换路径——不在循环里抛异常；
- 工具按 toolset 分组（core/bilibili/qq/android…），后续做能力开关时以组为单位。

D09 起有两条入口：
- `execute()` 返回结构化 `ToolResult`（状态/错误码/可重试/副作用是否未知）；
- `dispatch()` 仍返回字符串（`execute().to_message()`），旧调用点与测试零改动。
"""
from __future__ import annotations

import inspect
import json
import threading
from collections.abc import Callable, Iterator
from contextlib import contextmanager
from dataclasses import dataclass
from typing import Any

from ..safety.gates import Approver, DenyApprover, Risk
from .result import ToolResult, as_result


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
    func: Callable[[dict[str, Any]], str | ToolResult]  # 新工具一律返回 ToolResult（D09 收口）
    toolset: str = "core"       # 能力组：core / bilibili / qq / android …
    risk: Risk = Risk.SAFE      # safe / confirm / dangerous
    requires_human: bool = False  # 通用写工具可发布/支付，smart 不得替人批准
    # 批内并发（agent 工具循环）：只有**显式声明只读**的工具才会与同批
    # 其它只读工具并行执行。SAFE 不自动等于 read_only——remember/todo/
    # skill_create 这类"SAFE 但写状态"的工具必须保持默认 False（串行），
    # 否则同批"先写后读"的相对顺序会被并发打乱。声明前先核实：无共享可变
    # 状态、无"必须先于同批后续工具完成"的顺序语义（browser 共用同一活动
    # 页面，故整个 toolset 都不标）。
    read_only: bool = False


class ToolRegistry:
    def __init__(self) -> None:
        self._tools: dict[str, Tool] = {}

    def register(self, tool: Tool) -> None:
        self._tools[tool.name] = tool

    def unregister(self, name: str) -> bool:
        """反注册一个工具（插件禁用/卸载用）；不存在返回 False。"""
        return self._tools.pop(name, None) is not None

    def get(self, name: str) -> Tool | None:
        return self._tools.get(name)

    def describe(self) -> list[dict[str, Any]]:
        """当前注册元数据快照；不调用工具，不导出 callable 或会话/模型配置。"""
        snapshot = list(self._tools.values())
        return [
            {"name": tool.name, "description": tool.description, "toolset": tool.toolset,
             "risk": tool.risk.value, "requires_human": tool.requires_human, "read_only": tool.read_only,
             "parameters": json.loads(json.dumps(tool.parameters, ensure_ascii=False))}
            for tool in sorted(snapshot, key=lambda tool: (tool.toolset, tool.name))
        ]

    def names(self, toolsets: set[str] | None = None) -> list[str]:
        return [t.name for t in self._iter(toolsets)]

    def toolsets(self) -> list[str]:
        return sorted({t.toolset for t in self._tools.values()})

    def specs(
        self, toolsets: set[str] | None = None, *, exclude: frozenset[str] = frozenset()
    ) -> list[dict[str, Any]]:
        """生成 LiteLLM/OpenAI 格式的 tools 参数（可按能力组过滤）。

        exclude 是**当前回合**不可用的工具名（如群聊作用域下的个人记忆工具）：
        既不出现在规格里，也会在 dispatch 处再拦一次——模型自己编出名字也没用。
        """
        return [
            {
                "type": "function",
                "function": {
                    "name": t.name,
                    "description": t.description,
                    "parameters": json.loads(json.dumps(t.parameters, ensure_ascii=False, sort_keys=True)),
                },
            }
            for t in sorted(self._iter(toolsets), key=lambda item: item.name)
            if t.name not in exclude
        ]

    def dispatch(
        self,
        name: str,
        arguments: str | dict[str, Any],
        *,
        approver: Approver | None = None,
        denied_tools: frozenset[str] = frozenset(),
        cancel: Any = None,
    ) -> str:
        """兼容入口：字符串结果（模型与旧调用点看到的就是这句话）。"""
        return self.execute(
            name,
            arguments,
            approver=approver,
            denied_tools=denied_tools,
            cancel=cancel,
        ).to_message()

    def execute(
        self,
        name: str,
        arguments: str | dict[str, Any],
        *,
        approver: Approver | None = None,
        denied_tools: frozenset[str] = frozenset(),
        cancel: Any = None,
        denied_note: Callable[[str], str] | None = None,
    ) -> ToolResult:
        """派发一次工具调用，返回结构化结果（不抛异常：失败也回传给模型）。

        denied_note 是可选的拒绝说明回调（传工具名，返回补充文案或空串）：
        返回非空时替换默认的"受限作用域"文案——调用方用它区分"档位不够
        （提示升档）"与"场景不许可（保留原文案，不误导受限场景去升档）"。
        """
        tool = self._tools.get(name)
        if tool is None:
            return ToolResult.fail(
                f"错误：没有名为 {name} 的工具", error_code="tool_not_found"
            )
        if name in denied_tools:
            note = _denied_note_text(denied_note, name)
            if note:
                return ToolResult.fail(
                    f"错误：当前回合未提供工具 {name}。{note}",
                    error_code="tool_not_available",
                )
            return ToolResult.fail(
                f"错误：当前场景不提供工具 {name}"
                "（受限作用域不读取所有者的个人档案、常驻笔记、跨会话历史与工作区文件；"
                "群聊与他人的私聊均属此类，如需处理请让所有者本人来操作）",
                error_code="tool_not_available",
            )
        if _cancelled(cancel):
            return ToolResult.cancelled(f"工具 {name} 未执行：本次运行已取消")
        if isinstance(arguments, str):
            try:
                arguments = json.loads(arguments or "{}")
            except json.JSONDecodeError as exc:
                return ToolResult.fail(
                    f"错误：工具参数不是合法 JSON（{exc}）", error_code="bad_arguments"
                )
        if not isinstance(arguments, dict):
            return ToolResult.fail("错误：工具参数必须是 JSON 对象", error_code="bad_arguments")
        # 缺省策略与无人值守一致；审批器故障也不能绕过闸门。
        try:
            decision = _approve(approver or DenyApprover(), tool, arguments, cancel)
        except Exception:  # noqa: BLE001
            return ToolResult.denied(
                f"工具 {name} 未获批准：审批失败，已拒绝执行",
                error_code="approval_error",
            )
        if not decision.allowed:
            reason = decision.reason or "审批拒绝"
            return ToolResult.denied(
                f"工具 {name} 未获批准：{reason}。请改用不需要该操作的方式，或向用户说明需要授权。",
                error_code="run_cancelled" if _cancelled(cancel) else "approval_denied",
            )
        try:
            produced = tool.func(arguments or {})
        except Exception as exc:  # noqa: BLE001 —— 回传错误让 LLM 自行调整
            # 工具抛异常时**无法确定副作用是否已发生**：不自动重试、不声称失败无痕
            return ToolResult.fail(
                f"工具 {name} 执行出错: {exc}",
                error_code="tool_exception",
                retryable=False,
                side_effect_state="unknown",
            )
        result = as_result(produced, tool=tool.name)
        if _cancelled(cancel) and result.status == "ok":
            # 工具跑完时运行已被取消：结果照实回填，但状态标记为"取消后完成"
            return ToolResult(
                status=result.status,
                text=result.text,
                error_code="cancelled_after_tool",
                artifacts=result.artifacts,
                verification=result.verification,
                side_effect_state=result.side_effect_state,
                legacy=result.legacy,
                data=dict(result.data, cancelled_after_start=True),
            )
        return result

    def _iter(self, toolsets: set[str] | None):
        for tool in self._tools.values():
            if toolsets is None or tool.toolset in toolsets:
                yield tool


# 键要能区分不同类的 check：`type(func)` 对**所有**绑定方法都是 `method`，
# 用它做键会让"WebApprover 支持 cancel"的错误结论套到每一个审批器上。
_APPROVE_SIGNATURES: dict[tuple[type, str], bool] = {}


def _cancelled(cancel: Any) -> bool:
    """取消信号可能是 CancelToken，也可能是 None（不引入 core 的 import）。"""
    return bool(getattr(cancel, "cancelled", False))


def _denied_note_text(denied_note: Callable[[str], str] | None, name: str) -> str:
    """拒绝说明回调的安全求值：空串/异常都回退默认文案，不影响拒绝语义。"""
    if denied_note is None:
        return ""
    try:
        return str(denied_note(name) or "")
    except Exception:  # noqa: BLE001 —— 提示生成失败不能改变"工具被拒"这个事实
        return ""


def _accepts_cancel(func: Any) -> bool:
    """审批器是否支持 cancel 关键字。

    先看签名再调用，而不是 try/except TypeError——后者会把审批器内部真实的
    TypeError 误判成"不支持 cancel"，然后重跑一次审批（等于问了两次）。
    """
    kind = (type(getattr(func, "__self__", None)), getattr(func, "__name__", ""))
    cached = _APPROVE_SIGNATURES.get(kind)
    if cached is not None:
        return cached
    try:
        params = inspect.signature(func).parameters
        accepts = "cancel" in params or any(
            p.kind is inspect.Parameter.VAR_KEYWORD for p in params.values()
        )
    except (TypeError, ValueError):
        accepts = False
    _APPROVE_SIGNATURES[kind] = accepts
    return accepts


def _approve(approver: Approver, tool: Tool, arguments: dict[str, Any], cancel: Any):
    if _accepts_cancel(approver.check):
        return approver.check(tool, arguments, cancel=cancel)
    return approver.check(tool, arguments)
