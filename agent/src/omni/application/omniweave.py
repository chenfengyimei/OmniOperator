"""Observe Web admissions and expose scoped planning controls to Router runs."""

from __future__ import annotations

import hashlib
import json
from collections.abc import Callable, Mapping
from dataclasses import dataclass
from typing import cast

from ..domain.contracts import JSON, ActionResult, BusinessError, InvocationContext, Outcome, ToolSpec
from ..domain.omniweave import (
    OWA_PLAN,
    OWA_STATUS,
    PEER_TOOL_NAMES,
    PEER_TOOLS,
    WORKER_READ_TOOLS,
    WORKER_TOOLS,
    GoalUpdate,
    OmniWeaveMode,
)
from ..domain.omniweave_artifacts import (
    ARTIFACT_TOOLS,
    PUBLISH_ARTIFACT,
    READ_SHARED,
    SUBSCRIBE_ARTIFACTS,
    checked_artifact,
)
from .objects import ObjectService
from .ports import DatabasePort, Record, TurnAdmissionObserver, TurnCancellation, UnitOfWork


@dataclass(frozen=True)
class OmniWeaveService:
    mode: OmniWeaveMode
    database: DatabasePort
    notify: Callable[[], None]
    objects: ObjectService
    worker_tool_specs: tuple[ToolSpec, ...] = ()

    def _worker_catalog(self) -> dict[str, ToolSpec]:
        workspace = {
            spec.name: spec
            for spec in self.worker_tool_specs
            if spec.name in WORKER_TOOLS and spec.writes == (spec.name not in WORKER_READ_TOOLS)
        }
        return {**workspace, **{spec.name: spec for spec in (*PEER_TOOLS, *ARTIFACT_TOOLS)}}

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

    def web_admission(
        self, agent_id: str, key: str, owner_token: str, goal_update: GoalUpdate | None = None
    ) -> TurnAdmissionObserver | None:
        if goal_update is not None and self.mode != "dispatch":
            raise BusinessError("owa_dispatch_disabled", "Goal updates require OmniWeave dispatch", status=409)
        if self.mode == "off":
            return None
        digest = hashlib.sha256(owner_token.encode()).hexdigest()

        def observe(unit: UnitOfWork, receipt: Record) -> TurnCancellation | None:
            # A token revoked while the request waited for the writer cannot authorize a new Ticket.
            if not unit.token_valid(digest):
                raise BusinessError("unauthenticated", "A valid owner token is required", status=401)
            return unit.omniweave_observe(
                agent_id,
                key,
                str(receipt["run_id"]),
                str(receipt["session_id"]),
                duplicate=receipt["duplicate"] is True,
                dispatch=self.mode == "dispatch",
                catalog=self._worker_catalog(),
                goal_update=goal_update,
            )

        return observe

    async def tools_for_run(self, agent_id: str, run_id: str) -> tuple[ToolSpec, ...]:
        if self.mode != "dispatch":
            return ()

        def offered(unit: UnitOfWork) -> tuple[ToolSpec, ...]:
            execution = unit.omniweave_router(agent_id, run_id)
            return (
                (OWA_PLAN, OWA_STATUS)
                if execution and not execution["cancelled_at"]
                else unit.omniweave_worker_tools(agent_id, run_id)
            )

        return await self.database.read(offered)

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
                agent_id,
                context.session_id,
                str(
                    arguments.get("ticket_id", "") or (router["root_ticket_id"] if router["goal_version"] != 1 else "")
                ),
                include_results=True,
            )

        try:
            if name == OWA_PLAN.name:
                catalog = self._worker_catalog()
                result = await self.database.write(
                    lambda unit: unit.omniweave_plan(agent_id, run_id, dict(arguments), catalog)
                )
                self.notify()
            elif name == OWA_STATUS.name:
                result = await self.database.read(read_status)
            elif name in PEER_TOOL_NAMES:
                result = await self.database.write(
                    lambda unit: unit.omniweave_peer_command(agent_id, run_id, name, dict(arguments))
                )
                self.notify()
            elif name == PUBLISH_ARTIFACT.name:
                result = await self._publish_artifact(agent_id, run_id, operation_id, dict(arguments))
                self.notify()
            elif name == READ_SHARED.name:
                result = await self._read_artifact(agent_id, run_id, str(arguments.get("artifact_id", "")))
            elif name == SUBSCRIBE_ARTIFACTS.name:
                result = await self.database.write(
                    lambda unit: unit.omniweave_artifact_subscribe(
                        agent_id,
                        run_id,
                        str(arguments.get("producer", "")),
                        arguments.get("enabled") is True,
                    )
                )
            else:
                return ActionResult(Outcome.ERROR, "Unknown OmniWeave tool", error_code="tool_not_found")
        except BusinessError as exc:
            return ActionResult(Outcome.ERROR, exc.message, error_code=exc.code)
        return ActionResult(Outcome.OK, json.dumps(result, ensure_ascii=False), data=result)

    async def _publish_artifact(self, agent_id: str, run_id: str, action_id: str, arguments: Record) -> Record:
        draft = checked_artifact(arguments)
        info = await self.database.read(lambda unit: unit.omniweave_artifact_prepare(agent_id, run_id, draft))
        if isinstance(receipt := info.get("receipt"), dict):
            return receipt

        async def check() -> None:
            await self.database.read(lambda unit: unit.omniweave_artifact_prepare(agent_id, run_id, draft))

        return await self.objects.publish_linked(
            agent_id,
            draft.object_bytes(str(info["producer_id"])),
            before_publish=check,
            attach=lambda unit, blob, expires: unit.omniweave_artifact_attach(
                agent_id, run_id, action_id, draft, blob, expires
            ),
        )

    async def _read_artifact(self, agent_id: str, run_id: str, artifact_id: str) -> Record:
        row, data = await self.objects.read_linked(
            lambda unit: unit.omniweave_artifact_read(agent_id, run_id, artifact_id)
        )
        try:
            value: object = json.loads(data)
        except (ValueError, UnicodeError) as exc:
            raise BusinessError("object_integrity_failed", "Stored artifact envelope is invalid", status=503) from exc
        payload = cast(dict[str, object], value) if isinstance(value, dict) else {}
        text = payload.get("text")
        if payload.get("format") != "owa-text-v1" or not isinstance(text, str):
            raise BusinessError("object_integrity_failed", "Stored artifact envelope is invalid", status=503)
        return {**{key: item for key, item in row.items() if key != "agent_id"}, "text": text}

    async def revoke_artifact(
        self, agent_id: str, session_id: str, artifact_id: str, reader: str, owner_token: str
    ) -> None:
        digest = hashlib.sha256(owner_token.encode()).hexdigest()

        def revoke(unit: UnitOfWork) -> None:
            if not unit.token_valid(digest):
                raise BusinessError("unauthenticated", "A valid owner token is required", status=401)
            unit.omniweave_artifact_revoke(agent_id, session_id, artifact_id, reader)

        await self.database.write(revoke)

    async def public_ticket(self, agent_id: str, session_id: str, ticket_id: str, run_id: str) -> Record:
        if ticket_id and run_id:
            raise BusinessError("owa_ticket_filter_invalid", "Select either a Ticket or a root Run", status=422)
        return await self.database.read(
            lambda unit: unit.omniweave_ticket(agent_id, session_id, ticket_id, run_id=run_id)
        )

    async def ticket_options(self, agent_id: str, session_id: str) -> list[Record]:
        if self.mode != "dispatch":
            return []
        return await self.database.read(lambda unit: unit.omniweave_ticket_options(agent_id, session_id))

    async def require_public_scope(self, agent_id: str, resources: Mapping[str, str]) -> None:
        if await self.database.read(
            lambda unit: any(
                unit.omniweave_private_scope(agent_id, field, identity) for field, identity in resources.items()
            )
        ):
            raise BusinessError("not_found", "Resource does not exist in this Agent space", status=404)

    async def close(self, timeout_s: float) -> bool:
        return True
