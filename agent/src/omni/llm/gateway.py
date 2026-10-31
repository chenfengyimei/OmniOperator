"""LiteLLM 统一网关：一套 OpenAI 格式接所有供应商（海外/国产/本地 Ollama）。

模型串格式为 provider/model，例如：
- deepseek/deepseek-chat      （需 DEEPSEEK_API_KEY）
- anthropic/claude-sonnet-4-5 （需 ANTHROPIC_API_KEY）
- ollama/qwen2.5:7b           （本地，无需密钥）

D10 在这一层补上四件事，因为它们**必须在唯一出口**做，否则每个调用点都得记得：

1. **能力校验**：没声明的视觉/工具能力，调用前就拒绝（不发请求、不花钱、不猜）。
2. **错误分类与有界重试**：只对暂时性故障退避重试；认证/参数错误立即失败；
   重试永远打同一个 model，不偷偷换供应商。
3. **超时与脱敏**：结构化 timeout；异常文本先脱敏再进入日志/界面。
4. **费用预留与结算**：调用前预留、调用后结算，未知计价不当成零。
"""
from __future__ import annotations

import datetime as dt
import sys
import time
from collections.abc import Callable, Iterable, Iterator
from dataclasses import dataclass, field
from typing import Any

from ..config import LLM as LLMConfig
from ..config import Models
from . import errors
from .budget import (
    SOURCE_NOT_BILLED,
    SOURCE_RESERVED,
    SOURCE_UNKNOWN,
    Ledger,
    NullLedger,
    Quote,
    Reservation,
    RunBudget,
    settle_amount,
)
from .errors import GatewayError
from .usage import (
    MICRO,
    Usage,
    estimate_tokens,
    micro_from_usd,
    resolve_price,
    uses_vision,
)

# 兼容旧调用点与测试：这两个名字以前就住在这里
__all__ = ["Gateway", "GatewayError", "Reply", "StreamEvent", "to_openai_tool_call"]


def to_openai_tool_call(call: Any) -> dict[str, Any]:
    """LiteLLM ToolCall → OpenAI assistant 消息里的 tool_calls 条目。"""
    return {
        "id": call.id,
        "type": "function",
        "function": {
            "name": call.function.name,
            "arguments": call.function.arguments,
        },
    }


@dataclass
class Reply:
    """归一化的模型回复（含用量与费用，便于账本与界面解释）。"""

    text: str = ""
    tool_calls: list[Any] = field(default_factory=list)  # LiteLLM ToolCall 对象
    usage: Usage = field(default_factory=Usage)
    cost_micro: int | None = None
    cost_source: str = SOURCE_UNKNOWN
    attempts: int = 1
    incomplete: bool = False
    error_code: str = ""

    def brief(self) -> dict[str, Any]:
        return {
            "text_chars": len(self.text or ""),
            "tool_calls": [getattr(c.function, "name", "") for c in self.tool_calls],
            "usage": self.usage.brief(),
            "cost_micro": self.cost_micro,
            "cost_source": self.cost_source,
            "attempts": self.attempts,
            "incomplete": self.incomplete,
        }


@dataclass(frozen=True)
class StreamEvent:
    """流式事件：通道只消费这些，不直接摸 LiteLLM 的 chunk。"""

    type: str  # delta | tool_call_done | usage | done | interrupted
    text: str = ""
    data: dict[str, Any] = field(default_factory=dict)

    def brief(self) -> dict[str, Any]:
        return {"type": self.type, "text": self.text, **self.data}


def _today() -> str:
    return dt.date.today().isoformat()


class _CallRecord:
    """一次调用的记账上下文（失败路径也要能落账）。"""

    def __init__(self, *, model: str, purpose: str, reservation: Reservation | None) -> None:
        self.model = model
        self.purpose = purpose
        self.reservation = reservation
        self.started_at = dt.datetime.now().astimezone().isoformat(timespec="seconds")
        self.t0 = time.monotonic()

    @property
    def duration_ms(self) -> int:
        return int((time.monotonic() - self.t0) * 1000)


class Gateway:
    def __init__(
        self,
        models: Models,
        *,
        llm: LLMConfig | None = None,
        ledger: Ledger | None = None,
        sleep: Callable[[float], None] = time.sleep,
        clock: Callable[[], float] = time.monotonic,
    ) -> None:
        self.models = models
        self.llm = llm or LLMConfig()
        self.ledger: Ledger = ledger or NullLedger()
        self._sleep = sleep
        self._clock = clock
        self._warned = False  # 账本写失败只提醒一次，避免刷屏

    def resolve(self, route: str) -> str:
        """路由名（chat/vision/local）→ 配置的模型串。"""
        try:
            return getattr(self.models, route)
        except AttributeError as exc:
            raise GatewayError(
                code=errors.BAD_REQUEST,
                message=f"未知模型路由: {route}",
            ) from exc

    # ---------------------------------------------------------------- 能力校验

    def capability(self, model: str):
        return self.llm.capability(model)

    def _precheck(
        self,
        model: str,
        messages: list[dict[str, Any]],
        tools: list[dict[str, Any]] | None,
        cancel: Any,
    ) -> None:
        if cancel is not None and getattr(cancel, "cancelled", False):
            raise errors.cancel_error(model)
        cap = self.capability(model)
        if tools and not cap.tools:
            raise errors.unsupported(
                f"模型 {model} 未声明工具调用能力（llm.capabilities.{model}.tools=false），"
                "已拒绝本次请求：免得模型假装调用了工具",
                model=model,
            )
        if uses_vision(messages) and not cap.vision:
            raise errors.unsupported(
                f"模型 {model} 未声明视觉能力（llm.capabilities.{model}.vision），"
                "含图片的请求已拒绝，未发出网络调用",
                model=model,
            )
        if cap.context_tokens and estimate_tokens(messages) > cap.context_tokens:
            raise GatewayError(
                code=errors.CONTEXT_LENGTH,
                message=(
                    f"输入约 {estimate_tokens(messages)} tokens，超过 {model} 声明的 "
                    f"{cap.context_tokens}：请调小 limits.max_history_messages 或先压缩"
                ),
                model=model,
            )

    # ---------------------------------------------------------------- 预算

    def _reserve(
        self,
        *,
        model: str,
        messages: list[dict[str, Any]],
        run_budget: RunBudget | None,
    ) -> Reservation:
        price = resolve_price(self.llm, model)
        prompt_tokens = estimate_tokens(messages)
        completion_tokens = max(1, int(self.llm.max_output_tokens))
        micro = micro_from_usd(price.cost_usd(prompt_tokens, completion_tokens))
        quote = Quote(
            micro=micro,
            prompt_tokens=prompt_tokens,
            completion_tokens=completion_tokens,
            price=price,
        )
        daily_limit = int(round(self.llm.budget.daily_usd * MICRO))
        if micro is None and (daily_limit > 0 or (run_budget and run_budget.limit_micro)):
            # 有上限却算不出这笔要多少钱 → 没法守约，拒绝比"记成零"诚实
            raise GatewayError(
                code=errors.PRICE_UNKNOWN,
                message=(
                    f"模型 {model} 的单价未知（来源 {price.source}），无法计入费用上限："
                    "请在 config 的 llm.capabilities 里声明 input_usd_per_m/"
                    "output_usd_per_m，或把该模型标为本地免费"
                ),
                model=model,
            )
        if micro is None:
            return Reservation(day=_today(), micro=0, quote=quote)

        if run_budget is not None and not run_budget.try_reserve(micro):
            raise errors.budget_error(
                f"本次运行的费用上限（${run_budget.limit_micro / MICRO:.4f}）已到："
                f"已用 ${run_budget.spent_micro / MICRO:.4f}，本次需要预留 ${micro / MICRO:.6f}",
                model=model,
            )
        held_by_run = run_budget is not None
        if daily_limit > 0:
            try:
                ok, used = self.ledger.reserve(day=_today(), micro=micro, limit_micro=daily_limit)
            except Exception as exc:  # noqa: BLE001 —— 账本不可用就不敢花钱
                if held_by_run:
                    run_budget.release(micro)
                raise errors.budget_error(
                    f"费用账本不可用（{exc}）：为避免超支，本次未发出模型请求",
                    model=model,
                ) from exc
            if not ok:
                if held_by_run:
                    run_budget.release(micro)
                raise errors.budget_error(
                    f"今日模型费用已达上限 ${daily_limit / MICRO:.2f}"
                    f"（已用 ${used / MICRO:.4f}）：本次未发出请求",
                    model=model,
                )
            return Reservation(
                day=_today(), micro=micro, quote=quote, ledger_held=True, run_held=held_by_run
            )
        return Reservation(day=_today(), micro=micro, quote=quote, run_held=held_by_run)

    def _settle(
        self,
        record: _CallRecord,
        *,
        usage: Usage,
        cost_micro: int | None,
        cost_source: str,
        outcome: str,
        error_code: str = "",
        attempts: int = 1,
        run_budget: RunBudget | None = None,
        run_id: str | None = None,
        session_id: int | None = None,
        detail: str = "",
    ) -> None:
        reservation = record.reservation
        charged = cost_micro
        if reservation is not None and reservation.ledger_held:
            try:
                self.ledger.settle(
                    day=reservation.day,
                    reserved_micro=reservation.micro,
                    charged_micro=max(0, charged or 0),
                )
            except Exception as exc:  # noqa: BLE001 —— 结算失败不能否定已完成的回答
                self._warn_once(f"[额度] 结算失败：{exc}")
        if run_budget is not None and reservation is not None and reservation.run_held:
            run_budget.settle(reservation.micro, charged)
        try:
            self.ledger.record(
                run_id=run_id,
                session_id=session_id,
                purpose=record.purpose,
                model=record.model,
                prompt_tokens=usage.prompt_tokens,
                completion_tokens=usage.completion_tokens,
                cost_micro=cost_micro,
                cost_source=cost_source,
                outcome=outcome,
                error_code=error_code,
                attempts=attempts,
                duration_ms=record.duration_ms,
                started_at=record.started_at,
                detail=detail[:300],
            )
        except Exception as exc:  # noqa: BLE001 —— 观测失败不影响对话
            self._warn_once(f"[额度] 调用流水未落库：{exc}")

    def _warn_once(self, text: str) -> None:
        if self._warned:
            return
        self._warned = True
        print(text, file=sys.stderr, flush=True)

    # ---------------------------------------------------------------- 调用

    def _call(
        self,
        model: str,
        messages: list[dict[str, Any]],
        tools: list[dict[str, Any]] | None,
        cancel: Any,
    ) -> tuple[Any, int]:
        """带超时与有界退避的同步调用；返回 (响应, 实际尝试次数)。"""
        retry = self.llm.retry
        deadline = self._clock() + retry.total_deadline_s
        delay = retry.backoff_s
        attempts = 0
        while True:
            if cancel is not None and getattr(cancel, "cancelled", False):
                raise errors.cancel_error(model)
            attempts += 1
            try:
                response = _completion(
                    model=model,
                    messages=messages,
                    tools=tools,
                    timeout=self.llm.request_timeout_s,
                    num_retries=0,  # 重试只由本模块负责，不让 LiteLLM 再乘一次
                )
                return response, attempts
            except Exception as exc:  # noqa: BLE001 —— 统一翻译后再决定是否重试
                error = errors.classify(exc, model=model, attempts=attempts)
                if not error.retryable or attempts >= retry.max_attempts:
                    raise error from exc
                if self._clock() + delay >= deadline:
                    raise error from exc
                self._sleep(delay)
                delay = min(delay * 2, 8.0)

    def complete(
        self,
        messages: list[dict[str, Any]],
        *,
        model: str | None = None,
        tools: list[dict[str, Any]] | None = None,
        purpose: str = "chat",
        run_id: str | None = None,
        session_id: int | None = None,
        run_budget: RunBudget | None = None,
        cancel: Any = None,
    ) -> Reply:
        model = model or self.resolve("chat")
        self._precheck(model, messages, tools, cancel)
        reservation = self._reserve(
            model=model, messages=messages, run_budget=run_budget
        )
        record = _CallRecord(model=model, purpose=purpose, reservation=reservation)
        try:
            response, attempts = self._call(model, messages, tools, cancel)
        except GatewayError as error:
            # 限流/认证/参数错误：供应商明确没处理，不保留预留
            charged = reservation.micro if error.maybe_billed else 0
            source = SOURCE_RESERVED if error.maybe_billed and charged else SOURCE_NOT_BILLED
            self._settle(
                record,
                usage=Usage(),
                cost_micro=charged,
                cost_source=source,
                outcome="error",
                error_code=error.code,
                attempts=error.attempts,
                run_budget=run_budget,
                run_id=run_id,
                session_id=session_id,
                detail=error.message,
            )
            raise
        try:
            message = response.choices[0].message
        except (AttributeError, IndexError, TypeError) as exc:
            self._settle(
                record,
                usage=Usage(),
                cost_micro=0,
                cost_source=SOURCE_NOT_BILLED,
                outcome="error",
                error_code=errors.UNKNOWN,
                attempts=attempts,
                run_budget=run_budget,
                run_id=run_id,
                session_id=session_id,
                detail=f"响应格式异常：{exc}",
            )
            raise GatewayError(
                code=errors.UNKNOWN,
                message=f"模型 {model} 返回了无法解析的响应：{errors.sanitize(str(exc))}",
                model=model,
            ) from exc

        usage = Usage.from_response(response)
        cost_micro, cost_source = settle_amount(
            usage, reservation.quote.price, reservation.micro
        )
        self._settle(
            record,
            usage=usage,
            cost_micro=cost_micro,
            cost_source=cost_source,
            outcome="ok",
            attempts=attempts,
            run_budget=run_budget,
            run_id=run_id,
            session_id=session_id,
        )
        return Reply(
            text=message.content or "",
            tool_calls=list(getattr(message, "tool_calls", None) or []),
            usage=usage,
            cost_micro=cost_micro,
            cost_source=cost_source,
            attempts=attempts,
        )

    # ---------------------------------------------------------------- 流式

    def stream(
        self,
        messages: list[dict[str, Any]],
        *,
        model: str | None = None,
        tools: list[dict[str, Any]] | None = None,
        purpose: str = "chat",
        run_id: str | None = None,
        session_id: int | None = None,
        run_budget: RunBudget | None = None,
        cancel: Any = None,
        on_event: Callable[[StreamEvent], None] | None = None,
    ) -> Reply:
        """流式调用：边生成边交付增量，最后返回与 `complete` 同形状的 Reply。

        半途断连的处理：**保留已收到的文本**，标记 `incomplete=True`，
        并按预留额保守记账（供应商可能已经计费，我们拿不到 usage）。
        """
        model = model or self.resolve("chat")
        cap = self.capability(model)
        if not cap.stream:
            # 没声明流式能力就走同步路径，语义一致（不是"假装流式"）
            return self.complete(
                messages,
                model=model,
                tools=tools,
                purpose=purpose,
                run_id=run_id,
                session_id=session_id,
                run_budget=run_budget,
                cancel=cancel,
            )
        self._precheck(model, messages, tools, cancel)
        reservation = self._reserve(
            model=model, messages=messages, run_budget=run_budget
        )
        record = _CallRecord(model=model, purpose=purpose, reservation=reservation)
        text_parts: list[str] = []
        calls: dict[int, dict[str, Any]] = {}
        usage = Usage()
        interrupted: GatewayError | None = None
        attempts = 1

        def emit(event: StreamEvent) -> None:
            if on_event is not None:
                on_event(event)

        try:
            chunks, attempts = self._stream_call(model, messages, tools, cancel)
            for chunk in chunks:
                if cancel is not None and getattr(cancel, "cancelled", False):
                    _close(chunks)
                    raise errors.cancel_error(model)
                delta = _delta_of(chunk)
                if delta is not None:
                    text = getattr(delta, "content", None)
                    if text:
                        text_parts.append(text)
                        emit(StreamEvent("delta", text=text))
                    _accumulate_calls(calls, getattr(delta, "tool_calls", None))
                chunk_usage = Usage.from_response(chunk)
                if chunk_usage.reported:
                    usage = chunk_usage
            emit(StreamEvent("usage", data=usage.brief()))
        except GatewayError as error:
            if error.code == errors.CANCELLED:
                raise
            if not text_parts and not calls:
                charged = reservation.micro if error.maybe_billed else 0
                self._settle(
                    record,
                    usage=Usage(),
                    cost_micro=charged,
                    cost_source=SOURCE_RESERVED if charged else SOURCE_NOT_BILLED,
                    outcome="error",
                    error_code=error.code,
                    attempts=error.attempts,
                    run_budget=run_budget,
                    run_id=run_id,
                    session_id=session_id,
                    detail=error.message,
                )
                raise
            interrupted = error
        except Exception as exc:  # noqa: BLE001 —— 已收到内容时不让异常吞掉它
            interrupted = errors.classify(exc, model=model, attempts=attempts)
            if not text_parts and not calls:
                self._settle(
                    record,
                    usage=Usage(),
                    cost_micro=reservation.micro,
                    cost_source=SOURCE_RESERVED,
                    outcome="error",
                    error_code=interrupted.code,
                    attempts=attempts,
                    run_budget=run_budget,
                    run_id=run_id,
                    session_id=session_id,
                    detail=interrupted.message,
                )
                raise interrupted from exc

        cost_micro, cost_source = settle_amount(
            usage, reservation.quote.price, reservation.micro
        )
        self._settle(
            record,
            usage=usage,
            cost_micro=cost_micro,
            cost_source=cost_source,
            outcome="interrupted" if interrupted else "ok",
            error_code=interrupted.code if interrupted else "",
            attempts=attempts,
            run_budget=run_budget,
            run_id=run_id,
            session_id=session_id,
            detail=interrupted.message if interrupted else "",
        )
        reply = Reply(
            text="".join(text_parts),
            tool_calls=_finalize_calls(calls),
            usage=usage,
            cost_micro=cost_micro,
            cost_source=cost_source,
            attempts=attempts,
            incomplete=interrupted is not None,
            error_code=interrupted.code if interrupted else "",
        )
        if interrupted is not None:
            emit(
                StreamEvent(
                    "interrupted",
                    data={"code": interrupted.code, "message": interrupted.message},
                )
            )
        emit(StreamEvent("done", data=reply.brief()))
        return reply

    def _stream_call(
        self,
        model: str,
        messages: list[dict[str, Any]],
        tools: list[dict[str, Any]] | None,
        cancel: Any,
    ) -> tuple[Iterator[Any], int]:
        """打开流（只有"建立连接"阶段重试；已经吐字之后再重试会重复内容）。"""
        retry = self.llm.retry
        deadline = self._clock() + retry.total_deadline_s
        delay = retry.backoff_s
        attempts = 0
        while True:
            if cancel is not None and getattr(cancel, "cancelled", False):
                raise errors.cancel_error(model)
            attempts += 1
            try:
                stream = _completion(
                    model=model,
                    messages=messages,
                    tools=tools,
                    timeout=self.llm.request_timeout_s,
                    num_retries=0,
                    stream=True,
                )
                return iter(stream), attempts
            except Exception as exc:  # noqa: BLE001
                error = errors.classify(exc, model=model, attempts=attempts)
                if not error.retryable or attempts >= retry.max_attempts:
                    raise error from exc
                if self._clock() + delay >= deadline:
                    raise error from exc
                self._sleep(delay)
                delay = min(delay * 2, 8.0)


# ---------------------------------------------------------------- LiteLLM 适配


def _completion(**kwargs: Any) -> Any:
    """唯一接触 LiteLLM 的地方（测试替换这一个函数即可覆盖全部调用路径）。

    `num_retries=0`：LiteLLM 自身也会重试，叠在我们的有界重试上会变成 3×3 次，
    账单和等待时间都不可解释。重试只由本模块负责。
    """
    import litellm

    kwargs.setdefault("num_retries", 0)
    return litellm.completion(**kwargs)


def _delta_of(chunk: Any) -> Any:
    try:
        return chunk.choices[0].delta
    except (AttributeError, IndexError, TypeError):
        return None


def _accumulate_calls(calls: dict[int, dict[str, Any]], deltas: Iterable[Any] | None) -> None:
    """把流式分片的 tool_calls 按 index 拼起来（arguments 是逐字到达的）。"""
    for position, delta in enumerate(deltas or []):
        index = getattr(delta, "index", None)
        if not isinstance(index, int):
            index = position
        slot = calls.setdefault(
            index, {"id": "", "type": "function", "name": "", "arguments": ""}
        )
        if getattr(delta, "id", None):
            slot["id"] = delta.id
        function = getattr(delta, "function", None)
        if function is not None:
            if getattr(function, "name", None):
                slot["name"] = (slot["name"] or "") + function.name
            if getattr(function, "arguments", None):
                slot["arguments"] = (slot["arguments"] or "") + function.arguments


@dataclass
class _CallFunction:
    name: str
    arguments: str


@dataclass
class _Call:
    """把拼好的分片包装成与 LiteLLM ToolCall 相同的形状。"""

    id: str
    function: _CallFunction


def _finalize_calls(calls: dict[int, dict[str, Any]]) -> list[Any]:
    out: list[Any] = []
    for index in sorted(calls):
        slot = calls[index]
        name = slot.get("name") or ""
        if not name:
            continue  # 只有残缺分片、没有函数名：不算一次可以执行的调用
        out.append(
            _Call(
                id=slot.get("id") or f"call_{index}",
                function=_CallFunction(name=name, arguments=slot.get("arguments") or "{}"),
            )
        )
    return out


def _close(chunks: Any) -> None:
    close = getattr(chunks, "close", None)
    if callable(close):
        try:
            close()
        except Exception:  # noqa: BLE001, S110
            pass
