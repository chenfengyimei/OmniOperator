"""One-shot LiteLLM chat stream with verified async upstream close; no persistence or global runtime."""

from __future__ import annotations

import asyncio
import inspect
import json
import types
from collections.abc import Awaitable, Callable
from dataclasses import replace
from typing import Any, cast

from ..domain.contracts import BusinessError, ModelReply
from ..domain.model_stream import MAX_CHUNKS, MAX_FRAME_BYTES, MAX_STREAM_BYTES, StreamAssembly, invalid


class ProviderEvidence:
    """Observe provider frames before SDK-synthesized completion/usage chunks."""

    def __init__(self, native: Any) -> None:
        self.native = native
        self.finish_reason = ""
        self.usage: dict[str, object] | None = None
        self.chunks = self.wire_bytes = 0

    def __aiter__(self) -> ProviderEvidence:
        return self

    async def __anext__(self) -> Any:
        value = await self.native.__anext__()
        raw: Any = value.model_dump() if hasattr(value, "model_dump") else value
        try:
            size = (
                len(raw)
                if isinstance(raw, bytes)
                else len(
                    (
                        raw
                        if isinstance(raw, str)
                        else json.dumps(raw, ensure_ascii=False, allow_nan=False, default=lambda v: v.model_dump())
                    ).encode("utf-8")
                )
            )
        except (ValueError, TypeError, AttributeError, UnicodeError):
            raise invalid() from None
        self.chunks += 1
        self.wire_bytes += size
        if self.chunks > MAX_CHUNKS or size > MAX_FRAME_BYTES or self.wire_bytes > MAX_STREAM_BYTES:
            raise invalid("model_stream_too_large")
        if isinstance(raw, (str, bytes)):
            try:
                raw = json.loads(
                    raw.removeprefix("data:").strip() if isinstance(raw, str) else raw.removeprefix(b"data:").strip()
                )
            except (ValueError, TypeError):
                raw = None
        if isinstance(raw, dict):
            usage = cast(dict[str, Any], raw).get("usage")
            dump = getattr(usage, "model_dump", None)
            if callable(dump):
                usage = dump()
            if isinstance(usage, dict) and all(
                type(cast(dict[str, Any], usage).get(k)) is int and 0 <= cast(dict[str, Any], usage)[k] <= 2**63 - 1
                for k in ("prompt_tokens", "completion_tokens")
            ):
                self.usage = dict(cast(dict[str, object], usage))
            choices = cast(dict[str, Any], raw).get("choices")
            reason = (
                cast(dict[str, Any], choices[0]).get("finish_reason")
                if isinstance(choices, list) and len(cast(list[Any], choices)) == 1 and isinstance(choices[0], dict)
                else cast(dict[str, Any], raw).get("finish_reason")
            )
            if reason in {"stop", "tool_calls", "length", "content_filter"}:
                self.finish_reason = reason
        return value

    async def aclose(self) -> None:
        await PreparedStream.closer(self.native)()


async def finish_close(close: Awaitable[None]) -> None:
    # Keep the run task alive through repeated cancellation while the actual transport closes.
    async def perform() -> None:
        await close

    task = asyncio.create_task(perform(), name="model-stream-close")
    interrupted = False
    while not task.done():
        try:
            await asyncio.shield(task)
        except asyncio.CancelledError:
            interrupted = True
    task.result()
    if interrupted:
        raise asyncio.CancelledError


class PreparedStream:
    def __init__(
        self,
        send: Callable[[], Awaitable[Any]],
        price: Callable[[dict[str, Any]], int | None],
        released: Callable[[], None] = lambda: None,
    ) -> None:
        self._send, self._price, self._used = send, price, False
        self._stream: Any
        self._native: Any
        self._stream = self._native = None
        self._released = released
        self._close_lock = asyncio.Lock()
        self.active = self.cleanup_pending = self._closed = False

    @staticmethod
    def closer(native: Any) -> Callable[[], Awaitable[None]]:
        for name in ("aclose", "close"):
            method = getattr(native, name, None)
            if method is not None and (
                inspect.iscoroutinefunction(method) or isinstance(native, types.AsyncGeneratorType)
            ):
                return cast(Callable[[], Awaitable[None]], method)
        raise BusinessError("model_stream_cleanup_failed", "Streaming transport has no verifiable async close")

    async def close(self) -> None:
        async with self._close_lock:
            if self._stream is not None:
                try:
                    if self._native is not None:
                        await self.closer(self._native)()
                    self._stream.completion_stream = None
                    await self._stream.aclose()
                except asyncio.CancelledError:
                    self.cleanup_pending = True
                    raise
                except Exception:  # noqa: BLE001 -- native failures may embed private provider content
                    self.cleanup_pending = True
                    # Keep the actual stream owner registered for lifecycle recovery; no vendor text crosses the port.
                    raise BusinessError(
                        "model_stream_cleanup_failed", "Streaming transport reclamation is pending"
                    ) from None
                self._stream = self._native = None
            self._closed, self.cleanup_pending = True, False
            self._released()

    async def consume(self, emit: Callable[[str], Awaitable[None]]) -> ModelReply:
        if self._used or self._closed:
            raise BusinessError("model_invocation_reused", "Prepared model invocation cannot be replayed")
        self._used = True
        self.active = True
        stream = native = None
        try:
            async with asyncio.timeout(130):
                stream = await self._send()
                self._stream = stream
                # LiteLLM's outer close swallows upstream exceptions. Observe the actual async native close directly.
                native = stream.completion_stream
                self._native = native
                if native is None or not hasattr(native, "__anext__"):
                    raise BusinessError(
                        "model_stream_transport_unsupported",
                        "This provider lacks a verifiable async streaming transport",
                        status=502,
                    )
                self.closer(native)
                evidence = ProviderEvidence(native)
                stream.completion_stream = evidence
                assembled = StreamAssembly()
                iterator = stream.__aiter__()
                while True:
                    try:
                        async with asyncio.timeout(30):
                            chunk = await iterator.__anext__()
                    except StopAsyncIteration:
                        break
                    payload: dict[str, Any] = chunk.model_dump()
                    # The locked SDK emits its terminal usage in one empty choice instead of choices=[].
                    choices = payload.get("choices")
                    if (
                        assembled.finish_reason
                        and payload.get("usage") is not None
                        and isinstance(choices, list)
                        and len(cast(list[Any], choices)) == 1
                    ):
                        choice = cast(dict[str, Any], choices[0])
                        delta_value = choice.get("delta") if isinstance(cast(object, choice), dict) else None
                        if (
                            choice.get("index") == 0
                            and choice.get("finish_reason") is None
                            and isinstance(delta_value, dict)
                            and all(v is None or v == "" for v in cast(dict[str, Any], delta_value).values())
                        ):
                            payload = {**payload, "choices": []}
                    delta = assembled.feed(payload)
                    if delta:
                        await emit(delta)
                if not evidence.finish_reason or evidence.finish_reason != assembled.finish_reason:
                    raise BusinessError(
                        "model_stream_incomplete",
                        "The provider did not confirm the end of the model response",
                        status=502,
                    )
                # The SDK may calculate a usage estimate at EOF. Only provider-origin counts may release a reservation.
                assembled.usage = evidence.usage
                reply = assembled.result()
                payload = {
                    "model": assembled.model,
                    "choices": [
                        {
                            "index": 0,
                            "finish_reason": reply.completion_reason,
                            "message": {
                                "role": "assistant",
                                "content": reply.text,
                                "tool_calls": [
                                    {
                                        "id": c.id,
                                        "type": "function",
                                        "function": {"name": c.name, "arguments": json.dumps(c.arguments)},
                                    }
                                    for c in reply.calls
                                ],
                            },
                        }
                    ],
                    "usage": assembled.usage,
                }
                # Missing usage is unknown, never a zero charge or a local token guess.
                return replace(reply, cost_micro=self._price(payload) if assembled.usage is not None else None)
        except asyncio.CancelledError:
            raise
        except BusinessError:
            raise
        except Exception as error:
            raise BusinessError(
                "model_stream_failed",
                "Model stream ended without a confirmed result; its charge requires reconciliation",
                status=502,
            ) from error
        finally:
            self.active = False
            await finish_close(self.close())
