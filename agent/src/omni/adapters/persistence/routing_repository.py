"""Scoped immutable tier policies, run choices, promotions and actual tool manifests; no model calls or I/O."""

from __future__ import annotations

import hashlib
import json
from collections.abc import Sequence
from typing import TYPE_CHECKING, cast

from sqlalchemy import func, select

from ...domain.contracts import BusinessError, ToolSpec
from ...domain.metering import wire_tools
from ...domain.routing import MODES, POLICY_DEFAULT, choose, invalid, policy, requested, select_tools
from . import schema as s
from .image_repository import ImageRepositories

if TYPE_CHECKING:
    from .repository import Repositories


def encoded(specs: Sequence[ToolSpec]) -> bytes:
    return json.dumps(wire_tools(specs), sort_keys=True, ensure_ascii=False, separators=(",", ":")).encode()


class RoutingRepositories(ImageRepositories):
    def route_policy(self, agent_id: str) -> dict:
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
        return dict(row) if row else {"id": None, "revision": 0, "agent_id": agent_id, **POLICY_DEFAULT}

    def route_policy_save(self, agent_id: str, value: dict, revision: int) -> dict:
        checked = policy(value)
        current = self.route_policy(agent_id)
        if type(revision) is not int or revision != current["revision"]:
            raise BusinessError("revision_conflict", "Routing configuration changed; retain the draft and reload")
        for mode in MODES:
            if checked[f"{mode}_model_id"]:
                model = self.configuration_get(agent_id, checked[f"{mode}_model_id"])
                if model["kind"] != "model" or not model["enabled"]:
                    raise invalid("routing_model_unavailable")
        row = self._record(s.model_route_policies, {**checked, "revision": revision + 1}, agent_id)
        self.audit(agent_id, "routing.policy_saved", row["id"], {"revision": revision + 1})
        return row

    def route_resolve(self, agent_id: str, text: str, mode: str | None, images: bool, remote: bool) -> dict:
        config = self.route_policy(agent_id)
        decision = choose(text, requested(mode), config["default_mode"], images=images, remote=remote)
        current = self.settings(agent_id)["value"]
        return {
            **decision,
            "requested_mode": requested(mode),
            "policy_id": config["id"],
            "model_id": config[f"{decision['mode']}_model_id"] or current["model_profile_id"],
            "allow_escalation": bool(
                config["allow_escalation"] and not remote and "request_mode" not in current["disabled_tools"]
            ),
            "reservation_micro": config[f"{decision['mode']}_reservation_micro"] or current["call_reservation_micro"],
        }

    def route_attach(self, agent_id: str, run_id: str, choice: dict) -> None:
        row = self._record(
            s.run_routes,
            {
                "run_id": run_id,
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
            row["id"],
            {
                "run_id": run_id,
                "mode": choice["mode"],
                "model_revision_id": run["model_revision_id"],
                "source": choice["source"],
            },
        )

    def run_route(self, agent_id: str, run_id: str) -> dict:
        run = self._one(s.runs, agent_id, run_id)
        row = (
            self.connection.execute(
                select(s.run_routes).where(s.run_routes.c.run_id == run_id, s.run_routes.c.agent_id == agent_id)
            )
            .mappings()
            .first()
        )
        if not row:
            return {"recorded": False, "mode": "agent", "allow_escalation": False, "promoted": False}
        change = (
            self.connection.execute(
                select(s.route_changes).where(
                    s.route_changes.c.run_id == run_id, s.route_changes.c.agent_id == agent_id
                )
            )
            .mappings()
            .first()
        )
        model = self._configuration_revision(agent_id, run["model_revision_id"])
        return {
            **dict(row),
            "recorded": True,
            "mode": change["to_mode"] if change else row["initial_mode"],
            "promoted": bool(change),
            "model_revision_id": run["model_revision_id"],
            "model": model["value"]["model"],
            "model_name": model["name"],
            "change": dict(change) if change else None,
        }

    def route_reservation(self, agent_id: str, run_id: str, default: int) -> int:
        route = self.run_route(agent_id, run_id)
        return int(route["reservation_micro"]) if route["recorded"] else default

    def route_promote(self, agent_id: str, run_id: str, target: str) -> dict:
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
        if target not in MODES or MODES.index(target) <= MODES.index(route["mode"]):
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
        self, agent_id: str, run_id: str, call_id: str, tools: Sequence[ToolSpec], baseline: Sequence[ToolSpec]
    ) -> None:
        route = self.run_route(agent_id, run_id)
        if not route["recorded"]:
            return
        expected = select_tools(
            route["mode"], baseline, escalate=bool(route["allow_escalation"] and not route["promoted"])
        )
        if encoded(expected) != encoded(tools):
            raise invalid("routing_toolset_changed")
        selected_bytes, full_bytes = encoded(tools), encoded(baseline)
        row = self._record(
            s.model_call_routes,
            {
                "call_id": call_id,
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

    def route_report(self, agent_id: str, run_id: str) -> dict:
        route = self.run_route(agent_id, run_id)
        rows = [
            dict(r)
            for r in self.connection.execute(
                select(
                    s.model_call_routes,
                    s.model_calls.c.state.label("call_state"),
                    s.model_calls.c.cost_micro,
                    s.model_calls.c.input_token_estimate,
                    s.model_calls.c.counting_method,
                )
                .join(s.model_calls, s.model_calls.c.id == s.model_call_routes.c.call_id)
                .where(s.model_call_routes.c.agent_id == agent_id, s.model_calls.c.run_id == run_id)
                .order_by(s.model_call_routes.c.ordinal)
                .limit(65)
            ).mappings()
        ]
        for row in rows:
            row["tools"] = [
                dict(t)
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
        return {
            "route": route,
            "calls": rows[:64],
            "has_more": len(rows) > 64,
            "known_cost_micro": totals[0],
            "unsettled_calls": totals[1],
            "unrecorded_turn_calls": max(0, totals[2] - len(rows)),
            "currency": "USD",
            "routing_model_calls": 0,
            "byte_delta": sum(r["full_tool_bytes"] - r["tool_bytes"] for r in rows[:64]),
            "savings_claim": "tool_schema_bytes_only_not_real_cost_or_quality",
        }
