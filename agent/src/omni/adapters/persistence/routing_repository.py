"""Scoped immutable tier policies, run choices, promotions and actual tool manifests; no model calls or I/O."""

from __future__ import annotations

import hashlib
import json
from collections.abc import Sequence
from typing import TYPE_CHECKING, cast

from sqlalchemy import func, select

from ...application.ports import Record
from ...domain.contracts import JSON, BusinessError, ToolSpec
from ...domain.metering import wire_tools
from ...domain.routing import (
    KERNEL_DEFAULT,
    MODES,
    POLICY_DEFAULT,
    choose,
    invalid,
    kernel_policy,
    policy,
    requested,
    select_tools,
    tool_window,
)
from . import schema as s
from .image_repository import ImageRepositories

if TYPE_CHECKING:
    from .repository import Repositories


def encoded(specs: Sequence[ToolSpec]) -> bytes:
    return json.dumps(wire_tools(specs), sort_keys=True, ensure_ascii=False, separators=(",", ":")).encode()


class RoutingRepositories(ImageRepositories):
    def route_policy(self, agent_id: str) -> Record:
        self._configuration_agent(agent_id)
        row = (
            self.connection.execute(
                select(s.model_route_policies)
                .where(s.model_route_policies.c.agent_id == agent_id)
                .order_by(s.model_route_policies.c.revision.desc())
                .limit(1)
            )
            .mappings()
            .first()
        )
        result = cast(
            Record,
            dict(row) if row else {"id": None, "revision": 0, "agent_id": agent_id, **POLICY_DEFAULT},
        )
        result["kernel"] = kernel_policy(result.get("kernel", {}))
        return result

    def route_policy_save(self, agent_id: str, value: Record, revision: int) -> Record:
        checked = policy(value)
        current = self.route_policy(agent_id)
        if type(revision) is not int or revision != current["revision"]:
            raise BusinessError("revision_conflict", "Routing configuration changed; retain the draft and reload")
        if "kernel" not in value:
            checked["kernel"] = current["kernel"]
        kernel = kernel_policy(checked["kernel"])
        for mode in MODES:
            if checked[f"{mode}_model_id"]:
                model = self.configuration_get(agent_id, cast(str, checked[f"{mode}_model_id"]))
                if model["kind"] != "model" or not model["enabled"]:
                    raise invalid("routing_model_unavailable")
        for identity in cast(list[str], kernel["fallback_model_ids"]):
            model = self.configuration_get(agent_id, identity)
            if model["kind"] != "model" or not model["enabled"]:
                raise invalid("routing_model_unavailable")
        row = self._record(s.model_route_policies, {**checked, "revision": revision + 1}, agent_id)
        self.audit(agent_id, "routing.policy_saved", cast(str, row["id"]), {"revision": revision + 1})
        return row

    def route_resolve(self, agent_id: str, text: str, mode: str | None, images: bool, remote: bool) -> Record:
        config = self.route_policy(agent_id)
        decision = choose(text, requested(mode), cast(str, config["default_mode"]), images=images, remote=remote)
        current = cast(Record, self.settings(agent_id)["value"])
        kernel = kernel_policy(config["kernel"]) if not remote else KERNEL_DEFAULT
        model_id = config[f"{decision['mode']}_model_id"] or current["model_profile_id"]
        fallbacks: list[JSON] = []
        for identity in cast(list[str], kernel["fallback_model_ids"]) if not images else ():
            if identity == model_id:
                continue
            try:
                slot = self.configuration_get(agent_id, identity)
            except BusinessError as exc:
                if exc.code != "not_found":
                    raise
                continue
            if slot["kind"] == "model" and slot["enabled"] and cast(Record, slot["value"])["model"]:
                fallbacks.append(
                    cast(
                        Record,
                        {
                            "model_revision_id": slot["revision_id"],
                            "permission_epoch": slot["permission_epoch"],
                        },
                    )
                )
        return {
            **decision,
            "requested_mode": requested(mode),
            "policy_id": config["id"],
            "model_id": model_id,
            "kernel": {
                **{key: kernel[key] for key in KERNEL_DEFAULT if key != "fallback_model_ids"},
                "fallbacks": fallbacks,
            },
            "allow_escalation": bool(
                config["allow_escalation"]
                and not remote
                and "request_mode" not in cast(list[object], current["disabled_tools"])
            ),
            "reservation_micro": config[f"{decision['mode']}_reservation_micro"] or current["call_reservation_micro"],
        }

    def route_attach(self, agent_id: str, run_id: str, choice: Record) -> None:
        row = self._record(
            s.run_routes,
            {
                "run_id": run_id,
                "kernel": choice.get("kernel", {}),
                **{
                    k: choice[k]
                    for k in (
                        "policy_id",
                        "requested_mode",
                        "source",
                        "reason",
                        "allow_escalation",
                        "reservation_micro",
                    )
                },
                "initial_mode": choice["mode"],
            },
            agent_id,
        )
        run = self._one(s.runs, agent_id, run_id)
        self.audit(
            agent_id,
            "routing.selected",
            cast(str, row["id"]),
            {
                "run_id": run_id,
                "mode": choice["mode"],
                "model_revision_id": run["model_revision_id"],
                "source": choice["source"],
            },
        )

    def run_route(self, agent_id: str, run_id: str) -> Record:
        run = self._one(s.runs, agent_id, run_id)
        row = (
            self.connection.execute(
                select(s.run_routes).where(s.run_routes.c.run_id == run_id, s.run_routes.c.agent_id == agent_id)
            )
            .mappings()
            .first()
        )
        if not row:
            return cast(
                Record,
                {"recorded": False, "mode": "agent", "allow_escalation": False, "promoted": False},
            )
        change = (
            self.connection.execute(
                select(s.route_changes).where(
                    s.route_changes.c.run_id == run_id, s.route_changes.c.agent_id == agent_id
                )
            )
            .mappings()
            .first()
        )
        model = self._configuration_revision(agent_id, cast(str, run["model_revision_id"]))
        return cast(
            Record,
            {
                **dict(row),
                "recorded": True,
                "mode": change["to_mode"] if change else row["initial_mode"],
                "promoted": bool(change),
                "model_revision_id": run["model_revision_id"],
                "model": cast(Record, model["value"])["model"],
                "model_name": model["name"],
                "change": dict(change) if change else None,
            },
        )

    def route_reservation(self, agent_id: str, run_id: str, default: int) -> int:
        reserved = self.connection.execute(
            select(s.run_routes.c.reservation_micro).where(
                s.run_routes.c.run_id == run_id,
                s.run_routes.c.agent_id == agent_id,
            )
        ).scalar_one_or_none()
        return reserved if reserved is not None else default

    def route_promote(self, agent_id: str, run_id: str, target: str) -> Record:
        unit = cast("Repositories", self)
        unit.assert_run_authorized(agent_id, run_id)
        unit.assert_context(agent_id, run_id)
        unit.assert_model_authorized(agent_id, run_id)
        route = self.run_route(agent_id, run_id)
        run = self._one(s.runs, agent_id, run_id)
        if not route["recorded"] or run["state"] != "running" or not route["allow_escalation"]:
            raise invalid("mode_escalation_disabled")
        if route["promoted"]:
            raise invalid("mode_escalation_exhausted")
        if target not in MODES or MODES.index(target) <= MODES.index(cast(str, route["mode"])):
            raise invalid("mode_escalation_not_upward")
        source = self.connection.execute(
            select(s.model_call_routes.c.id)
            .join(s.model_calls, s.model_calls.c.id == s.model_call_routes.c.call_id)
            .where(
                s.model_call_routes.c.route_id == route["id"],
                s.model_call_routes.c.agent_id == agent_id,
                s.model_calls.c.state.in_(["settled", "unknown"]),
                s.model_call_routes.c.mode == route["mode"],
                s.model_call_routes.c.id.in_(
                    select(s.model_call_route_tools.c.snapshot_id).where(
                        s.model_call_route_tools.c.name == "request_mode",
                        s.model_call_route_tools.c.agent_id == agent_id,
                    )
                ),
            )
            .order_by(s.model_call_routes.c.ordinal.desc())
            .limit(1)
        ).scalar_one_or_none()
        if source is None:
            raise invalid("mode_escalation_not_offered")
        self._record(
            s.route_changes,
            {
                "route_id": route["id"],
                "run_id": run_id,
                "from_mode": route["mode"],
                "to_mode": target,
                "source_snapshot_id": source,
            },
            agent_id,
        )
        self.audit(
            agent_id,
            "routing.promoted",
            run_id,
            {"from": route["mode"], "to": target, "model_revision_id": route["model_revision_id"]},
        )
        unit.event(agent_id, run_id, "routing.promoted", {"from": route["mode"], "to": target, "model_unchanged": True})
        return self.run_route(agent_id, run_id)

    def route_call(
        self,
        agent_id: str,
        run_id: str,
        call_id: str,
        tools: Sequence[ToolSpec],
        baseline: Sequence[ToolSpec],
        *,
        model_revision_id: str | None = None,
        window_tokens: int | None = None,
        exposed: Sequence[str] = (),
    ) -> None:
        route = self.run_route(agent_id, run_id)
        if not route["recorded"]:
            return
        expected = select_tools(
            cast(str, route["mode"]),
            baseline,
            escalate=bool(route["allow_escalation"] and not route["promoted"]),
        )
        if window_tokens is not None:
            kernel = route.get("kernel")
            if not isinstance(kernel, dict) or not kernel.get("tool_discovery") or len(exposed) > 4:
                raise invalid("routing_toolset_changed")
            expected = tool_window(expected, window_tokens, exposed)
        if encoded(expected) != encoded(tools):
            raise invalid("routing_toolset_changed")
        selected_bytes, full_bytes = encoded(tools), encoded(baseline)
        row = self._record(
            s.model_call_routes,
            {
                "call_id": call_id,
                "model_revision_id": model_revision_id or route["model_revision_id"],
                "route_id": route["id"],
                "ordinal": self.connection.execute(
                    select(func.coalesce(func.max(s.model_call_routes.c.ordinal), 0) + 1).where(
                        s.model_call_routes.c.route_id == route["id"]
                    )
                ).scalar_one(),
                "mode": route["mode"],
                "tool_count": len(tools),
                "full_tool_count": len(baseline),
                "tool_bytes": len(selected_bytes),
                "full_tool_bytes": len(full_bytes),
                "manifest_sha256": hashlib.sha256(selected_bytes).hexdigest(),
            },
            agent_id,
        )
        for position, spec in enumerate(tools):
            self._record(
                s.model_call_route_tools,
                {
                    "snapshot_id": row["id"],
                    "position": position,
                    "name": spec.name,
                    "spec_sha256": hashlib.sha256(encoded([spec])).hexdigest(),
                },
                agent_id,
            )

    def assert_tool_bindings(self, agent_id: str, run_id: str, tools: Sequence[ToolSpec]) -> None:
        unit = cast("Repositories", self)
        for prefix, current in (("ext__", unit.extension_run_tools), ("mcp__", unit.mcp_run_tools)):
            required = {(spec.name, spec.binding_id) for spec in tools if spec.name.startswith(prefix)}
            if required and not required.issubset({(r["name"], r["binding_id"]) for r in current(agent_id, run_id)}):
                raise BusinessError("tool_authority_changed", "A captured tool was revoked; submit a new turn")

    def route_report(self, agent_id: str, run_id: str) -> Record:
        route = self.run_route(agent_id, run_id)
        rows = [
            cast(Record, dict(r))
            for r in self.connection.execute(
                select(
                    s.model_call_routes,
                    s.model_calls.c.model,
                    s.model_calls.c.state.label("call_state"),
                    s.model_calls.c.cost_micro,
                    s.model_calls.c.input_token_estimate,
                    s.model_calls.c.counting_method,
                    # 真实用量（供应商上报）：与估算口径分开暴露，界面分别标注。
                    # 未上报时为 NULL，原样交给界面显示「未知」，不补 0。
                    s.model_calls.c.input_tokens,
                    s.model_calls.c.output_tokens,
                )
                .join(s.model_calls, s.model_calls.c.id == s.model_call_routes.c.call_id)
                .where(s.model_call_routes.c.agent_id == agent_id, s.model_calls.c.run_id == run_id)
                .order_by(s.model_call_routes.c.ordinal)
                .limit(65)
            ).mappings()
        ]
        for row in rows:
            row["tools"] = [
                cast(Record, dict(t))
                for t in self.connection.execute(
                    select(s.model_call_route_tools.c.name, s.model_call_route_tools.c.spec_sha256)
                    .where(
                        s.model_call_route_tools.c.snapshot_id == row["id"],
                        s.model_call_route_tools.c.agent_id == agent_id,
                    )
                    .order_by(s.model_call_route_tools.c.position)
                ).mappings()
            ]
        totals = self.connection.execute(
            select(
                func.coalesce(func.sum(s.model_calls.c.cost_micro), 0),
                func.count().filter(s.model_calls.c.state.in_(["reserved", "unknown"])),
                func.count().filter(s.model_calls.c.purpose == "turn"),
            ).where(s.model_calls.c.agent_id == agent_id, s.model_calls.c.run_id == run_id)
        ).one()
        return cast(
            Record,
            {
                "route": route,
                "calls": rows[:64],
                "has_more": len(rows) > 64,
                "known_cost_micro": totals[0],
                "unsettled_calls": totals[1],
                "unrecorded_turn_calls": max(0, totals[2] - len(rows)),
                "currency": "USD",
                "routing_model_calls": 0,
                "byte_delta": sum(cast(int, r["full_tool_bytes"]) - cast(int, r["tool_bytes"]) for r in rows[:64]),
                "savings_claim": "tool_schema_bytes_only_not_real_cost_or_quality",
            },
        )
