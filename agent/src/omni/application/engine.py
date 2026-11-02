"""One supervised runtime: admission, session FIFO, model/tool execution and shutdown."""

from __future__ import annotations

import asyncio
import json
import logging
from collections.abc import Callable
from dataclasses import dataclass

from jsonschema import Draft202012Validator

from ..domain.contracts import ActionResult, BusinessError, Effect, InvocationContext, Outcome, ToolSpec
from ..domain.settings import settings_value
from .ports import CapabilityPort, Clock, DatabasePort, ModelPort, Observer, Record

log = logging.getLogger("omni.v2.engine")


@dataclass(frozen=True)
class Capability:
    spec: ToolSpec
    adapter: CapabilityPort


class Application:
    """Public facade contains use cases, not infrastructure object attributes."""

    def __init__(
        self,
        database: DatabasePort,
        model: ModelPort,
        capabilities: tuple[Capability, ...],
        clock: Clock,
        observer: Observer,
        *,
        concurrency: int = 4,
        close_s: float = 30,
        workspace_validator: Callable[[str], None] | None = None,
    ) -> None:
        self._db, self._model, self._clock, self._observer = database, model, clock, observer
        self._tools = {cap.spec.name: cap for cap in capabilities}
        if len(self._tools) != len(capabilities):
            raise ValueError("Duplicate capability names")
        self._concurrency, self._close_s = concurrency, close_s
        self._workspace_validator = workspace_validator
        self._last_probe = 0.0
        self._active: dict[str, tuple[str, asyncio.Task]] = {}
        self._resources: dict[str, asyncio.Lock] = {}
        self._dispatcher: asyncio.Task | None = None
        self._wake = asyncio.Event()
        self._closing = False
        self._ready = False
        self._worker_error = ""
        self._fully_closed = False

    async def start(self) -> None:
        if self._dispatcher:
            raise BusinessError("already_started", "Application has already started")
        await self._db.write(lambda u: u.initialize())
        await self._db.write(lambda u: u.recover())
        self._dispatcher = asyncio.create_task(self._dispatch(), name="omni-dispatch")
        self._ready = True
        self._wake.set()
        log.info("Application started", extra={"event": "runtime.started"})

    async def submit_turn(self, agent_id: str, session_id: str | None, key: str, text: str) -> Record:
        if self._closing or not self._ready:
            raise BusinessError("not_ready", "Application is not accepting work", status=503)
        receipt = await self._db.write(lambda u: u.accept_turn(agent_id, session_id, key, text, {}))
        self._wake.set()
        return receipt

    async def _dispatch(self) -> None:
        while not self._closing:
            try:
                if self._clock.monotonic() - self._last_probe >= 2:
                    await self._db.write(lambda u: u.probe_write())
                    self._last_probe = self._clock.monotonic()
                agents = await self._db.read(lambda u: u.query("agents", ""))
                for agent in agents:
                    if not agent["enabled"]:
                        continue
                    queued = await self._db.read(lambda u: u.query("runs", str(agent["id"]), state="queued", limit=68))
                    for run in queued:
                        run_id, session_id = str(run["id"]), str(run["session_id"])
                        if len(self._active) >= self._concurrency:
                            break
                        if run_id in self._active or session_id in {sid for sid, _ in self._active.values()}:
                            continue
                        task = asyncio.create_task(self._run(str(agent["id"]), run_id), name="run-" + run_id)
                        self._active[run_id] = (session_id, task)
                        task.add_done_callback(lambda _task, rid=run_id: self._completed(rid))
                self._worker_error = ""
            except asyncio.CancelledError:
                raise
            except Exception:
                self._worker_error = "dispatcher_failed"
                log.exception(
                    "Dispatcher iteration failed", extra={"event": "worker.failed", "error_code": self._worker_error}
                )
            try:
                await asyncio.wait_for(self._wake.wait(), timeout=0.25)
            except TimeoutError:
                pass
            self._wake.clear()

    def _completed(self, run_id: str) -> None:
        item = self._active.pop(run_id, None)
        if item is not None and not item[1].cancelled():
            error = item[1].exception()
            if error:
                log.error(
                    "Run cleanup failed",
                    exc_info=(type(error), error, error.__traceback__),
                    extra={"event": "run.cleanup_failed"},
                )
        self._wake.set()

    async def _run(self, agent_id: str, run_id: str) -> None:
        with self._observer.context(agent_id=agent_id, run_id=run_id), self._observer.span("agent.turn"):
            try:
                await self._db.write(lambda u: u.transition(agent_id, run_id, "running"))
                run = await self._db.read(lambda u: u.get_run(agent_id, run_id))
                settings = settings_value(run["config_snapshot"])
                history = await self._db.read(lambda u: u.context_messages(agent_id, run_id))
                await self._db.write(lambda u: u.snapshot(agent_id, run_id, [str(m["id"]) for m in history], []))
                messages: list[Record] = [{"role": "system", "content": str(settings.get("persona", ""))}]
                messages.extend({"role": m["role"], "content": m["content"]} for m in history)
                tools = tuple(
                    c.spec for c in self._tools.values() if c.spec.name not in settings.get("disabled_tools", [])
                )
                if not settings.get("model"):
                    raise BusinessError(
                        "model_not_configured", "Choose a model before starting a conversation", status=422
                    )
                for _step in range(min(32, max(1, int(settings.get("max_steps", 12))))):
                    await self._checkpoint(agent_id, run_id)
                    call_id = await self._db.write(
                        lambda u: u.reserve_call(
                            agent_id,
                            run_id,
                            int(settings.get("call_reservation_micro", 10_000)),
                            int(settings.get("daily_budget_micro", 1_000_000)),
                            str(settings["model"]),
                        )
                    )
                    try:
                        with self._observer.span("model.complete"):
                            reply = await self._model.complete(messages, tools, settings)
                    except BaseException as exc:
                        known = isinstance(exc, BusinessError) and exc.code in {
                            "model_not_configured",
                            "credential_missing",
                            "offline_model_denied",
                            "invalid_model_config",
                        }
                        await self._db.write(lambda u: u.settle_call(agent_id, call_id, None, known_not_sent=known))
                        raise
                    await self._db.write(lambda u: u.settle_call(agent_id, call_id, reply))
                    await self._checkpoint(agent_id, run_id)
                    if not reply.calls:
                        await self._db.write(lambda u: u.finish(agent_id, run_id, reply.text))
                        log.info("Run completed", extra={"event": "run.succeeded"})
                        return
                    messages.append(
                        {
                            "role": "assistant",
                            "content": reply.text,
                            "tool_calls": [
                                {
                                    "id": call.id,
                                    "type": "function",
                                    "function": {"name": call.name, "arguments": json.dumps(call.arguments)},
                                }
                                for call in reply.calls
                            ],
                        }
                    )
                    for call in reply.calls:
                        await self._checkpoint(agent_id, run_id)
                        result = await self._execute(
                            agent_id, run_id, call.name, call.arguments, self._revision(run["config_revision"])
                        )
                        messages.append({"role": "tool", "tool_call_id": call.id, "content": result.text})
                        if result.effect == Effect.UNKNOWN:
                            raise BusinessError(
                                "action_outcome_unknown", "An external action requires manual verification"
                            )
                raise BusinessError("step_budget_exceeded", "The run reached its step limit")
            except asyncio.CancelledError:
                await self._end_error(agent_id, run_id, "cancelled", "run_cancelled")
                raise
            except BusinessError as exc:
                await self._end_error(agent_id, run_id, "failed", exc.code)
                log.warning("Run ended", extra={"event": "run.failed", "error_code": exc.code})
            except Exception:
                log.exception("Run failed", extra={"event": "run.failed", "error_code": "internal_error"})
                await self._end_error(agent_id, run_id, "failed", "internal_error")

    async def _end_error(self, agent_id: str, run_id: str, target: str, code: str) -> None:
        def end(u):
            run = u.get_run(agent_id, run_id)
            u.close_run_actions(agent_id, run_id)
            if run["state"] in {"queued", "running", "waiting_approval", "cancelling"}:
                state = (
                    "cancelled"
                    if run["state"] == "cancelling" or target == "cancelled"
                    else "interrupted"
                    if run["state"] == "waiting_approval"
                    else target
                )
                u.transition(agent_id, run_id, state, code)

        await self._db.write(end)

    @staticmethod
    def _revision(value: object) -> int:
        if type(value) is not int:
            raise BusinessError("invalid_revision", "Stored policy revision is invalid")
        return value

    async def _checkpoint(self, agent_id: str, run_id: str) -> None:
        run = await self._db.read(lambda u: u.get_run(agent_id, run_id))
        if self._closing or run["state"] in {"cancelling", "cancelled"}:
            raise asyncio.CancelledError

    async def _execute(self, agent_id: str, run_id: str, name: str, arguments: Record, policy: int) -> ActionResult:
        cap = self._tools.get(name)
        if cap is None:
            return ActionResult(Outcome.ERROR, "Unknown capability", error_code="tool_not_found")
        if not Draft202012Validator(cap.spec.parameters).is_valid(arguments):
            return ActionResult(
                Outcome.ERROR, "Tool arguments do not match its schema", error_code="invalid_tool_arguments"
            )
        action = await self._db.write(lambda u: u.prepare_action(agent_id, run_id, cap.spec, arguments, policy))
        action_id = str(action["id"])
        if cap.spec.writes:
            while True:
                await self._checkpoint(agent_id, run_id)
                current = await self._db.read(lambda u: u.action(agent_id, action_id))
                if current["state"] == "denied":
                    return ActionResult(Outcome.DENIED, "Owner denied this action", error_code="approval_denied")
                if current["state"] == "approved":
                    break
                if self._clock.now() >= str(action["created_at"]):
                    # Authorization has an absolute expiry, even if the approval UI disconnects.
                    pending = await self._db.read(lambda u: u.pending_approvals(agent_id))
                    if not any(a["action_id"] == action_id for a in pending):
                        raise BusinessError("approval_expired", "Action approval expired")
                await asyncio.sleep(0.1)
        lock = self._resources.setdefault(cap.spec.resource or name, asyncio.Lock())
        async with lock:
            await self._checkpoint(agent_id, run_id)
            await self._db.write(lambda u: u.authorize_action(agent_id, action_id))
            try:
                with self._observer.context(operation_id=action_id), self._observer.span("tool." + name):
                    run = await self._db.read(lambda u: u.get_run(agent_id, run_id))
                    context = InvocationContext(
                        agent_id,
                        run_id,
                        str(run["session_id"]),
                        policy,
                        settings_value(run["config_snapshot"])["workspace"],
                    )
                    result = await asyncio.wait_for(
                        cap.adapter.invoke(name, arguments, operation_id=action_id, context=context), cap.spec.timeout_s
                    )
                    if not isinstance(result, ActionResult):
                        raise TypeError("Capability must return ActionResult")
            except BaseException as exc:
                result = ActionResult(
                    Outcome.UNKNOWN if cap.spec.writes else Outcome.ERROR,
                    "Action outcome is unknown" if cap.spec.writes else "Capability failed",
                    Effect.UNKNOWN if cap.spec.writes else Effect.NONE,
                    error_code="action_interrupted" if isinstance(exc, asyncio.CancelledError) else "capability_failed",
                )
                await self._db.write(lambda u: u.settle_action(agent_id, action_id, result))
                if isinstance(exc, asyncio.CancelledError):
                    raise
                return result
            await self._db.write(lambda u: u.settle_action(agent_id, action_id, result))
            return result

    async def query(
        self, kind: str, agent_id: str, *, after: int = 0, limit: int = 100, **filters: str
    ) -> list[Record]:
        return await self._db.read(lambda u: u.query(kind, agent_id, after=after, limit=limit, **filters))

    async def settings(self, agent_id: str) -> Record:
        return await self._db.read(lambda u: u.settings(agent_id))

    async def save_settings(self, agent_id: str, value: Record, revision: int) -> Record:
        checked = settings_value(value)
        if self._workspace_validator:
            self._workspace_validator(checked["workspace"])
        return await self._db.write(lambda u: u.settings_put(agent_id, value, revision))

    async def get_run(self, agent_id: str, run_id: str) -> Record:
        return await self._db.read(lambda u: u.get_run(agent_id, run_id))

    async def messages(self, agent_id: str, session_id: str) -> list[Record]:
        return await self._db.read(lambda u: u.messages(agent_id, session_id))

    async def approvals(self, agent_id: str) -> list[Record]:
        return await self._db.read(lambda u: u.pending_approvals(agent_id))

    async def approve(self, agent_id: str, approval_id: str, allowed: bool, revision: int) -> None:
        await self._db.write(lambda u: u.resolve_approval(agent_id, approval_id, allowed, revision))

    async def cancel(self, agent_id: str, run_id: str) -> None:
        await self._db.write(lambda u: u.cancel(agent_id, run_id))
        item = self._active.get(run_id)
        if item:
            item[1].cancel()

    async def remember(self, agent_id: str, content: str, *, message_id: str | None = None, quote: str = "") -> Record:
        return await self._db.write(lambda u: u.remember(agent_id, content, message_id=message_id, quote=quote))

    async def forget(self, agent_id: str, memory_id: str) -> None:
        await self._db.write(lambda u: u.forget(agent_id, memory_id))

    async def search_memory(self, agent_id: str, query: str) -> list[Record]:
        return await self._db.read(lambda u: u.search_memory(agent_id, query))

    def health(self) -> Record:
        return {
            "ready": self._ready and not self._closing and not self._worker_error,
            "closing": self._closing,
            "active_runs": len(self._active),
            "error_code": self._worker_error,
        }

    async def close(self) -> Record:
        if self._fully_closed:
            return {"complete": True, "residual": []}
        self._closing, self._ready = True, False
        self._wake.set()
        if self._dispatcher:
            await self._dispatcher
        await self._db.write(lambda u: u.cancel_approvals())
        tasks = [task for _, task in self._active.values()]
        for task in tasks:
            task.cancel()
        remaining = self._close_s
        started = self._clock.monotonic()
        pending = set()
        if tasks:
            _, pending = await asyncio.wait(tasks, timeout=remaining)
        remaining = max(0, remaining - (self._clock.monotonic() - started))
        adapters = {id(c.adapter): c.adapter for c in self._tools.values()}
        residual = []
        for adapter in adapters.values():
            try:
                async with asyncio.timeout(remaining):
                    if not await adapter.close(remaining):
                        residual.append(type(adapter).__name__)
            except Exception as exc:  # noqa: BLE001 -- shutdown reports every residual component
                log.error(
                    "Capability shutdown failed",
                    extra={"event": "worker.close_failed", "error_code": type(exc).__name__},
                )
                residual.append(type(adapter).__name__)
            remaining = max(0, self._close_s - (self._clock.monotonic() - started))
        residual.extend(task.get_name() for task in pending)
        if not residual:
            await self._db.close()
            self._fully_closed = True
        return {"complete": not residual, "residual": residual}
