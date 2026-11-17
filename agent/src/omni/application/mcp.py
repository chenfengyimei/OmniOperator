"""Owner MCP servers: explicit connect, bounded catalog, per-tool authorization and run-scoped calls.

The service orchestrates database transactions, the owned transport sessions and
the model-visible result contract. It never resolves a credential itself and
never puts external payloads, headers or exception text into a model result.
"""

from __future__ import annotations

import asyncio
import logging
from collections.abc import Awaitable, Callable, Mapping, Sequence
from dataclasses import dataclass
from typing import Protocol, TypeVar, cast

from ..domain.contracts import JSON, ActionResult, BusinessError, Effect, InvocationContext, Outcome, ToolSpec
from ..domain.mcp import (
    DEFAULT_CALL_TIMEOUT_S,
    MCPDispatchError,
    MCPUncertain,
    ServerTarget,
    argument_digest,
    call_state,
    catalog_rows,
    result_text,
    server_target,
    tool_spec,
)
from .ports import Clock, DatabasePort, Record

log = logging.getLogger("omni.v2.mcp")
T = TypeVar("T")
MAX_PENDING_OPERATIONS = 64


@dataclass
class _Invocation:
    task: asyncio.Task[object] | None = None
    cancelled: bool = False
    interruptible: bool = False

    def cancel(self) -> None:
        self.cancelled = True
        if self.interruptible and self.task is not None and not self.task.done() and not self.task.cancelling():
            self.task.cancel()

    def check(self) -> None:
        if self.cancelled:
            raise asyncio.CancelledError


class MCPTransportPort(Protocol):
    async def connect(self, target: ServerTarget) -> Record: ...

    async def catalog(self, target: ServerTarget) -> list[Record]: ...

    async def call(
        self,
        target: ServerTarget,
        name: str,
        arguments: dict[str, JSON],
        timeout_s: float,
        *,
        before_send: Callable[[], Awaitable[None]] | None = None,
    ) -> Record: ...

    async def stop(self, agent_id: str, server_id: str, timeout_s: float = 5) -> bool: ...

    async def oauth_start(self, target: ServerTarget, redirect_uri: str) -> Record: ...

    async def oauth_callback(self, state: str, code: str) -> Record: ...

    def health(self) -> Record: ...

    def stop_accepting(self) -> None: ...

    async def close(self, timeout_s: float) -> bool: ...


def _target(record: Record, binding: Record | None = None) -> ServerTarget:
    merged = dict(record)
    if binding is not None:
        merged["authority_epoch"] = binding["authority_epoch"]
    return server_target(merged)


class MCPService:
    def __init__(self, database: DatabasePort, transports: MCPTransportPort, clock: Clock) -> None:
        self._db, self._transports, self._clock = database, transports, clock
        self._closing = False
        self._pending: set[asyncio.Task[object]] = set()
        self._invocations: dict[asyncio.Task[object], _Invocation] = {}
        self._transport_close: asyncio.Task[bool] | None = None

    def _admit(self, operation: Callable[[], Awaitable[T]], invocation: _Invocation | None = None) -> asyncio.Task[T]:
        if self._closing:
            raise BusinessError("mcp_closing", "MCP admission is draining", status=503)
        if len(self._pending) >= MAX_PENDING_OPERATIONS:
            raise BusinessError("mcp_busy", "MCP business operations are bounded", retryable=True, status=503)

        async def perform() -> T:
            return await operation()

        task = asyncio.create_task(perform(), name="mcp-business-operation")
        owned = cast(asyncio.Task[object], task)
        self._pending.add(owned)
        if invocation is not None:
            invocation.task = owned
            self._invocations[owned] = invocation

        def complete(future: asyncio.Task[T]) -> None:
            self._pending.discard(owned)
            self._invocations.pop(owned, None)
            if not future.cancelled():
                future.exception()

        task.add_done_callback(complete)
        return task

    async def _wait(self, task: asyncio.Task[T], invocation: _Invocation | None = None) -> T:
        try:
            return await asyncio.shield(task)
        except asyncio.CancelledError:
            if invocation is not None:
                invocation.cancel()
            raise

    async def _execute(self, operation: Callable[[], Awaitable[T]]) -> T:
        return await self._wait(self._admit(operation))

    # ---------- owner surfaces ----------

    async def servers(self, agent_id: str) -> Record:
        return await self._execute(lambda: self._servers(agent_id))

    async def _servers(self, agent_id: str) -> Record:
        return {
            "items": cast(JSON, await self._db.read(lambda u: u.mcp_server_overview(agent_id))),
            "runtime": cast(JSON, self._transports.health()),
        }

    async def server(self, agent_id: str, config_id: str) -> Record:
        return await self._execute(lambda: self._server(agent_id, config_id))

    async def _server(self, agent_id: str, config_id: str) -> Record:
        return await self._db.read(lambda u: u.mcp_server_get(agent_id, config_id))

    async def connect(self, agent_id: str, config_id: str, revision: int) -> Record:
        return await self._execute(lambda: self._connect(agent_id, config_id, revision))

    async def _connect(self, agent_id: str, config_id: str, revision: int) -> Record:
        if self._closing:
            raise BusinessError("mcp_closing", "MCP admission is draining", status=503)
        begin = await self._db.write(lambda u: u.mcp_server_begin(agent_id, config_id, revision))
        target = _target(begin, cast(Record, begin["server"]))
        try:
            handshake = await self._transports.connect(target)
            raw = await self._transports.catalog(target)
            tools = [cast(Record, row) for row in catalog_rows(raw, str(begin["name"]))]
            committed = await self._db.write(
                lambda u: u.mcp_catalog_commit(agent_id, config_id, str(begin["revision_id"]), tools)
            )
        except (MCPDispatchError, BusinessError) as exc:
            code = exc.code
            await self._transports.stop(agent_id, config_id, 5)
            await self._db.write(lambda u: u.mcp_server_failed(agent_id, config_id, code))
            log.warning("MCP connect failed", extra={"event": "mcp.connect_failed", "error_code": code})
            return {**await self._db.read(lambda u: u.mcp_server_get(agent_id, config_id)), "connected": False}
        except BaseException:
            await self._transports.stop(agent_id, config_id, 5)
            await self._db.write(lambda u: u.mcp_server_failed(agent_id, config_id, "mcp_connect_failed"))
            raise
        view = await self._db.read(lambda u: u.mcp_server_get(agent_id, config_id))
        return {
            **view,
            "connected": True,
            "drift": committed["drift"],
            "revoked": committed["revoked"],
            "handshake": handshake,
            "tools": [
                {"tool_id": None, "name": tool["name"], "exposed_name": tool["exposed_name"]}
                for tool in cast(list[Record], committed["tools"])
            ],
        }

    async def disconnect(self, agent_id: str, config_id: str) -> Record:
        return await self._execute(lambda: self._disconnect(agent_id, config_id))

    async def _disconnect(self, agent_id: str, config_id: str) -> Record:
        await self._db.read(lambda u: u.mcp_server_get(agent_id, config_id))
        reclaimed = await self._transports.stop(agent_id, config_id, 5)
        view = await self._db.write(
            lambda u: u.mcp_server_stopped(agent_id, config_id, "" if reclaimed else "mcp_cleanup_pending")
        )
        return {**view, "reclaimed": reclaimed}

    async def reset(self, agent_id: str, config_id: str) -> Record:
        return await self._execute(lambda: self._reset(agent_id, config_id))

    async def _reset(self, agent_id: str, config_id: str) -> Record:
        await self._db.read(lambda u: u.mcp_server_get(agent_id, config_id))
        reclaimed = await self._transports.stop(agent_id, config_id, 5)
        view = await self._db.write(lambda u: u.mcp_server_reset(agent_id, config_id))
        return {**view, "reclaimed": reclaimed}

    async def catalog(self, agent_id: str, config_id: str, after: int = -1, limit: int = 20) -> Record:
        return await self._execute(lambda: self._catalog(agent_id, config_id, after, limit))

    async def _catalog(self, agent_id: str, config_id: str, after: int = -1, limit: int = 20) -> Record:
        return await self._db.read(lambda u: u.mcp_catalog_page(agent_id, config_id, after, limit))

    async def authorize(self, agent_id: str, config_id: str, request: Record) -> Record:
        return await self._execute(lambda: self._authorize(agent_id, config_id, request))

    async def _authorize(self, agent_id: str, config_id: str, request: Record) -> Record:
        return await self._db.write(lambda u: u.mcp_authorize(agent_id, config_id, request))

    async def authorize_tool(
        self,
        agent_id: str,
        config_id: str,
        tool_id: str,
        *,
        authorized: bool,
        read_only: bool,
        catalog_fingerprint: str,
        evidence: str,
    ) -> Record:
        return await self._execute(
            lambda: self._authorize_tool(
                agent_id,
                config_id,
                tool_id,
                authorized=authorized,
                read_only=read_only,
                catalog_fingerprint=catalog_fingerprint,
                evidence=evidence,
            )
        )

    async def _authorize_tool(
        self,
        agent_id: str,
        config_id: str,
        tool_id: str,
        *,
        authorized: bool,
        read_only: bool,
        catalog_fingerprint: str,
        evidence: str,
    ) -> Record:
        return await self._db.write(
            lambda u: u.mcp_tool_decision(
                agent_id,
                config_id,
                tool_id,
                authorized=authorized,
                read_only=read_only,
                catalog_fingerprint=catalog_fingerprint,
                evidence=evidence,
            )
        )

    async def revoke(self, agent_id: str, config_id: str) -> Record:
        return await self._execute(lambda: self._revoke(agent_id, config_id))

    async def _revoke(self, agent_id: str, config_id: str) -> Record:
        return await self._db.write(lambda u: u.mcp_revoke_grants(agent_id, config_id, "owner_revoked"))

    async def calls(self, agent_id: str, config_id: str, after: str = "", limit: int = 50) -> Record:
        return await self._execute(lambda: self._calls(agent_id, config_id, after, limit))

    async def _calls(self, agent_id: str, config_id: str, after: str = "", limit: int = 50) -> Record:
        return await self._db.read(lambda u: u.mcp_call_page(agent_id, config_id, after, limit))

    async def oauth_start(self, agent_id: str, config_id: str, redirect_uri: str) -> Record:
        return await self._execute(lambda: self._oauth_start(agent_id, config_id, redirect_uri))

    async def _oauth_start(self, agent_id: str, config_id: str, redirect_uri: str) -> Record:
        config = await self._db.read(lambda u: u.mcp_configuration(agent_id, config_id))
        value = cast(dict[str, object], config["value"])
        if value["auth_mode"] != "oauth":
            raise BusinessError("mcp_oauth_not_configured", "This server does not use remote OAuth", status=422)
        try:
            server = await self._db.read(lambda u: u.mcp_server_get(agent_id, config_id))
        except BusinessError:
            server = {"authority_epoch": 1}
        epoch = int(cast(int, server.get("authority_epoch") or 1))
        target = server_target({**config, "authority_epoch": epoch})
        started = await self._transports.oauth_start(target, redirect_uri)
        return {**started, "server": config["name"]}

    async def oauth_callback(self, state: str, code: str) -> Record:
        return await self._execute(lambda: self._oauth_callback(state, code))

    async def _oauth_callback(self, state: str, code: str) -> Record:
        completed = await self._transports.oauth_callback(state, code)
        view = await self._db.write(
            lambda u: u.mcp_oauth_authorized(str(completed["agent_id"]), str(completed["config_id"]))
        )
        return {
            "authorized": True,
            "server": view["name"],
            "state": view["state"],
            "authority_epoch": view["authority_epoch"],
            "scope": completed["scope"],
        }

    # ---------- run-scoped tools ----------

    async def tools_for_run(self, agent_id: str, run_id: str) -> Sequence[ToolSpec]:
        return await self._execute(lambda: self._tools_for_run(agent_id, run_id))

    async def _tools_for_run(self, agent_id: str, run_id: str) -> Sequence[ToolSpec]:
        try:
            await self._db.write(lambda u: u.mcp_run_capture(agent_id, run_id))
        except BusinessError as exc:
            if exc.code not in {"mcp_capacity", "mcp_tool_collision"}:
                raise
            # A broken capture never exposes a partial or substituted tool set for this run.
            log.warning("MCP capture rejected", extra={"event": "mcp.capture_rejected", "error_code": exc.code})
            return ()
        rows = await self._db.read(lambda u: u.mcp_run_tools(agent_id, run_id))
        return tuple(tool_spec(row) for row in rows)

    def quiescent(self) -> bool:
        health = self._transports.health()
        sessions = health.get("sessions", [])
        dirty = isinstance(sessions, list) and any(
            isinstance(row, dict) and (row.get("busy") or row.get("starting") or row.get("cleanup_pending"))
            for row in sessions
        )
        return (
            not self._pending
            and not health.get("pending", 0)
            and not dirty
            and (self._transport_close is None or self._clean_close(self._transport_close))
        )

    # ---------- model invocation ----------

    async def invoke(
        self, name: str, arguments: Mapping[str, JSON], *, operation_id: str, context: InvocationContext
    ) -> ActionResult:
        invocation = _Invocation()
        try:
            task = self._admit(
                lambda: self._invoke(
                    name, arguments, operation_id=operation_id, context=context, cancellation=invocation
                ),
                invocation,
            )
        except BusinessError as exc:
            return ActionResult(Outcome.ERROR, exc.message, error_code=exc.code)
        return await self._wait(task, invocation)

    async def _invoke(
        self,
        name: str,
        arguments: Mapping[str, JSON],
        *,
        operation_id: str,
        context: InvocationContext,
        cancellation: _Invocation,
    ) -> ActionResult:
        if self._closing:
            return ActionResult(Outcome.ERROR, "MCP admission is draining", error_code="mcp_closing")
        try:
            digest = argument_digest(dict(arguments))
        except BusinessError as exc:
            return ActionResult(Outcome.DENIED, exc.message, error_code=exc.code)
        try:
            cancellation.check()
            binding = await self._db.read(
                lambda u: u.mcp_dispatch(context.agent_id, context.run_id, operation_id, name)
            )
            cancellation.check()
            if binding["arguments_sha256"] != digest:
                return ActionResult(
                    Outcome.DENIED, "MCP arguments differ from the approved intent", error_code="mcp_arguments_changed"
                )
            config = await self._db.read(lambda u: u.mcp_configuration(context.agent_id, str(binding["config_id"])))
            cancellation.check()
            call = await self._db.write(
                lambda u: u.mcp_call_begin(context.agent_id, context.run_id, operation_id, binding)
            )
        except BusinessError as exc:
            return ActionResult(Outcome.DENIED, exc.message, error_code=exc.code)
        call_id = str(call["id"])
        started = self._clock.monotonic()
        responded = False

        async def before_send() -> None:
            # Cancellation must retain the original transaction's delivery. In
            # particular, a dispatched commit may finish after its waiter leaves.
            cancellation.interruptible = False
            try:
                cancellation.check()
                latest = await self._db.read(
                    lambda u: u.mcp_dispatch(context.agent_id, context.run_id, operation_id, name)
                )
                cancellation.check()
                if latest["arguments_sha256"] != digest:
                    raise BusinessError("mcp_arguments_changed", "MCP arguments differ from the approved intent")
                await self._db.write(lambda u: u.mcp_call_dispatched(context.agent_id, call_id))
                cancellation.check()
            finally:
                cancellation.interruptible = True

        try:
            cancellation.check()
            target = _target(config, binding)
            cancellation.interruptible = True
            try:
                payload = await self._transports.call(
                    target,
                    str(binding["tool_name"]),
                    dict(arguments),
                    DEFAULT_CALL_TIMEOUT_S,
                    before_send=before_send,
                )
                responded = True
            finally:
                cancellation.interruptible = False
            text, data = result_text(str(config["name"]), str(binding["tool_name"]), payload)
            is_error = call_state(payload) == "failed"
        except BusinessError as exc:
            unknown = responded or isinstance(exc, MCPUncertain) or bool(getattr(exc, "sent", False))
            state = "unknown" if unknown else "failed"
            await self._finish(context.agent_id, call_id, state, exc.code, started)
            if unknown:
                return ActionResult(
                    Outcome.UNKNOWN,
                    f"MCP call {binding['tool_name']} on {config['name']} may have executed ({exc.code}); "
                    "verify the external state before retrying.",
                    Effect.UNKNOWN,
                    error_code=exc.code,
                )
            return ActionResult(
                Outcome.ERROR,
                f"MCP call {binding['tool_name']} on {config['name']} was not executed ({exc.code}).",
                Effect.NONE,
                error_code=exc.code,
            )
        except asyncio.CancelledError:
            current = await self._db.read(lambda u: u.mcp_call_get(context.agent_id, call_id))
            state = "unknown" if current["state"] == "dispatched" else "cancelled"
            await self._finish(context.agent_id, call_id, state, "mcp_cancelled", started)
            raise
        except BaseException:
            await self._finish(context.agent_id, call_id, "unknown", "mcp_outcome_unknown", started)
            raise
        await self._db.write(
            lambda u: u.mcp_call_finish(
                context.agent_id,
                call_id,
                "failed" if is_error else "done",
                "mcp_tool_error" if is_error else "",
                cast(int, data["chars"]),
                bool(data["truncated"]),
                int(max(0, (self._clock.monotonic() - started) * 1000)),
            )
        )
        return ActionResult(
            Outcome.ERROR if is_error else Outcome.OK,
            text,
            Effect.NONE if binding["read_only"] else Effect.APPLIED,
            error_code="mcp_tool_error" if is_error else "",
            data=cast(dict[str, JSON], data),
        )

    async def _finish(self, agent_id: str, call_id: str, state: str, code: str, started: float) -> None:
        try:
            await self._db.write(
                lambda u: u.mcp_call_finish(
                    agent_id,
                    call_id,
                    state,
                    code,
                    duration_ms=int(max(0, (self._clock.monotonic() - started) * 1000)),
                )
            )
        except BusinessError:
            # The call row is already terminal (a concurrent settle won); the action
            # row still records the true outcome for this run.
            log.warning("MCP call settle was already terminal", extra={"event": "mcp.call_settle_conflict"})

    # ---------- lifecycle ----------

    async def recover(self) -> Record:
        return await self._execute(lambda: self._recover())

    async def _recover(self) -> Record:
        return await self._db.write(lambda u: u.mcp_calls_recover())

    def health(self) -> Record:
        return {
            "closing": self._closing,
            "pending": len(self._pending),
            "max_pending": MAX_PENDING_OPERATIONS,
            "cleanup_pending": self._transport_close is not None and not self._clean_close(self._transport_close),
            "transports": self._transports.health(),
        }

    def stop_accepting(self) -> None:
        self._closing = True
        self._transports.stop_accepting()

    @staticmethod
    def _clean_close(task: asyncio.Task[bool]) -> bool:
        return task.done() and not task.cancelled() and task.exception() is None and task.result() is True

    async def close(self, timeout_s: float = 30) -> bool:
        self.stop_accepting()
        deadline = asyncio.get_running_loop().time() + max(0, timeout_s)
        for invocation in tuple(self._invocations.values()):
            invocation.cancel()
        while self._pending:
            _, pending = await asyncio.wait(
                tuple(self._pending), timeout=max(0, deadline - asyncio.get_running_loop().time())
            )
            if pending:
                return False
        if self._transport_close is None or (
            self._transport_close.done() and not self._clean_close(self._transport_close)
        ):
            self._transport_close = asyncio.create_task(
                self._transports.close(max(0, deadline - asyncio.get_running_loop().time())),
                name="mcp-owned-transport-close",
            )
            self._transport_close.add_done_callback(lambda task: task.exception() if not task.cancelled() else None)
        _, pending = await asyncio.wait(
            (self._transport_close,), timeout=max(0, deadline - asyncio.get_running_loop().time())
        )
        return (
            not pending
            and not self._transport_close.cancelled()
            and self._transport_close.exception() is None
            and self._transport_close.result() is True
        )


__all__ = ["MCPService", "MCPTransportPort"]
