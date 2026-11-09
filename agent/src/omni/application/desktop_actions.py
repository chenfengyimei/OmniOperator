"""Owner-selected semantic commands using the shared run, approval and action ledger."""

from __future__ import annotations

import asyncio
import hashlib
import json
from collections.abc import Callable, Mapping
from typing import Any

from ..domain.contracts import JSON, ActionResult, BusinessError, InvocationContext, Outcome
from ..domain.desktop_actions import POLICY, SPEC, action_result, owner_action
from ..domain.foreground import POLICY as FOREGROUND_POLICY
from .desktop import DesktopService
from .ports import Clock, DatabasePort, DesktopBackendPort, Ids, Record


class DesktopActions:
    def __init__(
        self,
        db: DatabasePort,
        views: DesktopService,
        backend: DesktopBackendPort,
        clock: Clock,
        ids: Ids,
        *,
        accepting: Callable[[], bool],
    ) -> None:
        self._db, self._views, self._backend, self._clock, self._ids = db, views, backend, clock, ids
        self._accepting = accepting
        self._bindings: dict[str, dict] = {}
        self._pending: set[asyncio.Task[Any]] = set()
        self._leases: list[Record] = []
        self._closing = False
        self._admission = asyncio.Lock()

    async def _owned(self, coroutine) -> Any:
        if self._closing or len(self._pending) >= 4:
            coroutine.close()
            raise BusinessError("desktop_busy", "Desktop action admission is closed or busy", status=503)
        task = asyncio.create_task(coroutine, name="desktop-action")
        self._pending.add(task)

        def done(future: asyncio.Task[Any]) -> None:
            self._pending.discard(future)
            if not future.cancelled():
                future.exception()

        task.add_done_callback(done)
        return await asyncio.shield(task)

    async def submit(self, agent_id: str, data: object) -> Record:
        if not self._accepting():
            raise BusinessError("not_ready", "Application is not accepting work", status=503)
        request = owner_action(data)
        return await self._owned(self._submit(agent_id, request))

    async def _submit(self, agent_id: str, request: dict) -> Record:
        fingerprint = hashlib.sha256(
            json.dumps(request, sort_keys=True, ensure_ascii=False, separators=(",", ":")).encode()
        ).hexdigest()
        async with self._admission:
            replay = await self._db.read(lambda u: u.owner_command_replay(agent_id, request["request_id"], fingerprint))
            if replay:
                return replay
            if self._closing or not self._accepting():
                raise BusinessError("desktop_closing", "Desktop action admission is closed")
            self._bindings = {k: v for k, v in self._bindings.items() if v["expires"] > self._clock.monotonic()}
            if len(self._bindings) >= 64:
                raise BusinessError("desktop_busy", "Desktop action bindings are full")
            binding = self._views.action_binding(agent_id, request)
            identity = self._ids.new()
            arguments: Record = {
                "binding_id": identity,
                "kind": request["kind"],
                "text": request["text"],
                "target_application": binding["target"]["application_id"],
                "target_role": binding["node"]["role"],
                "policy": FOREGROUND_POLICY if request.get("takeover") else POLICY,
            }
            if request.get("takeover"):
                arguments["takeover"] = True
            # Keep identities only. Approved content has a single persistent owner in the command/action ledger.
            reference = {k: v for k, v in request.items() if k != "text"}
            self._bindings[identity] = {**binding, "agent_id": agent_id, "request": reference}
            try:
                return await self._db.write(
                    lambda u: u.accept_owner_command(
                        agent_id, request["session_id"], request["request_id"], fingerprint, arguments
                    )
                )
            except BaseException:
                self._bindings.pop(identity, None)
                raise

    async def invoke(
        self, name: str, arguments: Mapping[str, JSON], *, operation_id: str, context: InvocationContext
    ) -> ActionResult:
        if self._closing or len(self._pending) >= 4:
            return ActionResult(Outcome.DENIED, "Desktop action was not dispatched", error_code="desktop_busy")
        return await self._owned(self._invoke(name, dict(arguments), operation_id, context))

    async def _invoke(
        self, name: str, arguments: Record, operation_id: str, context: InvocationContext
    ) -> ActionResult:
        binding = self._bindings.get(str(arguments.get("binding_id")))
        if name != SPEC.name or binding is None or binding["agent_id"] != context.agent_id:
            return ActionResult(Outcome.DENIED, "Desktop binding expired", error_code="desktop_binding_expired")
        if self._leases:
            await self._release(0)
            if self._leases:
                return ActionResult(Outcome.DENIED, "Native cleanup is pending", error_code="desktop_cleanup_pending")

        async def guard() -> None:
            if self._closing:
                raise BusinessError("desktop_action_revoked", "Desktop action admission was revoked")
            current = self._views.action_binding(context.agent_id, binding["request"])
            if current != {k: binding[k] for k in ("target", "node", "expires")}:
                raise BusinessError("desktop_binding_changed", "Desktop observation changed")
            await self._db.read(lambda u: u.desktop_dispatch(context.agent_id, context.run_id, operation_id, arguments))
            self._views.action_binding(context.agent_id, binding["request"])

        try:
            await guard()
            lease = await self._db.write(
                lambda u: u.lease_acquire(context.agent_id, "desktop/semantic", context.run_id)
            )
        except BusinessError as error:
            return ActionResult(Outcome.DENIED, "Desktop action was not dispatched", error_code=error.code)
        if lease is None:
            return ActionResult(Outcome.DENIED, "Desktop lease is held", error_code="desktop_busy")
        self._leases.append(lease)
        try:
            if arguments.get("takeover"):
                result = await self._backend.foreground(
                    binding["target"],
                    {
                        "kind": arguments["kind"],
                        "text": arguments["text"],
                        "node": binding["node"],
                        "policy": FOREGROUND_POLICY,
                        "takeover": True,
                        "grant_id": arguments["binding_id"],
                        "run_id": context.run_id,
                        "operation_id": operation_id,
                    },
                    before_dispatch=guard,
                )
            else:
                result = await self._backend.act(
                    binding["target"],
                    {"kind": arguments["kind"], "text": arguments["text"], "node": binding["node"], "policy": POLICY},
                    before_dispatch=guard,
                )
            return action_result(result, str(arguments["kind"]))
        finally:
            self._views.consume_observation(context.agent_id, binding["request"]["observation_id"])
            self._bindings.pop(str(arguments["binding_id"]), None)
            await self._release(0.2)

    async def _release(self, timeout_s: float) -> None:
        if not self._leases:
            return
        if not await self._backend.reclaim(timeout_s):
            return
        for lease in list(self._leases):
            await self._db.write(
                lambda u: u.lease_release(
                    str(lease["agent_id"]), str(lease["resource"]), str(lease["token"]), int(str(lease["fence"]))
                )
            )
            self._leases.remove(lease)

    def health(self) -> Record:
        return {
            "closing": self._closing,
            "pending": len(self._pending),
            "bindings": len(self._bindings),
            "leases": len(self._leases),
        }

    def quiescent(self) -> bool:
        return not self._pending and self._backend.quiescent()

    def stop_accepting(self) -> None:
        self._closing = True
        self._bindings.clear()

    async def close(self, timeout_s: float) -> bool:
        self.stop_accepting()
        deadline = self._clock.monotonic() + max(0, timeout_s)
        if self._pending:
            await asyncio.wait(self._pending, timeout=max(0, deadline - self._clock.monotonic()))
        await self._release(max(0, deadline - self._clock.monotonic()))
        return not self._pending and not self._leases
