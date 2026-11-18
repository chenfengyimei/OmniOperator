"""One supervised runtime: admission, session FIFO, model/tool execution and shutdown."""

from __future__ import annotations

import asyncio
import json
import logging
from collections.abc import Callable, Mapping
from dataclasses import dataclass
from typing import cast

from jsonschema import Draft202012Validator

from ..domain.agents import MAX_AGENTS, name_value, revision_value
from ..domain.contracts import JSON, ActionResult, BusinessError, Effect, InvocationContext, Outcome, ToolSpec
from ..domain.routing import REQUEST_MODE, select_tools
from ..domain.routing import notice as route_notice
from ..domain.settings import agent_settings_value, settings_value
from ..domain.todos import TODO_LIST
from ..domain.turn_prompt import TurnPrompt
from .context import ContextService
from .images import ImageService
from .memory_semantics import MemorySemanticService
from .model_calls import ModelCalls
from .ports import (
    CapabilityPort,
    Clock,
    DatabasePort,
    ExtensionCapabilityPort,
    MCPCapabilityPort,
    Observer,
    Record,
    UnitOfWork,
)
from .skills import SkillService
from .todos import TodoService

log = logging.getLogger("omni.v2.engine")


@dataclass(frozen=True)
class Capability:
    spec: ToolSpec
    adapter: CapabilityPort
    quiescent: Callable[[], bool] | None = None


class Application:
    """Public facade contains use cases, not infrastructure object attributes."""

    def __init__(
        self,
        database: DatabasePort,
        calls: ModelCalls,
        contexts: ContextService,
        capabilities: tuple[Capability, ...],
        clock: Clock,
        observer: Observer,
        *,
        concurrency: int = 4,
        close_s: float = 30,
        workspace_validator: Callable[[str], None] | None = None,
        delivery_cancellation: Callable[[list[str]], None] | None = None,
        extensions: ExtensionCapabilityPort | None = None,
        mcp: MCPCapabilityPort | None = None,
        owner_capabilities: tuple[Capability, ...] = (),
        images: ImageService | None = None,
        memory_semantics: MemorySemanticService | None = None,
        skills: SkillService | None = None,
        todos: TodoService | None = None,
    ) -> None:
        self._db, self._calls, self._contexts, self._clock, self._observer = database, calls, contexts, clock, observer
        self._tools = {cap.spec.name: cap for cap in capabilities}
        self._owner_tools = {cap.spec.name: cap for cap in owner_capabilities}
        if self._tools.keys() & self._owner_tools.keys() or len(self._owner_tools) != len(owner_capabilities):
            raise ValueError("Owner capabilities require unique names")
        if len(self._tools) != len(capabilities):
            raise ValueError("Duplicate capability names")
        self._concurrency, self._close_s = concurrency, close_s
        self._workspace_validator = workspace_validator
        self._delivery_cancellation = delivery_cancellation
        self._extensions = extensions
        self._mcp = mcp
        self._images = images
        self._memory_semantics = memory_semantics
        self._skills = skills
        self._todos = todos
        if todos is not None and TODO_LIST.name in self._tools:
            raise ValueError("Task-plan control name is reserved")
        self._last_probe = 0.0
        self._active: dict[str, tuple[str, asyncio.Task[None]]] = {}
        self._resources: dict[str, asyncio.Lock] = {}
        self._dispatcher: asyncio.Task[None] | None = None
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

    async def submit_turn(
        self,
        agent_id: str,
        session_id: str | None,
        key: str,
        text: str,
        image_ids: tuple[str, ...] = (),
        mode: str | None = None,
        skill_ids: tuple[str, ...] = (),
    ) -> Record:
        if self._closing or not self._ready:
            raise BusinessError("not_ready", "Application is not accepting work", status=503)
        receipt = await self._db.write(
            lambda u: u.accept_turn(
                agent_id,
                session_id,
                key,
                text,
                {},
                image_ids=image_ids,
                mode=mode,
                skill_ids=skill_ids,
                skill_platform=self._skills.platform if self._skills else "",
            )
        )
        self._wake.set()
        return receipt

    def notify(self) -> None:
        self._wake.set()

    def action_review_blockers(self, record: Record) -> list[str]:
        """Observe actual execution ownership; a terminal database row alone is insufficient."""
        blockers: list[str] = []
        item = self._active.get(str(record["run_id"]))
        if item is not None and not item[1].done():
            blockers.append("action_run_active")
        proof = record.get("proof")
        source = proof.get("source") if isinstance(proof, dict) else None
        if isinstance(source, dict) and source.get("kind") == "plugin":
            if self._extensions is None or not self._extensions.review_quiescent(
                str(record["agent_id"]), str(source["extension_id"]), self._revision(source["authority_epoch"])
            ):
                blockers.append("action_worker_active")
        elif str(record["tool"]).startswith("mcp__"):
            if self._mcp is None or not self._mcp.quiescent():
                blockers.append("action_worker_active")
        else:
            cap = self._tools.get(str(record["tool"])) or self._owner_tools.get(str(record["tool"]))
            if cap is None or cap.quiescent is None:
                blockers.append("action_executor_unverified")
            elif not cap.quiescent():
                blockers.append("action_worker_active")
        return blockers

    async def _dispatch(self) -> None:
        while not self._closing:
            try:
                if self._clock.monotonic() - self._last_probe >= 2:
                    await self._db.write(lambda u: u.probe_write())
                    self._last_probe = self._clock.monotonic()
                await self._db.write(lambda u: u.renew_run_leases())
                claims = await self._db.write(lambda u: u.claim_runs(self._concurrency - len(self._active)))
                for run in claims:
                    run_id, session_id = str(run["id"]), str(run["session_id"])
                    task = asyncio.create_task(
                        self._run(
                            str(run["agent_id"]), run_id, str(run["claim_token"]), self._revision(run["lease_fence"])
                        ),
                        name="run-" + run_id,
                    )
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

    async def _run(self, agent_id: str, run_id: str, token: str, fence: int) -> None:
        with self._observer.context(agent_id=agent_id, run_id=run_id), self._observer.span("agent.turn") as measured:
            if measured:
                measured.result(Outcome.ERROR)
            try:

                def start(u: UnitOfWork) -> None:
                    u.start_claimed_run(agent_id, run_id, token, fence)
                    u.transition(agent_id, run_id, "running")

                await self._db.write(start)
                run = await self._db.read(lambda u: u.get_run(agent_id, run_id))
                command = await self._db.read(lambda u: u.owner_command(agent_id, run_id))
                if command is not None:
                    await self._checkpoint(agent_id, run_id)
                    arguments = command["arguments"]
                    if not isinstance(arguments, dict):
                        raise BusinessError("owner_command_expired", "Owner command was consumed or revoked")
                    result = await self._execute(
                        agent_id,
                        run_id,
                        str(command["tool"]),
                        arguments,
                        self._revision(run["policy_epoch"]),
                        registry=self._owner_tools,
                    )
                    if result.effect == Effect.UNKNOWN:
                        raise BusinessError("action_outcome_unknown", "Owner action requires independent verification")
                    if result.status != Outcome.OK:
                        raise BusinessError(result.error_code or "owner_command_failed", result.text)
                    await self._db.write(lambda u: u.finish(agent_id, run_id, result.text))
                    if measured:
                        measured.result(Outcome.OK)
                    return
                settings = settings_value(await self._db.read(lambda u: u.run_settings(agent_id, run_id)))
                remote = await self._db.read(lambda u: u.qq_run(agent_id, run_id))
                registry = {} if remote else dict(self._tools)
                if self._todos is not None and not remote:
                    registry[TODO_LIST.name] = Capability(TODO_LIST, self._todos)
                if self._extensions is not None and not remote:
                    for spec in await self._extensions.tools_for_run(agent_id, run_id):
                        if spec.name in registry or spec.name == "request_mode":
                            raise BusinessError(
                                "extension_tool_collision", "Extension name conflicts with a registered capability"
                            )
                        registry[spec.name] = Capability(spec, self._extensions)
                if self._mcp is not None and not remote:
                    for mcp_spec in await self._mcp.tools_for_run(agent_id, run_id):
                        if mcp_spec.name in registry or mcp_spec.name == "request_mode":
                            raise BusinessError(
                                "mcp_tool_collision", "MCP tool name conflicts with a registered capability"
                            )
                        registry[mcp_spec.name] = Capability(mcp_spec, self._mcp)
                baseline = tuple(c.spec for c in registry.values() if c.spec.name not in settings["disabled_tools"])
                route = await self._db.read(lambda u: u.run_route(agent_id, run_id))
                may_promote = bool(route["allow_escalation"] and not route["promoted"])
                tools = select_tools(str(route["mode"]), baseline, escalate=may_promote)
                initial_notice = route_notice(
                    str(route["mode"]), any(spec.name == "request_mode" for spec in tools)
                ) + (await self._skills.notice(agent_id, run_id) if self._skills and not remote else "")
                todo_notice = await self._todo_notice(agent_id, run_id, tools)
                prepared = await self._contexts.prepare(
                    agent_id, run_id, settings, tools, extra_notice=initial_notice, tail_notice=todo_notice
                )
                messages: list[Record] = cast(list[Record], prepared["request"])
                notice = str(prepared["notice"])
                prompt = TurnPrompt.capture(messages, notice + initial_notice + todo_notice)
                for _step in range(min(32, max(1, int(settings.get("max_steps", 12))))):
                    await self._checkpoint(agent_id, run_id)
                    route = await self._db.read(lambda u: u.run_route(agent_id, run_id))
                    may_promote = bool(route["allow_escalation"] and not route["promoted"])
                    tools = select_tools(str(route["mode"]), baseline, escalate=may_promote)
                    batch_registry = {spec.name: registry[spec.name] for spec in tools if spec.name in registry}
                    await self._db.read(lambda u: u.context_memories(agent_id, str(prepared["snapshot_id"])))
                    prompt.update(
                        messages,
                        notice
                        + route_notice(str(route["mode"]), any(spec.name == "request_mode" for spec in tools))
                        + (await self._skills.notice(agent_id, run_id) if self._skills and not remote else "")
                        + await self._todo_notice(agent_id, run_id, tools),
                    )
                    reply = await self._calls.invoke(
                        agent_id, run_id, messages, tools, settings, baseline_tools=baseline
                    )
                    await self._checkpoint(agent_id, run_id)
                    if not reply.calls:
                        await self._db.write(lambda u: u.finish(agent_id, run_id, reply.text))
                        if measured:
                            measured.result(Outcome.OK)
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
                        if call.name == "request_mode":
                            validator = Draft202012Validator(REQUEST_MODE.parameters)
                            check = cast(Callable[[object], bool], getattr(validator, "is_valid"))
                            if not check(call.arguments):
                                result = ActionResult(
                                    Outcome.ERROR, "Invalid tier control arguments", error_code="invalid_tool_arguments"
                                )
                            else:
                                try:
                                    promoted = await self._db.write(
                                        lambda u: u.route_promote(agent_id, run_id, str(call.arguments["mode"]))
                                    )
                                    result = ActionResult(
                                        Outcome.OK,
                                        f"Next call uses {promoted['mode']} tools. Captured model and authorization are unchanged.",
                                    )
                                except BusinessError as exc:
                                    if exc.code not in {
                                        "mode_escalation_disabled",
                                        "mode_escalation_exhausted",
                                        "mode_escalation_not_upward",
                                        "mode_escalation_not_offered",
                                    }:
                                        raise
                                    result = ActionResult(
                                        Outcome.ERROR,
                                        f"Tier request rejected: {exc.code}. No model or permission change occurred.",
                                        error_code=exc.code,
                                    )
                        elif call.name == TODO_LIST.name and call.name in batch_registry and self._todos is not None:
                            # Planning is an internal control like request_mode: no external
                            # action, human approval or executable capability is created.
                            result = await self._todos.invoke(
                                call.name,
                                call.arguments,
                                operation_id=run_id,
                                context=InvocationContext(
                                    agent_id, run_id, str(run["session_id"]), self._revision(run["policy_epoch"])
                                ),
                            )
                        elif call.name in registry and call.name not in batch_registry:
                            result = ActionResult(
                                Outcome.DENIED,
                                "Tool is outside this call's tier. A tier request only changes the next call; permissions remain separate.",
                                error_code="tool_outside_mode",
                            )
                        else:
                            result = await self._execute(
                                agent_id,
                                run_id,
                                call.name,
                                call.arguments,
                                self._revision(run["policy_epoch"]),
                                registry=batch_registry,
                            )
                        messages.append({"role": "tool", "tool_call_id": call.id, "content": result.text})
                        if result.effect == Effect.UNKNOWN:
                            raise BusinessError(
                                "action_outcome_unknown", "An external action requires manual verification"
                            )
                raise BusinessError("step_budget_exceeded", "The run reached its step limit")
            except asyncio.CancelledError:
                if measured:
                    measured.result(Outcome.CANCELLED)
                await self._end_error(agent_id, run_id, "cancelled", "run_cancelled")
                raise
            except BusinessError as exc:
                if measured:
                    measured.result(Outcome.UNKNOWN if exc.code == "action_outcome_unknown" else Outcome.ERROR)
                await self._end_error(agent_id, run_id, "failed", exc.code)
                log.warning("Run ended", extra={"event": "run.failed", "error_code": exc.code})
            except Exception:
                log.exception("Run failed", extra={"event": "run.failed", "error_code": "internal_error"})
                await self._end_error(agent_id, run_id, "failed", "internal_error")

    async def _todo_notice(self, agent_id: str, run_id: str, tools: tuple[ToolSpec, ...]) -> str:
        return (
            await self._todos.notice(agent_id, run_id)
            if self._todos and any(t.name == TODO_LIST.name for t in tools)
            else ""
        )

    async def session_todos(self, agent_id: str, session_id: str) -> Record:
        return await self._db.read(lambda u: u.todo_get(agent_id, session_id))

    async def _end_error(self, agent_id: str, run_id: str, target: str, code: str) -> None:
        def end(u: UnitOfWork) -> None:
            run = u.get_run(agent_id, run_id)
            u.context_failure(agent_id, run_id, code)
            unresolved = u.close_run_actions(agent_id, run_id)
            if run["state"] in {"queued", "running", "waiting_approval", "cancelling"}:
                state = (
                    "cancelled"
                    if run["state"] == "cancelling" or target == "cancelled"
                    else "interrupted"
                    if run["state"] in {"queued", "waiting_approval"}
                    else target
                )
                u.transition(agent_id, run_id, state, code + "_with_unknown_effect" if unresolved else code)

        await self._db.write(end)

    @staticmethod
    def _revision(value: object) -> int:
        if type(value) is not int:
            raise BusinessError("invalid_revision", "Stored policy revision is invalid")
        return value

    async def _checkpoint(self, agent_id: str, run_id: str) -> None:
        await self._db.read(lambda u: u.assert_run_authorized(agent_id, run_id))
        run = await self._db.read(lambda u: u.get_run(agent_id, run_id))
        if self._closing or run["state"] in {"cancelling", "cancelled"}:
            raise asyncio.CancelledError

    async def _execute(
        self,
        agent_id: str,
        run_id: str,
        name: str,
        arguments: Record,
        policy: int,
        *,
        registry: Mapping[str, Capability] | None = None,
    ) -> ActionResult:
        cap = (registry if registry is not None else self._tools).get(name)
        if cap is None:
            return ActionResult(Outcome.ERROR, "Unknown capability", error_code="tool_not_found")
        validator = Draft202012Validator(cap.spec.parameters)
        check = cast(Callable[[object], bool], getattr(validator, "is_valid"))
        if not check(arguments):
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
                if current["state"] == "cancelled":
                    return ActionResult(Outcome.DENIED, "Action authorization was revoked", error_code="action_revoked")
                if current["state"] == "approved":
                    break
                # Read action state and approval expiry from one snapshot. A
                # separate pending-list query races with the owner's approval.
                if current["approval_state"] != "pending" or str(current["approval_expires_at"]) <= self._clock.now():
                    raise BusinessError("approval_expired", "Action approval expired")
                await asyncio.sleep(0.1)
        lock = self._resources.setdefault(cap.spec.resource or name, asyncio.Lock())
        async with lock:
            await self._checkpoint(agent_id, run_id)
            await self._db.write(lambda u: u.authorize_action(agent_id, action_id))
            try:
                with self._observer.context(operation_id=action_id), self._observer.span("tool." + name) as measured:
                    if measured:
                        measured.result(Outcome.UNKNOWN if cap.spec.writes else Outcome.ERROR)
                    run = await self._db.read(lambda u: u.get_run(agent_id, run_id))
                    context = InvocationContext(
                        agent_id,
                        run_id,
                        str(run["session_id"]),
                        policy,
                        str((await self._db.read(lambda u: u.run_settings(agent_id, run_id)))["workspace"]),
                    )
                    result = await asyncio.wait_for(
                        cap.adapter.invoke(name, arguments, operation_id=action_id, context=context), cap.spec.timeout_s
                    )
                    if not isinstance(cast(object, result), ActionResult):
                        raise TypeError("Capability must return ActionResult")
                    if measured:
                        measured.result(result.status)
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

    async def get_agent(self, agent_id: str) -> Record:
        return await self._db.read(lambda u: u.get_agent(agent_id))

    async def create_agent(self, name: str) -> Record:
        if self._closing or not self._ready:
            raise BusinessError("not_ready", "Application is not accepting work", status=503)
        checked = name_value(name)

        def create(uow: UnitOfWork) -> Record:
            # Admission and initialization share the writer transaction: two
            # owner clients cannot race past the bounded space inventory.
            if len(uow.query("agents", "", limit=MAX_AGENTS)) >= MAX_AGENTS:
                raise BusinessError("agent_capacity", "This instance has reached its 64 Agent spaces", status=409)
            record = uow.create_agent(checked)
            uow.audit(str(record["id"]), "agent.created", None, {"revision": 1})
            return record

        return await self._db.write(create)

    async def rename_agent(self, agent_id: str, name: str, revision: int) -> Record:
        if self._closing or not self._ready:
            raise BusinessError("not_ready", "Application is not accepting work", status=503)
        checked, expected = name_value(name), revision_value(revision)
        return await self._db.write(lambda u: u.rename_agent(agent_id, checked, expected))

    async def history(
        self, kind: str, agent_id: str, *, cursor: str = "", limit: int = 100, run_id: str = ""
    ) -> Record:
        if kind not in {"audit", "events"}:
            raise BusinessError("invalid_history_query", "Agent history stream is invalid", status=422)
        return await self._db.read(lambda u: u.history_page(kind, agent_id, cursor=cursor, limit=limit, run_id=run_id))

    async def save_settings(self, agent_id: str, value: Record, revision: int) -> Record:
        checked = agent_settings_value(value)
        if self._workspace_validator:
            self._workspace_validator(checked["workspace"])
        return await self._db.write(lambda u: u.settings_put(agent_id, value, revision))

    async def run_configuration(self, agent_id: str, run_id: str) -> Record:
        return await self._db.read(lambda u: u.run_settings(agent_id, run_id))

    async def get_run(self, agent_id: str, run_id: str) -> Record:
        return await self._db.read(lambda u: u.get_run(agent_id, run_id))

    async def live_output(self, agent_id: str, run_id: str) -> Record:
        return await self._calls.live_output(agent_id, run_id)

    async def pin_run(self, agent_id: str, run_id: str, pinned: bool, revision: int) -> Record:
        return await self._db.write(lambda u: u.retention_pin(agent_id, run_id, pinned, revision))

    async def run_retention(self, agent_id: str, run_id: str) -> Record:
        return await self._db.read(lambda u: u.retention_inspect(agent_id, run_id))

    async def audit_retention_preview(
        self, agent_id: str, *, cursor: str = "", limit: int = 20, as_of: str = ""
    ) -> Record:
        return await self._db.read(
            lambda u: u.audit_retention_preview(agent_id, cursor=cursor, limit=limit, as_of=as_of)
        )

    async def event_retention_preview(
        self, agent_id: str, *, cursor: str = "", limit: int = 20, as_of: str = ""
    ) -> Record:
        return await self._db.read(
            lambda u: u.event_retention_preview(agent_id, cursor=cursor, limit=limit, as_of=as_of)
        )

    async def messages(self, agent_id: str, session_id: str) -> list[Record]:
        rows = await self._db.read(lambda u: u.messages(agent_id, session_id))
        if self._images is not None:
            return cast(list[Record], await self._images.messages(agent_id, cast(list[dict[str, object]], rows)))
        return rows

    async def approvals(self, agent_id: str) -> list[Record]:
        return await self._db.read(lambda u: u.pending_approvals(agent_id))

    async def approve(self, agent_id: str, approval_id: str, allowed: bool, revision: int) -> None:
        await self._db.write(lambda u: u.resolve_approval(agent_id, approval_id, allowed, revision))

    async def cancel(self, agent_id: str, run_id: str) -> None:
        def cancel(u: UnitOfWork) -> list[str]:
            u.cancel(agent_id, run_id)
            return u.delivery_cancel_run(agent_id, run_id)

        outbound = await self._db.write(cancel)
        if self._delivery_cancellation:
            self._delivery_cancellation(outbound)
        item = self._active.get(run_id)
        if item:
            item[1].cancel()

    async def remember(
        self,
        agent_id: str,
        content: str,
        *,
        message_id: str | None = None,
        quote: str = "",
        action_id: str | None = None,
        document_revision_id: str | None = None,
    ) -> Record:
        return await self._db.write(
            lambda u: u.remember(
                agent_id,
                content,
                message_id=message_id,
                quote=quote,
                action_id=action_id,
                document_revision_id=document_revision_id,
            )
        )

    async def forget(self, agent_id: str, memory_id: str, revision: int | None = None) -> None:
        await self._db.write(lambda u: u.forget(agent_id, memory_id, revision))

    async def context(self, agent_id: str, run_id: str) -> list[Record]:
        return await self._db.read(lambda u: u.run_context(agent_id, run_id))

    async def context_report(self, agent_id: str, run_id: str) -> Record:
        def read(u: UnitOfWork) -> Record:
            return {
                **u.run_context_report(agent_id, run_id),
                "items": cast(JSON, u.run_context(agent_id, run_id)),
            }

        return await self._db.read(read)

    async def session_context_overview(self, agent_id: str, session_id: str) -> Record:
        return await self._db.read(lambda u: u.session_context_overview(agent_id, session_id))

    async def summary(self, agent_id: str, summary_id: str) -> Record:
        return await self._db.read(lambda u: u.summary_get(agent_id, summary_id))

    async def erase_message(self, agent_id: str, message_id: str) -> None:
        await self._db.write(lambda u: u.erase_message(agent_id, message_id))

    async def search_memory(self, agent_id: str, query: str) -> list[Record]:
        if self._memory_semantics is not None:
            return (await self._memory_semantics.search(agent_id, query))["items"]
        return await self._db.read(lambda u: u.search_memory(agent_id, query))

    def health(self) -> Record:
        return {
            "ready": self._ready and not self._closing and not self._worker_error,
            "closing": self._closing,
            "active_runs": len(self._active),
            "error_code": self._worker_error,
            "model_streaming": self._calls.health(),
        }

    def stop_accepting(self) -> None:
        self._closing, self._ready = True, False
        self._wake.set()

    async def close(self, timeout_s: float | None = None, *, close_database: bool = True) -> Record:
        if self._fully_closed:
            return {"complete": True, "residual": cast(JSON, [])}
        self.stop_accepting()
        budget = self._close_s if timeout_s is None else max(0, timeout_s)
        started = asyncio.get_running_loop().time()
        if self._dispatcher:
            await self._dispatcher
        await self._db.write(lambda u: u.cancel_approvals())
        tasks = [task for _, task in self._active.values()]
        for task in tasks:
            task.cancel()
        remaining = max(0, budget - (asyncio.get_running_loop().time() - started))
        pending: set[asyncio.Task[None]] = set()
        if tasks:
            _, pending = await asyncio.wait(tasks, timeout=remaining)
        remaining = max(0, budget - (asyncio.get_running_loop().time() - started))
        adapters = {id(c.adapter): c.adapter for c in (*self._tools.values(), *self._owner_tools.values())}
        residual: list[str] = []
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
            remaining = max(0, budget - (asyncio.get_running_loop().time() - started))
        residual.extend(task.get_name() for task in pending)
        if not residual:
            if close_database:
                await self._db.close()
            self._fully_closed = True
        return {"complete": not residual, "residual": cast(JSON, residual)}
