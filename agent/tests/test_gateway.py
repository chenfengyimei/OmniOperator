"""网关的确定性行为：错误分类、有界重试、超时、能力校验与脱敏（D10）。

这些用例**全部离线**：`_completion` 是唯一接触 LiteLLM 的函数，这里替换掉它，
不联网、不读真实密钥、不产生费用。断言的重点不是"能调通"，而是：

- 不该重试的错误一次都不多重试；
- 该拒绝的请求在发出去之前就拒绝（调用次数为零）；
- 密钥不会因为报错而出现在文本里。
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

import pytest

from omni.config import LLM, LLMBudget, Models, ModelCapability, Retry
from omni.llm import gateway as gw
from omni.llm.errors import (
    AUTH,
    BAD_REQUEST,
    CANCELLED,
    CONTEXT_LENGTH,
    RATE_LIMIT,
    TIMEOUT,
    UNSUPPORTED,
    GatewayError,
    classify,
    sanitize,
)
from omni.llm.usage import estimate_tokens, resolve_price, uses_vision

# ---------------------------------------------------------------- 假响应/假异常


@dataclass
class _Msg:
    content: str = ""
    tool_calls: list[Any] | None = None


@dataclass
class _Choice:
    message: _Msg = field(default_factory=_Msg)
    delta: Any = None


@dataclass
class _Usage:
    prompt_tokens: int
    completion_tokens: int


@dataclass
class _Resp:
    choices: list[_Choice]
    usage: Any = None


def _resp(text: str = "OK", *, prompt: int | None = 10, completion: int | None = 5) -> _Resp:
    usage = _Usage(prompt, completion) if prompt is not None else None
    return _Resp(choices=[_Choice(message=_Msg(content=text))], usage=usage)


def _exc(name: str, text: str = "boom", status: int | None = None) -> Exception:
    cls = type(name, (Exception,), {"__module__": "litellm"})
    error = cls(text)
    if status is not None:
        error.status_code = status  # type: ignore[attr-defined]
    return error


class _Script:
    """按脚本依次抛出/返回；记录每次调用的参数。"""

    def __init__(self, *outcomes: Any) -> None:
        self.outcomes = list(outcomes)
        self.calls: list[dict[str, Any]] = []

    def __call__(self, **kwargs: Any) -> Any:
        self.calls.append(kwargs)
        outcome = self.outcomes.pop(0)
        if isinstance(outcome, BaseException):
            raise outcome
        return outcome


@pytest.fixture()
def no_sleep():
    return []


def _gateway(
    script: _Script,
    monkeypatch: pytest.MonkeyPatch,
    *,
    llm: LLM | None = None,
    sleeps: list[float] | None = None,
    clock: list[float] | None = None,
) -> gw.Gateway:
    monkeypatch.setattr(gw, "_completion", script)

    def _sleep(seconds: float) -> None:
        if sleeps is not None:
            sleeps.append(seconds)
        if clock is not None:
            clock[0] += seconds  # 虚拟时钟随时间前进（不然总时限永远到不了）

    return gw.Gateway(
        Models(chat="deepseek/deepseek-chat"),
        llm=llm or LLM(),
        sleep=_sleep,
        clock=(lambda: clock[0]) if clock is not None else (lambda: 0.0),
    )


# ---------------------------------------------------------------- 分类


@pytest.mark.parametrize(
    ("name", "code", "retryable"),
    [
        ("RateLimitError", RATE_LIMIT, True),
        ("Timeout", TIMEOUT, True),
        ("APIConnectionError", "connection", True),
        ("InternalServerError", "server", True),
        ("AuthenticationError", AUTH, False),
        ("BadRequestError", BAD_REQUEST, False),
        ("ContextWindowExceededError", CONTEXT_LENGTH, False),
    ],
)
def test_classify_maps_by_exception_name(name: str, code: str, retryable: bool) -> None:
    error = classify(_exc(name), model="x/y", attempts=2)
    assert (error.code, error.retryable, error.attempts) == (code, retryable, 2)
    assert error.model == "x/y" and error.hint


def test_classify_falls_back_to_status_code() -> None:
    assert classify(_exc("WeirdError", status=429)).code == RATE_LIMIT
    assert classify(_exc("WeirdError", status=503)).retryable is True
    assert classify(_exc("WeirdError", status=401)).code == AUTH
    assert classify(TimeoutError("slow")).code == TIMEOUT


def test_builtin_errors_survive_reclassification() -> None:
    original = GatewayError(code=CANCELLED, message="已取消")
    assert classify(original) is original


# ---------------------------------------------------------------- 重试


def test_retry_backs_off_and_then_succeeds(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    sleeps: list[float] = []
    script = _Script(_exc("RateLimitError"), _exc("RateLimitError"), _resp("好"))
    gateway = _gateway(script, monkeypatch, sleeps=sleeps)
    reply = gateway.complete([{"role": "user", "content": "hi"}])
    assert reply.text == "好"
    assert reply.attempts == 3
    assert sleeps == [0.5, 1.0]  # 指数退避
    assert len(script.calls) == 3


def test_auth_failure_is_never_retried(monkeypatch: pytest.MonkeyPatch) -> None:
    script = _Script(_exc("AuthenticationError", "invalid key"))
    gateway = _gateway(script, monkeypatch)
    with pytest.raises(GatewayError) as caught:
        gateway.complete([{"role": "user", "content": "hi"}])
    assert caught.value.code == AUTH and caught.value.retryable is False
    assert len(script.calls) == 1  # 只打一次，不浪费配额


def test_retry_stops_at_max_attempts(monkeypatch: pytest.MonkeyPatch) -> None:
    script = _Script(*[_exc("RateLimitError")] * 5)
    gateway = _gateway(script, monkeypatch, llm=LLM(retry=Retry(max_attempts=3, backoff_s=0)))
    with pytest.raises(GatewayError) as caught:
        gateway.complete([{"role": "user", "content": "hi"}])
    assert caught.value.attempts == 3
    assert len(script.calls) == 3


def test_retry_respects_total_deadline(monkeypatch: pytest.MonkeyPatch) -> None:
    """总时限到点就放弃：不因为"还能退避"就无限等下去。"""
    clock = [0.0]
    sleeps: list[float] = []
    script = _Script(*[_exc("RateLimitError")] * 5)
    gateway = _gateway(
        script,
        monkeypatch,
        llm=LLM(retry=Retry(max_attempts=5, total_deadline_s=1.0, backoff_s=0.8)),
        sleeps=sleeps,
        clock=clock,
    )
    with pytest.raises(GatewayError):
        gateway.complete([{"role": "user", "content": "hi"}])
    # 第一次退避 0.8s（还剩 0.2s），第二次要 1.6s 就越界 → 只打两次、只睡一次
    assert len(script.calls) == 2
    assert sleeps == [0.8]


def test_timeout_is_structured_and_internal_retries_are_off(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    script = _Script(_resp())
    gateway = _gateway(script, monkeypatch, llm=LLM(request_timeout_s=12.5))
    gateway.complete([{"role": "user", "content": "hi"}])
    assert script.calls[0]["timeout"] == 12.5
    # LiteLLM 自己也会重试；不禁用它，我们的有界重试就变成乘法
    assert script.calls[0]["num_retries"] == 0


def test_retry_never_switches_provider(monkeypatch: pytest.MonkeyPatch) -> None:
    script = _Script(_exc("RateLimitError"), _resp())
    gateway = _gateway(script, monkeypatch)
    gateway.complete([{"role": "user", "content": "hi"}], model="deepseek/deepseek-chat")
    assert {call["model"] for call in script.calls} == {"deepseek/deepseek-chat"}


# ---------------------------------------------------------------- 能力校验


def _vision_messages() -> list[dict[str, Any]]:
    return [
        {
            "role": "user",
            "content": [
                {"type": "text", "text": "看图"},
                {"type": "image_url", "image_url": {"url": "data:image/png;base64,AAAA"}},
            ],
        }
    ]


def test_vision_request_refused_before_any_call(monkeypatch: pytest.MonkeyPatch) -> None:
    script = _Script()
    gateway = _gateway(script, monkeypatch)
    with pytest.raises(GatewayError) as caught:
        gateway.complete(_vision_messages())
    assert caught.value.code == UNSUPPORTED
    assert script.calls == []  # 一次都没发出去
    assert "vision" in caught.value.hint or "能力" in caught.value.hint


def test_vision_allowed_when_declared(monkeypatch: pytest.MonkeyPatch) -> None:
    script = _Script(_resp("看到了"))
    llm = LLM(capabilities={"deepseek/deepseek-chat": ModelCapability(vision=True)})
    gateway = _gateway(script, monkeypatch, llm=llm)
    assert gateway.complete(_vision_messages()).text == "看到了"
    assert len(script.calls) == 1


def test_tools_refused_when_model_declares_none(monkeypatch: pytest.MonkeyPatch) -> None:
    script = _Script()
    llm = LLM(capabilities={"deepseek/deepseek-chat": ModelCapability(tools=False)})
    gateway = _gateway(script, monkeypatch, llm=llm)
    with pytest.raises(GatewayError) as caught:
        gateway.complete(
            [{"role": "user", "content": "hi"}],
            tools=[{"type": "function", "function": {"name": "x"}}],
        )
    assert caught.value.code == UNSUPPORTED
    assert script.calls == []


def test_context_overflow_refused_locally(monkeypatch: pytest.MonkeyPatch) -> None:
    script = _Script()
    llm = LLM(capabilities={"deepseek/deepseek-chat": ModelCapability(context_tokens=10)})
    gateway = _gateway(script, monkeypatch, llm=llm)
    with pytest.raises(GatewayError) as caught:
        gateway.complete([{"role": "user", "content": "很长" * 500}])
    assert caught.value.code == CONTEXT_LENGTH
    assert script.calls == []


def test_unknown_route_is_rejected() -> None:
    gateway = gw.Gateway(Models())
    with pytest.raises(GatewayError) as caught:
        gateway.resolve("nope")
    assert caught.value.code == BAD_REQUEST


# ---------------------------------------------------------------- 脱敏


def test_error_text_never_contains_the_key(monkeypatch: pytest.MonkeyPatch) -> None:
    secret = "sk-proj-abcdefghijklmnop1234567890"
    script = _Script(
        _exc("AuthenticationError", f"Incorrect API key provided: {secret}. Bearer {secret}")
    )
    gateway = _gateway(script, monkeypatch)
    with pytest.raises(GatewayError) as caught:
        gateway.complete([{"role": "user", "content": "hi"}])
    text = str(caught.value)
    assert secret not in text
    assert "<已隐藏>" in text


def test_sanitize_keeps_readable_context() -> None:
    text = sanitize("model deepseek failed with api_key=abcdefgh123456 and sk-live-1234567890")
    assert "deepseek" in text and "abcdefgh123456" not in text


# ---------------------------------------------------------------- 响应边界


def test_unparsable_response_is_an_explained_error(monkeypatch: pytest.MonkeyPatch) -> None:
    script = _Script(_Resp(choices=[]))
    gateway = _gateway(script, monkeypatch)
    with pytest.raises(GatewayError) as caught:
        gateway.complete([{"role": "user", "content": "hi"}])
    assert caught.value.code == "unknown"


def test_usage_reported_when_present(monkeypatch: pytest.MonkeyPatch) -> None:
    script = _Script(_resp("hi", prompt=11, completion=7))
    gateway = _gateway(script, monkeypatch)
    reply = gateway.complete([{"role": "user", "content": "hi"}])
    assert reply.usage.reported is True
    assert reply.usage.total_tokens == 18
    assert reply.cost_micro is not None and reply.cost_source == "reported"


def test_missing_usage_is_marked_not_reported(monkeypatch: pytest.MonkeyPatch) -> None:
    script = _Script(_resp("hi", prompt=None))
    gateway = _gateway(script, monkeypatch)
    reply = gateway.complete([{"role": "user", "content": "hi"}])
    assert reply.usage.reported is False
    assert reply.usage.total_tokens is None


# ---------------------------------------------------------------- 估算与单价


def test_estimate_grows_with_content() -> None:
    short = estimate_tokens([{"role": "user", "content": "hi"}])
    long = estimate_tokens([{"role": "user", "content": "字" * 400}])
    assert long > short > 0


def test_vision_detection() -> None:
    assert uses_vision(_vision_messages()) is True
    assert uses_vision([{"role": "user", "content": "纯文本"}]) is False


def test_price_resolution_marks_local_free_vs_unknown() -> None:
    llm = LLM()
    local = resolve_price(llm, "ollama/qwen2.5:7b")
    assert local.known and local.source == "local_free"
    unknown = resolve_price(llm, "foo/bar")
    assert unknown.known is False and unknown.source == "unknown"
    known = resolve_price(llm, "deepseek/deepseek-chat")
    assert known.known and known.source == "litellm"


def test_config_price_overrides_table() -> None:
    llm = LLM(
        capabilities={
            "ollama/qwen2.5:7b": ModelCapability(
                input_usd_per_m=1.0, output_usd_per_m=2.0
            )
        }
    )
    price = resolve_price(llm, "ollama/qwen2.5:7b")
    assert price.source == "config"
    assert price.cost_usd(1_000_000, 1_000_000) == 3.0


# ---------------------------------------------------------------- 流式


@dataclass
class _Delta:
    content: str | None = None
    tool_calls: list[Any] | None = None


@dataclass
class _Chunk:
    choices: list[Any]
    usage: Any = None


def _chunk(text: str | None = None, *, tool_calls: list[Any] | None = None, usage=None) -> _Chunk:
    return _Chunk(
        choices=[type("_C", (), {"delta": _Delta(content=text, tool_calls=tool_calls)})()],
        usage=usage,
    )


def _call_delta(index: int, *, cid: str | None = None, name: str | None = None, args: str | None = None):
    function = type("_F", (), {"name": name, "arguments": args})()
    return type("_D", (), {"index": index, "id": cid, "function": function})()


class _Cancel:
    """按检查次数翻转的取消信号：模拟"用户在第 N 个分片后点了停止"。"""

    def __init__(self, after: int) -> None:
        self.after = after
        self.seen = 0

    @property
    def cancelled(self) -> bool:
        self.seen += 1
        return self.seen > self.after


def test_stream_emits_deltas_and_settles_usage(monkeypatch: pytest.MonkeyPatch) -> None:
    script = _Script(
        iter([_chunk("你"), _chunk("好"), _chunk(usage=_Usage(12, 3))])
    )
    gateway = _gateway(script, monkeypatch, llm=LLM(budget=LLMBudget(daily_usd=1.0)))
    events: list[gw.StreamEvent] = []
    reply = gateway.stream(
        [{"role": "user", "content": "hi"}], on_event=events.append
    )
    assert reply.text == "你好"
    assert [e.type for e in events] == ["delta", "delta", "usage", "done"]
    assert [e.text for e in events if e.type == "delta"] == ["你", "好"]
    assert reply.usage.reported and reply.usage.total_tokens == 15
    assert reply.cost_source == "reported"


def test_stream_accumulates_tool_call_fragments(monkeypatch: pytest.MonkeyPatch) -> None:
    script = _Script(
        iter(
            [
                _chunk(tool_calls=[_call_delta(0, cid="call_1", name="remember", args='{"te')]),
                _chunk(tool_calls=[_call_delta(0, args='xt": "拿铁"}')]),
                _chunk(usage=_Usage(20, 8)),
            ]
        )
    )
    gateway = _gateway(script, monkeypatch)
    reply = gateway.stream([{"role": "user", "content": "hi"}])
    assert len(reply.tool_calls) == 1
    assert reply.tool_calls[0].id == "call_1"
    assert reply.tool_calls[0].function.name == "remember"
    assert reply.tool_calls[0].function.arguments == '{"text": "拿铁"}'


def test_stream_midway_break_keeps_partial_text(monkeypatch: pytest.MonkeyPatch) -> None:
    def _broken():
        yield _chunk("已经写了一半")
        raise ConnectionError("connection reset by peer")

    script = _Script(_broken())
    gateway = _gateway(script, monkeypatch)
    reply = gateway.stream([{"role": "user", "content": "hi"}])
    assert reply.text == "已经写了一半"
    assert reply.incomplete is True and reply.error_code
    # 半途断连拿不到 usage：按预留保守记账，不当成免费
    assert reply.cost_source == "reserved"


def test_stream_fails_before_first_token(monkeypatch: pytest.MonkeyPatch) -> None:
    def _empty_failure():
        raise _exc("AuthenticationError", "bad key")
        yield  # pragma: no cover

    script = _Script(_empty_failure())
    gateway = _gateway(script, monkeypatch)
    with pytest.raises(GatewayError) as caught:
        gateway.stream([{"role": "user", "content": "hi"}])
    assert caught.value.code == AUTH


def test_stream_stops_when_cancelled(monkeypatch: pytest.MonkeyPatch) -> None:
    script = _Script(iter([_chunk("第一段"), _chunk("不该出现")]))
    gateway = _gateway(script, monkeypatch)
    cancel = _Cancel(after=2)  # 检查点：调用前 1 次 + 第一个分片后翻转
    with pytest.raises(GatewayError) as caught:
        gateway.stream([{"role": "user", "content": "hi"}], cancel=cancel)
    assert caught.value.code == CANCELLED


def test_stream_retries_only_before_first_token(monkeypatch: pytest.MonkeyPatch) -> None:
    """建立连接阶段可以重试；已经吐字之后重试会重复内容，所以不允许。"""
    script = _Script(_exc("RateLimitError"), iter([_chunk("好")]))
    gateway = _gateway(script, monkeypatch)
    reply = gateway.stream([{"role": "user", "content": "hi"}])
    assert reply.text == "好" and reply.attempts == 2


def test_stream_falls_back_to_complete_when_not_declared(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    script = _Script(_resp("同步回答"))
    llm = LLM(capabilities={"deepseek/deepseek-chat": ModelCapability(stream=False)})
    gateway = _gateway(script, monkeypatch, llm=llm)
    reply = gateway.stream([{"role": "user", "content": "hi"}])
    assert reply.text == "同步回答"
    assert script.calls and script.calls[0].get("stream") is None  # 走的同步接口


# ---------------------------------------------------------------- 推理档位（D30）


def test_reasoning_auto_sends_nothing():
    """auto / 空串 = 一个参数都不发：沿用供应商默认，别替用户做主。"""
    assert gw.reasoning_params("deepseek/deepseek-flash", "auto") == {}
    assert gw.reasoning_params("deepseek/deepseek-flash", "") == {}


def test_reasoning_off_disables_thinking_on_deepseek():
    assert gw.reasoning_params("deepseek/deepseek-flash", "off") == {
        "extra_body": {"thinking": {"type": "disabled"}}
    }


def test_reasoning_levels_go_through_extra_body_on_deepseek():
    """thinking 与 reasoning_effort 必须走 extra_body：LiteLLM 的 openai 兼容层会把
    reasoning_effort 当未知顶层参数过滤掉，注册成顶层参数等于"设了没用"。"""
    for level in ("low", "medium", "high", "max"):
        assert gw.reasoning_params("deepseek/deepseek-flash", level) == {
            "extra_body": {"thinking": {"type": "enabled"}, "reasoning_effort": level}
        }


def test_reasoning_off_maps_to_none_on_openai():
    assert gw.reasoning_params("openai/gpt-5.5", "off") == {"reasoning_effort": "none"}
    assert gw.reasoning_params("openai/gpt-5.5", "high") == {"reasoning_effort": "high"}


@pytest.mark.parametrize(
    "model",
    ["anthropic/claude-opus-4-8", "ollama/qwen2.5:7b", "dashscope/qwen-max", "自定义/中转"],
)
def test_reasoning_unknown_provider_sends_nothing(model):
    """不认识这两个参数的供应商一律不发：被静默忽略至少还能聊天，
    发错参数 400 会直接变成"聊不了天"。"""
    assert gw.reasoning_params(model, "high") == {}


def test_gateway_attaches_reasoning_params_for_matching_slot(monkeypatch):
    from omni.config import Reasoning

    script = _Script(_resp("OK"))
    monkeypatch.setattr(gw, "_completion", script)
    gateway = gw.Gateway(
        Models(chat="deepseek/deepseek-flash"),
        reasoning=Reasoning(chat="high"),
    )
    gateway.complete([{"role": "user", "content": "hi"}])
    assert script.calls[0]["extra_body"] == {
        "thinking": {"type": "enabled"},
        "reasoning_effort": "high",
    }


def test_gateway_leaves_other_models_alone(monkeypatch):
    """档位只跟"哪个槽位用了这个模型串"绑定：同一进程里调用别的模型不该被牵连。"""
    from omni.config import Reasoning

    script = _Script(_resp("OK"))
    monkeypatch.setattr(gw, "_completion", script)
    gateway = gw.Gateway(
        Models(chat="deepseek/deepseek-flash"),
        reasoning=Reasoning(chat="max"),
    )
    gateway.complete([{"role": "user", "content": "hi"}], model="openai/gpt-5.5")
    assert "extra_body" not in script.calls[0]
    assert "reasoning_effort" not in script.calls[0]


def test_gateway_without_reasoning_config_sends_nothing(monkeypatch):
    """没配 reasoning（老配置/测试）也不能多发参数：默认行为必须与升级前一致。"""
    script = _Script(_resp("OK"))
    monkeypatch.setattr(gw, "_completion", script)
    gw.Gateway(Models(chat="deepseek/deepseek-flash")).complete([{"role": "user", "content": "hi"}])
    assert "extra_body" not in script.calls[0]
