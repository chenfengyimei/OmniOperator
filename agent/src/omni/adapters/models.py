"""Async model adapter. Credentials and vendor exceptions do not cross the port."""

from __future__ import annotations

import asyncio
import json
import logging
import os
from collections.abc import Awaitable, Callable, Sequence

from ..application.ports import AsyncSecretPort, Record
from ..domain.contracts import BusinessError, Invocation, ModelReply, ToolSpec
from ..domain.metering import wire_tools
from ..domain.settings import Settings
from .diagnostics import Redactor
from .model_stream import PreparedStream


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
            return await litellm.acompletion(**kwargs, stream=True, stream_options={"include_usage": True})

        # Credential preparation can yield. Enforce capacity again before publishing ownership.
        if self._closing or len(self._streams) >= 4:
            raise BusinessError(
                "model_stream_capacity", "Model streaming transport admission is closed or full", status=503
            )
        prepared = PreparedStream(
            send, lambda payload: self._price(litellm.ModelResponse(**payload)), lambda: self._streams.discard(prepared)
        )
        self._streams.add(prepared)
        return prepared

    def health(self) -> dict:
        return {
            "enabled": True,
            "owned": len(self._streams),
            "closing": self._closing,
            "active": sum(s.active for s in self._streams),
            "cleanup_pending": sum(s.cleanup_pending for s in self._streams),
        }

    def stop_accepting(self):
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

    async def _prepare(self, messages: Sequence[Record], tools: Sequence[ToolSpec], settings: Settings) -> dict:
        if os.environ.get("OMNI_TEST_OFFLINE") == "1":
            raise BusinessError("offline_model_denied", "Real model calls are disabled in offline tests")
        model = str(settings.get("model", ""))
        if not model:
            raise BusinessError("model_not_configured", "Choose a model first")
        kwargs = {
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

    async def _send(self, kwargs: dict) -> ModelReply:
        import litellm

        try:
            async with asyncio.timeout(130):
                response = await litellm.acompletion(**kwargs)
        except asyncio.CancelledError:
            raise
        except Exception as exc:
            raise BusinessError(
                "model_call_failed", "Model request failed; its charge may require reconciliation", status=502
            ) from exc
        if not isinstance(response, litellm.ModelResponse):
            raise BusinessError("invalid_model_response", "Expected a non-streaming model response", status=502)
        payload = response.model_dump()
        calls = []
        message = payload["choices"][0]["message"]
        for call in message.get("tool_calls") or []:
            try:
                arguments = json.loads(call["function"]["arguments"])
                if not isinstance(arguments, dict):
                    raise ValueError
            except (ValueError, TypeError) as exc:
                raise BusinessError(
                    "invalid_tool_arguments", "Model returned invalid tool arguments", status=502
                ) from exc
            calls.append(Invocation(str(call["id"]), str(call["function"]["name"]), arguments))
        cost = self._price(response)
        usage = payload.get("usage") or {}
        return ModelReply(
            message.get("content") or "",
            tuple(calls),
            usage.get("prompt_tokens"),
            usage.get("completion_tokens"),
            cost,
            completion_reason=payload["choices"][0].get("finish_reason") or "stop",
        )

    @staticmethod
    def _price(response) -> int | None:
        import litellm

        cost = None
        try:
            price = litellm.completion_cost(completion_response=response)
            if price is not None and price >= 0:
                cost = max(1, round(price * 1_000_000)) if price > 0 else 0
        except Exception:  # noqa: BLE001 -- vendor price lookup can fail for unknown models
            logging.getLogger("omni.v2.models").info(
                "Model price unavailable; reservation retained", extra={"event": "model.price_unknown"}
            )
        return cost
