"""Async model adapter. Credentials and vendor exceptions do not cross the port."""

from __future__ import annotations

import asyncio
import hashlib
import json
import logging
import os
from collections.abc import Awaitable, Callable, Sequence
from typing import Any, cast

from httpx import Response as HTTPResponse
from httpx import ResponseNotRead

from ..application.ports import AsyncSecretPort, Record
from ..domain.contracts import BusinessError, Invocation, ModelReply, ToolSpec
from ..domain.metering import wire_tools
from ..domain.model_recovery import ModelRejected
from ..domain.settings import Settings
from .diagnostics import Redactor
from .model_stream import PreparedStream

CACHE_KEY_PROVIDERS = ("xai/", "x-ai/", "openai/", "gpt-", "o1", "o3", "o4")


def _rejection(error: Exception) -> ModelRejected | None:
    response: object = getattr(error, "response", None)
    if not isinstance(response, HTTPResponse):
        return None
    status = response.status_code
    if status == 429:
        rejected = ModelRejected("model_rate_limited", "Provider rejected admission before generation", status=429)
    elif status in {400, 413} and type(error).__name__ == "ContextWindowExceededError":
        rejected = ModelRejected("model_context_rejected", "Provider rejected the context window", status=422)
    else:
        return None
    # LiteLLM can replace the outer response with a status-only response. Look for the actual
    # matching HTTP receipt in this exception chain, including its explicit zero usage.
    current: BaseException | None = error
    for _ in range(8):
        if current is None:
            break
        receipt: object = getattr(current, "response", None)
        if isinstance(receipt, HTTPResponse) and receipt.status_code == status and _zero_usage(receipt):
            return rejected
        current = current.__cause__ or current.__context__
    return None


def _zero_usage(response: HTTPResponse) -> bool:
    if not response.headers.get("content-type"):
        return False
    try:
        record: object = response.json()
    except (ResponseNotRead, ValueError):
        return False
    raw_usage = cast(dict[str, object], record).get("usage") if isinstance(record, dict) else None
    if not isinstance(raw_usage, dict):
        return False
    usage = cast(dict[str, object], raw_usage)
    return all(type(usage.get(key)) is int and usage[key] == 0 for key in ("prompt_tokens", "completion_tokens"))


def prompt_cache_key(settings: Settings) -> str | None:
    """Stable opaque routing key for the run's (agent, session, content_epoch) cache bucket."""
    agent = cast(object, settings.get("agent_id"))
    session = cast(object, settings.get("session_id"))
    epoch = cast(object, settings.get("content_epoch"))
    if not isinstance(agent, str) or not isinstance(session, str) or type(epoch) is not int:
        return None
    digest = hashlib.sha256(f"{agent}|{session}|{epoch}".encode()).hexdigest()[:24]
    return "pc_" + digest


def cache_route(settings: Settings) -> bool:
    """Only inject the key where the provider uses routing affinity; content-address routes rely on bytes."""
    base = str(settings.get("base_url", "")).lower()
    model = str(settings.get("model", "")).lower()
    if "api.x.ai" in base or model.startswith(("xai/", "x-ai/")) or "grok" in model:
        return True
    return "openai" in base or model.startswith(CACHE_KEY_PROVIDERS)


class PreparedCall:
    def __init__(self, send: Callable[[], Awaitable[ModelReply]]) -> None:
        self._send, self._used = send, False

    async def send(self) -> ModelReply:
        if self._used:
            raise BusinessError("model_invocation_reused", "Prepared model invocation cannot be replayed")
        self._used = True
        return await self._send()


class LiteLLMModel:
    def __init__(
        self,
        secrets: AsyncSecretPort | None,
        redactor: Redactor,
        configure_logging: Callable[[tuple[logging.Logger, ...]], None] | None = None,
    ) -> None:
        self.secrets, self.redactor = secrets, redactor
        self._configure_logging = configure_logging
        self._streams: set[PreparedStream] = set()
        self._closing = False

    async def complete(self, messages: Sequence[Record], tools: Sequence[ToolSpec], settings: Settings) -> ModelReply:
        return await (await self.prepare(messages, tools, settings)).send()

    async def prepare(self, messages: Sequence[Record], tools: Sequence[ToolSpec], settings: Settings) -> PreparedCall:
        kwargs = await self._prepare(messages, tools, settings)
        return PreparedCall(lambda: self._send(kwargs))

    async def prepare_stream(
        self, messages: Sequence[Record], tools: Sequence[ToolSpec], settings: Settings
    ) -> PreparedStream:
        if self._closing or len(self._streams) >= 4:
            raise BusinessError(
                "model_stream_capacity", "Model streaming transport admission is closed or full", status=503
            )
        kwargs = await self._prepare(messages, tools, settings)
        import litellm

        async def send():
            try:
                return await litellm.acompletion(**kwargs, stream=True, stream_options={"include_usage": True})
            except Exception as exc:
                rejected = _rejection(exc)
                if rejected is not None:
                    raise rejected from None
                raise

        # Credential preparation can yield. Enforce capacity again before publishing ownership.
        if self._closing or len(self._streams) >= 4:
            raise BusinessError(
                "model_stream_capacity", "Model streaming transport admission is closed or full", status=503
            )

        def price(payload: dict[str, Any]) -> int | None:
            return self._price(litellm.ModelResponse(**payload))

        prepared = PreparedStream(send, price, lambda: self._streams.discard(prepared))
        self._streams.add(prepared)
        return prepared

    def health(self) -> dict[str, Any]:
        return {
            "enabled": True,
            "owned": len(self._streams),
            "closing": self._closing,
            "active": sum(s.active for s in self._streams),
            "cleanup_pending": sum(s.cleanup_pending for s in self._streams),
        }

    def stop_accepting(self) -> None:
        self._closing = True

    async def close(self, timeout_s: float = 30) -> bool:
        self.stop_accepting()
        deadline = asyncio.get_running_loop().time() + max(0, timeout_s)
        for stream in tuple(self._streams):
            if stream.active:
                return False
            try:
                async with asyncio.timeout(max(0, deadline - asyncio.get_running_loop().time())):
                    await stream.close()
            except (BusinessError, TimeoutError):
                return False
        return not self._streams

    async def _prepare(
        self, messages: Sequence[Record], tools: Sequence[ToolSpec], settings: Settings
    ) -> dict[str, Any]:
        if os.environ.get("OMNI_TEST_OFFLINE") == "1":
            raise BusinessError("offline_model_denied", "Real model calls are disabled in offline tests")
        model = str(settings.get("model", ""))
        if not model:
            raise BusinessError("model_not_configured", "Choose a model first")
        kwargs: dict[str, Any] = {
            "model": model,
            "messages": list(messages),
            "timeout": 120,
            "num_retries": 0,
            "no-log": True,
            "caching": False,
            "max_tokens": settings["output_tokens"],
        }
        reference = settings.get("credential_ref")
        if reference:
            secret = await self.secrets.get(str(reference)) if self.secrets else None
            if not secret:
                raise BusinessError("credential_missing", "Configured model credential does not exist")
            kwargs["api_key"] = secret
        if settings.get("base_url"):
            kwargs["api_base"] = settings["base_url"]
        headers: dict[str, str] = {"User-Agent": "omni-operator/2.0"}
        # OpenCode Go 要求每个会话携带稳定 x-opencode-session 以便路由与提示缓存。
        # 以 model_profile_id 作为稳定会话标识（同一配置=同一会话）。
        if "opencode.ai" in str(settings.get("base_url", "")):
            headers["x-opencode-session"] = str(settings.get("model_profile_id") or "omni-operator")
        custom = settings.get("extra_headers")
        if isinstance(custom, dict):
            for key, value in cast(dict[object, object], custom).items():
                if (
                    isinstance(key, str)
                    and isinstance(value, str)
                    and key.lower() not in {"authorization", "x-api-key"}
                ):
                    headers[key] = value
        kwargs["extra_headers"] = headers
        cache_key = prompt_cache_key(settings)
        if cache_key is not None and cache_route(settings):
            # Provider routing affinity only; request messages/prefix bytes are untouched.
            extra_body: object = kwargs.get("extra_body")
            body: dict[str, Any] = dict(cast(dict[str, Any], extra_body)) if isinstance(extra_body, dict) else {}
            body["prompt_cache_key"] = cache_key
            kwargs["extra_body"] = body
        if tools:
            kwargs["tools"] = wire_tools(tools)
        os.environ.setdefault("LITELLM_LOCAL_MODEL_COST_MAP", "True")
        import litellm

        litellm.suppress_debug_info = True
        litellm.turn_off_message_logging = True
        if self._configure_logging:
            self._configure_logging(
                tuple(logging.getLogger(name) for name in ("LiteLLM", "LiteLLM Router", "LiteLLM Proxy"))
            )
        return kwargs

    async def _send(self, kwargs: dict[str, Any]) -> ModelReply:
        import litellm

        try:
            async with asyncio.timeout(130):
                response = await litellm.acompletion(**kwargs)
        except asyncio.CancelledError:
            raise
        except Exception as exc:
            rejected = _rejection(exc)
            if rejected is not None:
                raise rejected from None
            raise BusinessError(
                "model_call_failed", "Model request failed; its charge may require reconciliation", status=502
            ) from exc
        if not isinstance(response, litellm.ModelResponse):
            raise BusinessError("invalid_model_response", "Expected a non-streaming model response", status=502)
        # The vendor response is a pydantic model whose dump type is partially unknown here.
        native: Any = response
        payload: Any = native.model_dump()
        calls: list[Invocation] = []
        message = payload["choices"][0]["message"]
        for call in cast(list[dict[str, Any]], message.get("tool_calls") or []):
            try:
                arguments = json.loads(call["function"]["arguments"])
                if not isinstance(arguments, dict):
                    raise ValueError
            except (ValueError, TypeError) as exc:
                raise BusinessError(
                    "invalid_tool_arguments", "Model returned invalid tool arguments", status=502
                ) from exc
            calls.append(Invocation(str(call["id"]), str(call["function"]["name"]), cast(dict[str, Any], arguments)))
        cost = self._price(response)
        usage: dict[str, Any] = payload.get("usage") or {}
        details: dict[str, Any] = usage.get("prompt_tokens_details") or {}
        # 提示缓存用量各家字段不同：DeepSeek/OpenAI 系 hit/cached，Anthropic 系 read/creation。
        cached = usage.get("prompt_cache_hit_tokens")
        if cached is None:
            cached = details.get("cached_tokens")
        if cached is None:
            cached = usage.get("cache_read_input_tokens")
        created = usage.get("cache_creation_input_tokens")
        if created is None:
            created = details.get("cache_creation_tokens")
        return ModelReply(
            message.get("content") or "",
            tuple(calls),
            usage.get("prompt_tokens"),
            usage.get("completion_tokens"),
            cost,
            completion_reason=payload["choices"][0].get("finish_reason") or "stop",
            cached_prompt_tokens=cached if type(cached) is int and cached >= 0 else None,
            cache_creation_tokens=created if type(created) is int and created >= 0 else None,
        )

    @staticmethod
    def _price(response: Any) -> int | None:
        import litellm

        cost = None
        try:
            price = cast(float | None, litellm.completion_cost(completion_response=response))
            if price is not None and price >= 0:
                cost = max(1, round(price * 1_000_000)) if price > 0 else 0
        except Exception:  # noqa: BLE001 -- vendor price lookup can fail for unknown models
            logging.getLogger("omni.v2.models").info(
                "Model price unavailable; reservation retained", extra={"event": "model.price_unknown"}
            )
        return cost
