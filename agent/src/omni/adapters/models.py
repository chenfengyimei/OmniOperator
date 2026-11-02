"""Async model adapter. Credentials and vendor exceptions do not cross the port."""

from __future__ import annotations

import asyncio
import json
import logging
import os
from collections.abc import Sequence

from ..application.ports import Record, SecretStore
from ..domain.contracts import BusinessError, Invocation, ModelReply, ToolSpec
from ..domain.settings import Settings
from .diagnostics import Redactor


class LiteLLMModel:
    def __init__(self, secrets: SecretStore, redactor: Redactor) -> None:
        self.secrets, self.redactor = secrets, redactor

    async def complete(self, messages: Sequence[Record], tools: Sequence[ToolSpec], settings: Settings) -> ModelReply:
        if os.environ.get("OMNI_TEST_OFFLINE") == "1":
            raise BusinessError("offline_model_denied", "Real model calls are disabled in offline tests")
        model = str(settings.get("model", ""))
        if not model:
            raise BusinessError("model_not_configured", "Choose a model first")
        kwargs = {"model": model, "messages": list(messages), "timeout": 120, "num_retries": 0}
        reference = settings.get("credential_ref")
        if reference:
            secret = await asyncio.to_thread(self.secrets.get, str(reference))
            if secret is None:
                raise BusinessError("credential_missing", "Configured model credential does not exist")
            self.redactor.register(secret)
            kwargs["api_key"] = secret
        if settings.get("model_base_url"):
            kwargs["api_base"] = settings["model_base_url"]
        if tools:
            kwargs["tools"] = [
                {
                    "type": "function",
                    "function": {"name": t.name, "description": t.description, "parameters": t.parameters},
                }
                for t in tools
            ]
        os.environ.setdefault("LITELLM_LOCAL_MODEL_COST_MAP", "True")
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
        cost = None
        try:
            price = litellm.completion_cost(completion_response=response)
            if price is not None and price >= 0:
                cost = max(1, round(price * 1_000_000)) if price > 0 else 0
        except Exception:  # noqa: BLE001 -- vendor price lookup can fail for unknown models
            logging.getLogger("omni.v2.models").info(
                "Model price unavailable; reservation retained", extra={"event": "model.price_unknown"}
            )
        usage = payload.get("usage") or {}
        return ModelReply(
            message.get("content") or "",
            tuple(calls),
            usage.get("prompt_tokens"),
            usage.get("completion_tokens"),
            cost,
        )
