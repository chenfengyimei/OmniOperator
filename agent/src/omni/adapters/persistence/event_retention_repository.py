"""Preview and prune retired run events through the shared history cycle; never remove run identities."""

from __future__ import annotations

import re
from typing import cast

from sqlalchemy import Label, case, func, select
from sqlalchemy.sql.elements import ColumnElement

from ...application.ports import Record
from ...domain.contracts import BusinessError
from ...domain.event_retention import (
    FORMAT,
    KINDS,
    PLAN_FORMAT,
    POLICY,
    decode_preview_cursor,
    encode_preview_cursor,
    preview_time,
    validate_event_plan,
)
from ...domain.retention import body_fingerprint
from . import schema as s
from .history_protection import HistoryProtectionRepositories
from .history_pruning_cycle import PruningCycle, PruningSpec

EVENT_SPEC = PruningSpec(
    "events",
    "event_retention",
    s.run_events,
    s.event_retention_batches,
    s.event_retention_targets,
    "sequence",
    "sequence",
    (("sequence", "sequence"), ("run_id", "run_id"), ("seq", "seq"), ("kind", "kind")),
    "event_rows_restored",
    validate_event_plan,
    s.audit_retention_batches,
)


def _event_columns() -> tuple[ColumnElement[object] | Label[object], ...]:
    # Unpruned or malformed payloads do not supply provenance. Only the few
    # identifiers preserved by body pruning are projected, never the JSON body.
    def field(name: str) -> Label[object]:
        return case(
            (
                s.run_events.c.body_pruned_at.is_not(None),
                case(
                    (func.json_valid(s.run_events.c.data), func.json_extract(s.run_events.c.data, "$." + name)),
                    else_=None,
                ),
            ),
            else_=None,
        ).label("payload_" + name)

    return (
        s.run_events.c.sequence,
        s.run_events.c.agent_id,
        s.run_events.c.run_id,
        s.run_events.c.seq,
        s.run_events.c.kind,
        s.run_events.c.created_at,
        s.run_events.c.body_pruned_at,
        s.run_events.c.data_sha256,
        field("action_id"),
        field("status"),
        field("effect"),
        field("body_pruned"),
    )


class EventRetentionRepositories(HistoryProtectionRepositories):
    def _event_candidates(self, agent_id: str, rows: list[Record], cutoff: str, batch_id: str = "") -> list[Record]:
        runs: dict[str, Record | None] = {}
        protections: dict[str, list[str]] = {}
        actions: dict[str, Record | None] = {}
        items: list[Record] = []
        for row in rows:
            reasons: list[str] = []
            if row["kind"] not in KINDS:
                reasons.append("event_kind_not_eligible")
            run_id = cast(str, row["run_id"])
            if run_id not in runs:
                run = (
                    self.connection.execute(
                        select(s.runs).where(
                            s.runs.c.agent_id == agent_id,
                            s.runs.c.id == run_id,
                        )
                    )
                    .mappings()
                    .first()
                )
                runs[run_id] = cast(Record, dict(run)) if run else None
            run = runs[run_id]
            if run is None:
                reasons.append("event_run_missing")
            else:
                identity = cast(str, run["id"])
                if identity not in protections:
                    protections[identity] = self._history_run_protections(
                        run, cutoff, stream="events", batch_id=batch_id
                    )
                reasons.extend(protections[identity])
                if (
                    not row["body_pruned_at"]
                    or row["body_pruned_at"] != run["body_pruned_at"]
                    or not isinstance(cast(object, row["data_sha256"]), str)
                    or re.fullmatch(r"[0-9a-f]{64}", cast(str, row["data_sha256"])) is None
                    or row["payload_body_pruned"] != 1
                ):
                    reasons.append("event_body_not_pruned")
                if row["kind"] in {"run.succeeded", "run.failed", "run.cancelled"} and run["state"] != row[
                    "kind"
                ].removeprefix("run."):
                    reasons.append("event_state_mismatch")
                if row["kind"] in {"action.prepared", "action.completed"}:
                    action_id = row["payload_action_id"]
                    action: Record | None = None
                    if isinstance(action_id, str) and len(action_id) == 36:
                        if action_id not in actions:
                            found = (
                                self.connection.execute(
                                    select(
                                        s.actions.c.id,
                                        s.actions.c.agent_id,
                                        s.actions.c.run_id,
                                        s.actions.c.state,
                                        s.actions.c.effect,
                                        s.actions.c.body_pruned_at,
                                    ).where(s.actions.c.id == action_id, s.actions.c.agent_id == agent_id)
                                )
                                .mappings()
                                .first()
                            )
                            actions[action_id] = cast(Record, dict(found)) if found else None
                        action = actions[action_id]
                    if not action or action["run_id"] != run["id"]:
                        reasons.append("event_action_missing")
                    else:
                        if action["body_pruned_at"] != run["body_pruned_at"]:
                            reasons.append("event_body_not_pruned")
                        if row["kind"] == "action.completed" and (
                            row["payload_status"] not in {"ok", "error", "denied", "cancelled"}
                            or action["state"] != row["payload_status"]
                            or action["effect"] != row["payload_effect"]
                            or row["payload_effect"] == "unknown"
                        ):
                            reasons.append("event_state_mismatch")
            items.append(
                cast(
                    Record,
                    {
                        **{key: row[key] for key in ("sequence", "agent_id", "run_id", "seq", "kind", "created_at")},
                        "eligible": not reasons,
                        "protected_reasons": list(dict.fromkeys(reasons)),
                    },
                )
            )
        return items

    def event_retention_preview(self, agent_id: str, *, cursor: str = "", limit: int = 20, as_of: str = "") -> Record:
        if (
            type(limit) is not int
            or not 1 <= limit <= 50
            or not isinstance(cast(object, cursor), str)
            or not isinstance(cast(object, as_of), str)
        ):
            raise BusinessError(
                "invalid_event_retention_query", "Use a cursor and a page of 1 to 50 events", status=422
            )
        if not self.connection.execute(select(s.agents.c.id).where(s.agents.c.id == agent_id)).first():
            raise BusinessError("not_found", "Agent does not exist", status=404)
        now = self.clock.now()
        epoch = self.connection.execute(select(s.history_identity.c.epoch)).scalar_one()
        scope = cast(
            Record,
            dict(
                self.connection.execute(
                    select(s.history_scopes).where(
                        s.history_scopes.c.kind == "events",
                        s.history_scopes.c.scope_id == agent_id,
                    )
                )
                .mappings()
                .one()
            ),
        )
        current = self.connection.execute(
            select(s.history_sequences.c.last_issued).where(
                s.history_sequences.c.kind == "events",
            )
        ).scalar_one()
        if cursor:
            position = decode_preview_cursor(
                cursor,
                epoch=cast(str, epoch),
                agent_id=agent_id,
                revision=cast(int, scope["trim_revision"]),
                current_high_water=current,
                now=now,
                as_of=as_of,
            )
            as_of, cutoff, cap, after = (
                position["as_of"],
                position["cutoff"],
                position["high_water"],
                position["position"],
            )
        else:
            as_of, cutoff = preview_time(as_of or now, now)
            cap, after = current, 0
        rows = [
            cast(Record, dict(row))
            for row in self.connection.execute(
                select(*_event_columns())
                .where(
                    s.run_events.c.agent_id == agent_id,
                    s.run_events.c.sequence > after,
                    s.run_events.c.sequence <= cap,
                    s.run_events.c.created_at <= cutoff,
                )
                .order_by(s.run_events.c.sequence)
                .limit(limit + 1)
            ).mappings()
        ]
        items = self._event_candidates(agent_id, rows[:limit], cutoff)
        eligible = sum(bool(item["eligible"]) for item in items)
        return cast(
            Record,
            {
                "format": FORMAT,
                "policy": POLICY,
                "agent_id": agent_id,
                "instance_id": self.connection.execute(select(s.instances.c.id)).scalar_one(),
                "schema_version": self.connection.exec_driver_sql(
                    "SELECT version_num FROM alembic_version"
                ).scalar_one(),
                "as_of": as_of,
                "cutoff": cutoff,
                "items": items,
                "counts": {"scanned": len(items), "eligible": eligible, "protected": len(items) - eligible},
                "next_cursor": encode_preview_cursor(
                    cast(str, epoch),
                    agent_id,
                    cast(int, scope["trim_revision"]),
                    as_of,
                    cast(int, cap),
                    cast(int, items[-1]["sequence"]) if items else after,
                ),
                "has_more": len(rows) > limit,
                "history_trimmed": cast(int, scope["deleted_count"]) > 0,
                "stream": {
                    "high_water": cap,
                    **{key: scope[key] for key in ("trim_revision", "deleted_count", "last_trim_at")},
                },
                "can_apply": False,
                "scope": "Aged pruned event metadata only; current protections are rechecked per page",
            },
        )

    def _event_target(self, agent_id: str, sequence: int, cutoff: str, batch_id: str = "") -> Record:
        conditions = (s.run_events.c.agent_id == agent_id, s.run_events.c.sequence == sequence)
        metadata = self.connection.execute(select(*_event_columns()).where(*conditions)).mappings().first()
        if not metadata or cast(str, metadata["created_at"]) > cutoff:
            raise BusinessError("event_retention_conflict", "The original aged event is missing or changed")
        item = self._event_candidates(agent_id, [cast(Record, dict(metadata))], cutoff, batch_id)[0]
        if not item["eligible"]:
            raise BusinessError(
                "event_retention_conflict", "Event provenance or business protection requires retention"
            )
        full = cast(
            Record,
            dict(self.connection.execute(select(s.run_events).where(*conditions)).mappings().one()),
        )
        run = self._one(s.runs, agent_id, cast(str, item["run_id"]))
        return {
            **{key: item[key] for key in ("sequence", "agent_id", "run_id", "seq", "kind")},
            "run_retention_revision": run["retention_revision"],
            "prune_plan_id": run["body_pruned_plan_id"],
            "body_sha256": run["body_sha256"],
            "fingerprint": body_fingerprint([full])[0],
        }

    def event_retention_plan(self, agent_id: str, *, cursor: str = "", limit: int = 20, as_of: str = "") -> Record:
        preview = self.event_retention_preview(agent_id, cursor=cursor, limit=limit, as_of=as_of)
        targets = [
            self._event_target(agent_id, cast(int, row["sequence"]), cast(str, preview["cutoff"]))
            for row in cast(list[Record], preview["items"])
            if row["eligible"]
        ]
        if not targets:
            raise BusinessError(
                "no_event_retention_candidates", "No eligible events in this page; continue the preview"
            )
        return cast(
            Record,
            {
                "format": PLAN_FORMAT,
                "policy": POLICY,
                "id": self.ids.new(),
                "created_at": self.clock.now(),
                **{key: preview[key] for key in ("instance_id", "agent_id", "schema_version", "as_of", "cutoff")},
                "epoch": self.connection.execute(select(s.history_identity.c.epoch)).scalar_one(),
                "trim_revision": cast(Record, preview["stream"])["trim_revision"],
                "high_water": cast(Record, preview["stream"])["high_water"],
                "targets": targets,
            },
        )

    def _event_cycle(self) -> PruningCycle:
        return PruningCycle(self, EVENT_SPEC, self._event_target)

    def event_retention_batch(self, identity: str) -> Record | None:
        return self._event_cycle().batch(identity)

    def event_retention_validate(self, plan: Record) -> None:
        self._event_cycle().validate(plan)

    def event_retention_prepare(self, plan: Record, backup_path: str, backup_sha256: str) -> Record:
        return self._event_cycle().prepare(plan, backup_path, backup_sha256)

    def event_retention_delete(self, plan: Record, target: Record) -> None:
        self._event_cycle().prune(plan, target)

    def event_retention_complete(self, identity: str) -> Record:
        return {**self._event_cycle().complete(identity), "events_deleted": True, "run_records_deleted": False}

    def event_retention_cancel(self, plan: Record, reason: str) -> Record:
        return self._event_cycle().cancel(plan, reason)
