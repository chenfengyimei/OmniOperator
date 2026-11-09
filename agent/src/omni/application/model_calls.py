"""One preflight, authorization and settlement path for model and compression calls."""

from __future__ import annotations

import asyncio
from collections.abc import Mapping, Sequence

from ..domain.context import CONTEXT_CHARS
from ..domain.contracts import BusinessError, ModelReply, Outcome, ToolSpec
from ..domain.images import references
from ..domain.metering import TokenEstimate, serialized_request
from ..domain.settings import Settings
from .images import ImageService
from .model_output import ModelOutput
from .ports import DatabasePort, ModelPort, ModelPreparationPort, ModelStreamingPort, Observer, Record, TokenCounter


class ModelCalls:
    def __init__(
        self,
        database: DatabasePort,
        model: ModelPort,
        observer: Observer,
        counter: TokenCounter,
        *,
        preparer: ModelPreparationPort | None = None,
        streamer: ModelStreamingPort | None = None,
        images: ImageService | None = None,
    ) -> None:
        self._db, self._model, self._observer, self._counter = database, model, observer, counter
        self._preparer = preparer
        self._streamer = streamer
        self._images = images
        self._output = ModelOutput()

    def health(self) -> dict:
        return self._streamer.health() if self._streamer is not None else {"enabled": False, "owned": 0}

    async def live_output(self, agent_id: str, run_id: str) -> Record:
        run = await self._db.read(lambda u: u.get_run(agent_id, run_id))
        result: Record = {"run_id": run_id, "persisted_state": run["state"], "state": "unavailable", "text": ""}
        if run["state"] != "running":
            self._output.clear(agent_id, run_id)
            return result
        if self._output.get(agent_id, run_id) is None:
            return result
        authority = self._output.authority(agent_id, run_id)

        def authorize(u):
            u.assert_run_authorized(agent_id, run_id)
            u.assert_context(agent_id, run_id)
            u.assert_model_authorized(agent_id, run_id)
            if u.model_dispatch_authority(agent_id, run_id) != authority:
                raise BusinessError("credential_changed", "Model credential authority changed")

        try:
            await self._db.read(authorize)
        except BusinessError as error:
            self._output.clear(agent_id, run_id)
            return {**result, "reason": error.code}
        row = self._output.get(agent_id, run_id)
        return {**result, **row, "state": "streaming", "ephemeral": True} if row else result

    def counter_available(self, settings: Mapping[str, object]) -> bool:
        return self._counter.supports(
            str(settings["counter_mode"]), str(settings["counter_ref"]), str(settings["model"])
        )

    async def estimate(
        self, messages: Sequence[Record], tools: Sequence[ToolSpec], settings: Settings
    ) -> TokenEstimate:
        try:
            if not self.counter_available(settings):
                raise ValueError("Unsupported counter")
            estimate = await asyncio.wait_for(self._counter.count(messages, tools, settings), timeout=2)
            if (
                not isinstance(estimate, TokenEstimate)
                or estimate.counter_id != settings["counter_ref"]
                or (settings["counter_mode"] == "required" and estimate.method != "exact")
            ):
                raise ValueError("Counter identity or quality mismatch")
            return estimate
        except asyncio.CancelledError:
            raise
        except Exception as exc:
            raise BusinessError(
                "token_counter_unavailable",
                "Required token counting is unavailable; no model request was sent",
                status=503,
            ) from exc

    @staticmethod
    def fits(estimate: TokenEstimate, settings: Settings, *, summary: bool = False) -> bool:
        output = settings["summary_tokens"] if summary else settings["output_tokens"]
        return estimate.tokens + output + settings["margin_tokens"] <= settings["context_tokens"]

    async def invoke(
        self,
        agent_id: str,
        run_id: str,
        messages: Sequence[Record],
        tools: Sequence[ToolSpec],
        settings: Settings,
        *,
        reserved_id: str | None = None,
        baseline_tools: Sequence[ToolSpec] | None = None,
    ) -> ModelReply:
        def reserve(u):
            u.assert_context(agent_id, run_id)
            u.assert_model_authorized(agent_id, run_id)
            return u.reserve_call(
                agent_id, run_id, settings["call_reservation_micro"], settings["daily_budget_micro"], settings["model"]
            )

        identity = reserved_id or await self._db.write(reserve)
        output = settings["summary_tokens"] if reserved_id else settings["output_tokens"]
        refs = []
        try:
            refs = references(messages)
            if refs and (self._images is None or reserved_id is not None):
                raise BusinessError("image_protocol_unavailable", "Image materialization is unavailable for this call")
            await self._db.read(lambda u: u.assert_context(agent_id, run_id))
            await self._db.read(lambda u: u.assert_model_authorized(agent_id, run_id))
            if len(serialized_request(messages, tools)) > CONTEXT_CHARS:
                raise BusinessError(
                    "context_budget_exceeded", "The serialized request exceeds the character safety limit"
                )
            estimate = await self.estimate(messages, tools, settings)

            def register(u):
                u.assert_context(agent_id, run_id)
                if baseline_tools is not None:
                    u.route_call(agent_id, run_id, identity, tools, baseline_tools)
                u.record_metering(
                    agent_id,
                    identity,
                    estimate.tokens,
                    estimate.method,
                    estimate.counter_id,
                    output,
                    settings["context_tokens"],
                )

            await self._db.write(register)
            if not self.fits(estimate, settings, summary=bool(reserved_id)):
                raise BusinessError(
                    "token_budget_exceeded", "Input, output and safety margin exceed the declared model capacity"
                )
        except BaseException:
            await self._db.write(lambda u: u.settle_call(agent_id, identity, None, known_not_sent=True))
            raise
        known = False
        dispatched = False
        streaming = None
        try:
            with (
                self._observer.context(operation_id=identity),
                self._observer.span("model.summary" if reserved_id else "model.complete") as measured,
            ):
                try:
                    authority = await self._db.read(lambda u: u.model_dispatch_authority(agent_id, run_id))

                    def authorize(u):
                        u.assert_run_authorized(agent_id, run_id)
                        u.assert_context(agent_id, run_id)
                        u.assert_model_authorized(agent_id, run_id)
                        if u.model_dispatch_authority(agent_id, run_id) != authority:
                            raise BusinessError(
                                "credential_changed", "Model credential authority changed; prepare a new request"
                            )

                    async def guard():
                        await self._db.read(authorize)

                    wire_messages = list(messages)
                    if refs and self._images is not None:

                        def prepare_images(u):
                            authorize(u)
                            u.model_images_prepare(agent_id, run_id, identity, refs)

                        await self._db.write(prepare_images)
                        wire_messages = await self._images.wire(agent_id, wire_messages, guard)
                    streaming = (
                        await self._streamer.prepare_stream(wire_messages, tools, settings)
                        if self._streamer is not None and not reserved_id
                        else None
                    )
                    invocation = (
                        await self._preparer.prepare(wire_messages, tools, {**settings, "output_tokens": output})
                        if self._preparer and streaming is None
                        else None
                    )

                    # Credentials may have blocked in the OS store. Recheck
                    # authority after preparation and immediately before send.
                    if refs:

                        def dispatch_images(u):
                            authorize(u)
                            u.model_images_dispatch(agent_id, run_id, identity)

                        await self._db.write(dispatch_images)
                    else:
                        await self._db.read(authorize)
                    if streaming is not None:
                        self._output.begin(agent_id, run_id, identity, authority)

                    async def emit(delta: str):
                        await self._db.read(authorize)
                        self._output.append(agent_id, run_id, identity, delta)

                    dispatched = True
                    reply = (
                        await streaming.consume(emit)
                        if streaming is not None
                        else await invocation.send()
                        if invocation
                        else await self._model.complete(wire_messages, tools, {**settings, "output_tokens": output})
                    )
                    if measured:
                        measured.result(Outcome.UNKNOWN if reply.cost_micro is None else Outcome.OK)
                except BaseException as exc:
                    known = (
                        not dispatched
                        or streaming is None
                        and isinstance(exc, BusinessError)
                        and exc.code
                        in {
                            "model_not_configured",
                            "credential_missing",
                            "offline_model_denied",
                            "invalid_model_config",
                            "credential_busy",
                            "credential_closing",
                            "credential_changed",
                            "credential_store_unavailable",
                            "credential_decryption_failed",
                            "credential_backend_untrusted",
                            "redaction_capacity",
                            "invalid_credential_reference",
                            "invalid_credential_value",
                            "unsafe_vault",
                        }
                    )
                    if measured:
                        measured.result(
                            Outcome.CANCELLED
                            if known and isinstance(exc, asyncio.CancelledError)
                            else Outcome.ERROR
                            if known
                            else Outcome.UNKNOWN
                        )
                    raise
        except BaseException:
            await self._db.write(lambda u: u.settle_call(agent_id, identity, None, known_not_sent=known))
            raise
        finally:
            self._output.clear(agent_id, run_id, identity)
            if streaming is not None and not dispatched:
                await streaming.close()
        await self._db.write(lambda u: u.settle_call(agent_id, identity, reply))
        if reply.completion_reason in {"length", "content_filter"}:
            raise BusinessError(
                "model_response_incomplete",
                "Model response was truncated or filtered; usage was recorded and partial output was not published",
                status=502,
            )
        if reply.output_tokens is not None and reply.output_tokens > output:
            raise BusinessError(
                "model_output_limit_exceeded",
                "The provider exceeded the requested output budget; recorded costs remain settled",
            )
        return reply
