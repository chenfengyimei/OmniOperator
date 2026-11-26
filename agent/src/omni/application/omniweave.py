"""Observe Web admissions and expose scoped planning controls to Router runs."""

from __future__ import annotations

import hashlib
import json
from collections.abc import Callable, Mapping
from dataclasses import dataclass

from ..domain.contracts import JSON, ActionResult, BusinessError, InvocationContext, Outcome, ToolSpec
from ..domain.delegation import tool_hash
from ..domain.omniweave import OWA_PLAN, OWA_STATUS, WORKER_TOOLS, OmniWeaveMode
from .ports import DatabasePort, Record, TurnAdmissionObserver, UnitOfWork


@dataclass(frozen=True)
class OmniWeaveService:
    mode: OmniWeaveMode
    database: DatabasePort
    notify: Callable[[], None]
    worker_tool_specs: tuple[ToolSpec, ...] = ()

    async def main_session(self, agent_id: str) -> Record:
        session_id = await self.database.read(lambda unit: unit.omniweave_main_session(agent_id))
        return {"enabled": self.mode == "dispatch", "session_id": session_id}

    async def select_main_session(self, agent_id: str, session_id: str | None, owner_token: str) -> Record:
        if self.mode != "dispatch":
            raise BusinessError(
                "owa_dispatch_disabled", "Enable OmniWeave before selecting a main conversation", status=409
            )
        digest = hashlib.sha256(owner_token.encode()).hexdigest()

        def select_main(unit: UnitOfWork) -> str:
            if not unit.token_valid(digest):
                raise BusinessError("unauthenticated", "A valid owner token is required", status=401)
            return unit.omniweave_select_main(agent_id, session_id)

        return {"enabled": True, "session_id": await self.database.write(select_main)}

    def web_admission(self, agent_id: str, key: str, owner_token: str) -> TurnAdmissionObserver | None:
        if self.mode == "off":
            return None
        digest = hashlib.sha256(owner_token.encode()).hexdigest()

        def observe(unit: UnitOfWork, receipt: Record) -> None:
            # A token revoked while the request waited for the writer cannot authorize a new Ticket.
            if not unit.token_valid(digest):
                raise BusinessError("unauthenticated", "A valid owner token is required", status=401)
            unit.omniweave_observe(
                agent_id,
                key,
                str(receipt["run_id"]),
                str(receipt["session_id"]),
                duplicate=receipt["duplicate"] is True,
                dispatch=self.mode == "dispatch",
            )

        return observe

    async def tools_for_run(self, agent_id: str, run_id: str) -> tuple[ToolSpec, ...]:
        if self.mode != "dispatch":
            return ()
        execution = await self.database.read(lambda unit: unit.omniweave_router(agent_id, run_id))
        return (OWA_PLAN, OWA_STATUS) if execution and not execution["cancelled_at"] else ()

    async def invoke(
        self, name: str, arguments: Mapping[str, JSON], *, operation_id: str, context: InvocationContext
    ) -> ActionResult:
        if self.mode != "dispatch":
            return ActionResult(Outcome.DENIED, "OmniWeave dispatch is disabled", error_code="owa_dispatch_disabled")
        agent_id, run_id = context.agent_id, context.run_id

        def read_status(unit: UnitOfWork) -> Record:
            unit.assert_run_authorized(agent_id, run_id)
            router = unit.omniweave_router(agent_id, run_id)
            if router is None or router["cancelled_at"] or router["main_session_id"] != context.session_id:
                raise BusinessError("owa_router_not_active", "Status is available from the owning main conversation")
            return unit.omniweave_ticket(
                agent_id, context.session_id, str(arguments.get("ticket_id", "")), include_results=True
            )

        try:
            if name == OWA_PLAN.name:
                catalog = {spec.name: tool_hash(spec) for spec in self.worker_tool_specs if spec.name in WORKER_TOOLS}
                result = await self.database.write(
                    lambda unit: unit.omniweave_plan(agent_id, run_id, dict(arguments), catalog)
                )
                self.notify()
            elif name == OWA_STATUS.name:
                result = await self.database.read(read_status)
            else:
                return ActionResult(Outcome.ERROR, "Unknown OmniWeave tool", error_code="tool_not_found")
        except BusinessError as exc:
            return ActionResult(Outcome.ERROR, exc.message, error_code=exc.code)
        return ActionResult(Outcome.OK, json.dumps(result, ensure_ascii=False), data=result)

    async def public_ticket(self, agent_id: str, session_id: str, ticket_id: str, run_id: str) -> Record:
        if ticket_id and run_id:
            raise BusinessError("owa_ticket_filter_invalid", "Select either a Ticket or a root Run", status=422)
        return await self.database.read(
            lambda unit: unit.omniweave_ticket(agent_id, session_id, ticket_id, run_id=run_id)
        )

    async def require_public_scope(self, agent_id: str, resources: Mapping[str, str]) -> None:
        if await self.database.read(
            lambda unit: any(
                unit.omniweave_private_scope(agent_id, field, identity) for field, identity in resources.items()
            )
        ):
            raise BusinessError("not_found", "Resource does not exist in this Agent space", status=404)

    async def close(self, timeout_s: float) -> bool:
        return True
