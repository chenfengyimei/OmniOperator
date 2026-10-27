"""工具结果的结构化契约（D09）。

以前工具只能返回一个字符串，于是三种完全不同的情况长得一模一样：

| 实际发生了什么 | 旧字符串 | 后果 |
| --- | --- | --- |
| 审批被拒 | `工具 X 未获批准：…` | 界面只能靠中文前缀猜 |
| 工具自己报错 | `错误：…` | 分不清是参数错还是环境错 |
| 执行到一半超时 | `…超时…` | 没人知道副作用有没有发生 |

`ToolResult` 把这些拆成字段：`status`（ok/error/denied/cancelled/unknown）、
`error_code`（可定位的稳定标识）、`retryable`、`side_effect_state`（none/unknown/applied）、
`artifacts`、`verification`。

兼容：`text` 保留给模型看的那句话（与旧输出逐字一致），`Registry.dispatch()` 仍返回字符串。
新代码用 `execute()` 拿结构化结果，旧调用点不用改。
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

OK = "ok"
ERROR = "error"
DENIED = "denied"
CANCELLED = "cancelled"
UNKNOWN = "unknown"

SIDE_EFFECT_NONE = "none"        # 确定没有产生外部影响（校验失败、只读工具）
SIDE_EFFECT_UNKNOWN = "unknown"  # 不确定：超时/断连/进程崩溃，需要人工查证
SIDE_EFFECT_APPLIED = "applied"  # 工具明确报告已完成（例如已发送）


@dataclass(frozen=True)
class ToolResult:
    status: str
    text: str
    error_code: str = ""
    retryable: bool = False
    side_effect_state: str = SIDE_EFFECT_NONE
    artifacts: tuple[str, ...] = ()
    verification: str = ""
    # 旧字符串适配标记：text 来自"只能返回字符串"的工具，status 是按前缀推断的
    legacy: bool = False
    data: dict[str, Any] = field(default_factory=dict)

    def to_message(self) -> str:
        """回填给模型的内容：与旧输出一致，模型不需要学会读 JSON。"""
        return self.text

    def brief(self) -> dict[str, Any]:
        """写进事件流的最小信息（不含长文本）。"""
        return {
            "status": self.status,
            "error_code": self.error_code,
            "retryable": self.retryable,
            "side_effect_state": self.side_effect_state,
            "artifacts": list(self.artifacts),
            "verification": self.verification,
            "legacy": self.legacy,
            "chars": len(self.text),
        }

    @classmethod
    def ok(cls, text: str, **kwargs: Any) -> ToolResult:
        return cls(status=OK, text=text, **kwargs)

    @classmethod
    def fail(
        cls,
        text: str,
        *,
        error_code: str = "tool_failed",
        retryable: bool = False,
        side_effect_state: str = SIDE_EFFECT_NONE,
        **kwargs: Any,
    ) -> ToolResult:
        return cls(
            status=ERROR,
            text=text,
            error_code=error_code,
            retryable=retryable,
            side_effect_state=side_effect_state,
            **kwargs,
        )

    @classmethod
    def denied(cls, text: str, *, error_code: str = "approval_denied") -> ToolResult:
        return cls(status=DENIED, text=text, error_code=error_code)

    @classmethod
    def cancelled(cls, text: str) -> ToolResult:
        return cls(
            status=CANCELLED, text=text, error_code="run_cancelled", side_effect_state=SIDE_EFFECT_NONE
        )

    @classmethod
    def from_legacy(cls, text: str, *, tool: str = "") -> ToolResult:
        """把"只会返回字符串"的旧工具结果转成结构化结果。

        只按中文前缀推断（`错误：` / `未获批准` / `超时`），并在 `legacy=True` 上留痕——
        推断出来的状态不能拿来当"副作用已确认"的证据，只能用来分类展示。
        """
        raw = text if isinstance(text, str) else str(text)
        lowered = raw[:200]
        if "未获批准" in lowered:
            return cls(status=DENIED, text=raw, error_code="approval_denied", legacy=True)
        if "已取消" in lowered:
            return cls(status=CANCELLED, text=raw, error_code="run_cancelled", legacy=True)
        if lowered.lstrip().startswith("错误") or "执行出错" in lowered:
            side = SIDE_EFFECT_UNKNOWN if "超时" in lowered or "未知" in lowered else SIDE_EFFECT_NONE
            return cls(
                status=ERROR,
                text=raw,
                error_code="legacy_error",
                retryable="超时" in lowered,
                side_effect_state=side,
                legacy=True,
            )
        return cls(status=OK, text=raw, legacy=True)


def as_result(value: Any, *, tool: str = "") -> ToolResult:
    """工具函数返回值 → ToolResult（字符串按旧契约适配，已结构化的原样返回）。"""
    if isinstance(value, ToolResult):
        return value
    return ToolResult.from_legacy(value, tool=tool)
