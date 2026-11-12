"""Immutable schedule definitions, durable due cursors and atomic occurrence admission."""

from __future__ import annotations

from collections.abc import Callable, Mapping
from typing import Any

from sqlalchemy import func, insert, literal, or_, select, update

from ...application.ports import Record
from ...domain.contracts import BusinessError
from . import schema as s
from .delivery_predicates import pending_for_run
from .lease_repository import LeaseRepositories

FIELDS = ("kind", "timezone", "expression", "interval_seconds", "start_at", "session_id", "prompt", "misfire")


class ScheduleRepositories(LeaseRepositories):
    def _schedule_agent(self, agent_id: str) -> dict:
        row = self.connection.execute(select(s.agents).where(s.agents.c.id == agent_id)).mappings().first()
        if not row:
            raise BusinessError("agent_not_found", "Agent does not exist", status=404)
        return dict(row)

    def schedule_get(self, agent_id: str, schedule_id: str) -> dict:
        root = self._one(s.schedules, agent_id, schedule_id)
        revision = (
            self.connection.execute(
                select(s.schedule_revisions).where(
                    s.schedule_revisions.c.schedule_id == schedule_id,
                    s.schedule_revisions.c.revision == root["revision"],
                )
            )
            .mappings()
            .one()
        )
        progress = (
            self.connection.execute(
                select(s.schedule_progress).where(
                    s.schedule_progress.c.schedule_id == schedule_id,
                )
            )
            .mappings()
            .one()
        )
        return {
            **root,
            "value": {k: revision[k] for k in FIELDS},
            "tzdb_version": revision["tzdb_version"],
            "progress": dict(progress),
            "blocker": self.schedule_blocker(agent_id, schedule_id),
        }

    def schedule_page(
        self, agent_id: str, after: str = "", limit: int = 100, include_archived: bool = False
    ) -> list[dict]:
        self._schedule_agent(agent_id)
        ids = self.connection.execute(
            select(s.schedules.c.id)
            .where(
                s.schedules.c.agent_id == agent_id,
                s.schedules.c.id > after,
                or_(s.schedules.c.deleted_at.is_(None), literal(include_archived)),
            )
            .order_by(s.schedules.c.id)
            .limit(max(1, min(200, limit)))
        ).scalars()
        return [self.schedule_get(agent_id, key) for key in ids]

    def schedule_save(
        self,
        agent_id: str,
        name: str,
        enabled: bool,
        value: dict,
        tzdb_version: str,
        next_at: str | None,
        schedule_id: str | None = None,
        revision: int = 0,
    ) -> dict:
        self._schedule_agent(agent_id)
        if not name.strip() or len(name) > 100 or type(enabled) is not bool:
            raise BusinessError("invalid_schedule", "A name and explicit enabled state are required", status=422)
        old = self.schedule_get(agent_id, schedule_id) if schedule_id else None
        if old and (old["revision"] != revision or old["deleted_at"]):
            raise BusinessError("revision_conflict", "Schedule changed or was archived")
        if not old and revision != 0:
            raise BusinessError("revision_conflict", "A new schedule requires revision zero")
        if not old and self.connection.execute(select(func.count()).select_from(s.schedules)).scalar_one() >= 4096:
            raise BusinessError("schedule_limit", "Schedule storage limit reached", status=429)
        active = (
            select(func.count())
            .select_from(s.schedules)
            .where(s.schedules.c.enabled.is_(True), s.schedules.c.deleted_at.is_(None))
        )
        if enabled and not (old and old["enabled"]) and self.connection.execute(active).scalar_one() >= 256:
            raise BusinessError("schedule_limit", "At most 256 schedules may be enabled", status=429)
        value = dict(value)
        session_id = value["session_id"] or (old["value"]["session_id"] if old else None)
        if session_id:
            if self._one(s.sessions, agent_id, session_id)["deleted_at"]:
                raise BusinessError("session_deleted", "Schedule target session was erased")
        else:
            session_id = self._record(s.sessions, {"title": name, "revision": 1, "next_seq": 0}, agent_id)["id"]
        value["session_id"] = session_id
        if old:
            self.connection.execute(
                update(s.schedules)
                .where(s.schedules.c.id == schedule_id)
                .values(
                    name=name,
                    enabled=enabled,
                    revision=revision + 1,
                )
            )
        else:
            root = self._record(s.schedules, {"name": name, "enabled": enabled, "revision": 1}, agent_id)
            schedule_id = root["id"]
        definition = self._record(
            s.schedule_revisions,
            {
                "schedule_id": schedule_id,
                "revision": revision + 1,
                "name": name,
                "enabled": enabled,
                "tzdb_version": tzdb_version,
                **value,
            },
            agent_id,
        )
        progress = {
            "revision_id": definition["id"],
            "next_at": next_at if enabled else None,
            "error_code": "",
            "updated_at": self.clock.now(),
        }
        if old:
            self.connection.execute(
                update(s.schedule_progress)
                .where(s.schedule_progress.c.schedule_id == schedule_id)
                .values(**progress, revision=s.schedule_progress.c.revision + 1)
            )
        else:
            self.connection.execute(
                insert(s.schedule_progress).values(
                    schedule_id=schedule_id, agent_id=agent_id, sequence=0, reviewed_sequence=0, revision=1, **progress
                )
            )
        if not enabled:
            self.connection.execute(
                update(s.occurrences)
                .where(
                    s.occurrences.c.schedule_id == schedule_id,
                    s.occurrences.c.run_id.is_(None),
                    s.occurrences.c.state.in_(("pending", "blocked")),
                )
                .values(state="cancelled", error_code="schedule_disabled", updated_at=self.clock.now())
            )
        self.audit(agent_id, "schedule.saved", schedule_id, {"revision": revision + 1, "enabled": enabled})
        assert schedule_id is not None
        return self.schedule_get(agent_id, schedule_id)

    def schedule_archive(self, agent_id: str, schedule_id: str, revision: int) -> None:
        row = self.schedule_get(agent_id, schedule_id)
        if row["revision"] != revision or row["deleted_at"]:
            raise BusinessError("revision_conflict", "Schedule changed or was archived")
        self.connection.execute(
            update(s.schedules).where(s.schedules.c.id == schedule_id).values(deleted_at=self.clock.now())
        )
        self.connection.execute(
            update(s.schedule_progress)
            .where(s.schedule_progress.c.schedule_id == schedule_id)
            .values(
                next_at=None, error_code="", revision=s.schedule_progress.c.revision + 1, updated_at=self.clock.now()
            )
        )
        self.connection.execute(
            update(s.occurrences)
            .where(
                s.occurrences.c.schedule_id == schedule_id,
                s.occurrences.c.run_id.is_(None),
                s.occurrences.c.state.in_(("pending", "blocked")),
            )
            .values(state="cancelled", error_code="schedule_archived", updated_at=self.clock.now())
        )
        self.audit(agent_id, "schedule.archived", schedule_id, {"revision": revision})

    def schedule_history(self, agent_id: str, schedule_id: str, after: int = 0, limit: int = 50) -> list[dict]:
        self._one(s.schedules, agent_id, schedule_id)
        return [
            dict(r)
            for r in self.connection.execute(
                select(s.schedule_revisions)
                .where(
                    s.schedule_revisions.c.schedule_id == schedule_id,
                    s.schedule_revisions.c.revision > after,
                )
                .order_by(s.schedule_revisions.c.revision)
                .limit(min(200, max(1, limit)))
            ).mappings()
        ]

    def schedule_occurrences(self, agent_id: str, schedule_id: str, after: int = 0, limit: int = 50) -> list[dict]:
        self._one(s.schedules, agent_id, schedule_id)
        return [
            dict(r)
            for r in self.connection.execute(
                select(
                    s.occurrences,
                    s.runs.c.state.label("run_state"),
                    s.runs.c.error_code.label("run_error_code"),
                    s.schedule_revisions.c.revision.label("definition_revision"),
                    pending_for_run(s.occurrences.c.run_id).label("delivery_pending"),
                )
                .join(s.schedule_revisions, s.schedule_revisions.c.id == s.occurrences.c.revision_id)
                .outerjoin(s.runs, s.runs.c.id == s.occurrences.c.run_id)
                .where(
                    s.occurrences.c.schedule_id == schedule_id,
                    s.occurrences.c.sequence > after,
                )
                .order_by(s.occurrences.c.sequence)
                .limit(min(200, max(1, limit)))
            ).mappings()
        ]

    def schedule_candidates(self, after: str = "", limit: int = 16) -> list[dict]:
        pending = (
            select(s.occurrences.c.id)
            .where(
                s.occurrences.c.schedule_id == s.schedules.c.id,
                s.occurrences.c.state.in_(("pending", "blocked")),
                s.occurrences.c.run_id.is_(None),
            )
            .exists()
        )
        ids = self.connection.execute(
            select(s.schedules.c.agent_id, s.schedules.c.id)
            .join(
                s.schedule_progress,
                s.schedule_progress.c.schedule_id == s.schedules.c.id,
            )
            .join(s.agents, s.agents.c.id == s.schedules.c.agent_id)
            .where(
                s.schedules.c.id > after,
                s.schedules.c.enabled.is_(True),
                s.schedules.c.deleted_at.is_(None),
                s.agents.c.enabled.is_(True),
                or_(s.schedule_progress.c.next_at <= self.clock.now(), pending),
            )
            .order_by(s.schedules.c.id)
            .limit(min(16, max(1, limit)))
        ).all()
        return [self.schedule_get(agent, key) for agent, key in ids]

    def schedule_materialize(
        self, captured: Mapping[str, Any], selection: Mapping[str, Any] | None, tzdb_version: str
    ) -> dict | None:
        agent, key = captured["agent_id"], captured["id"]
        row = self.schedule_get(agent, key)
        if row["deleted_at"] or not row["enabled"] or row["revision"] != captured["revision"]:
            return None
        if row["progress"]["error_code"] == "restored_schedule_paused":
            return None
        if not self._schedule_agent(agent)["enabled"]:
            return None
        error = "timezone_data_changed" if row["tzdb_version"] != tzdb_version else ""
        blocker = row["blocker"]
        if blocker and blocker["reason"] == "unknown":
            error = "previous_outcome_unknown"
        if error:
            self.schedule_error(agent, key, error)
            return None
        if blocker:
            if blocker["run_id"]:
                return None
            pending = self._one(s.occurrences, agent, blocker["id"])
            return {**pending, "value": dict(self._one(s.schedule_revisions, agent, pending["revision_id"]))}
        if selection is None or row["progress"]["revision"] != captured["progress"]["revision"]:
            return None
        if row["progress"]["next_at"] is None or row["progress"]["next_at"] > self.clock.now():
            return None
        if selection["scheduled_at"] > self.clock.now():
            return None
        if row["progress"]["last_scheduled_at"] and selection["scheduled_at"] <= row["progress"]["last_scheduled_at"]:
            raise BusinessError("invalid_schedule_cursor", "Schedule cannot repeat a previous due identity")
        occurrence = self._record(
            s.occurrences,
            {
                "schedule_id": key,
                "revision_id": row["progress"]["revision_id"],
                "sequence": row["progress"]["sequence"] + 1,
                "state": "pending",
                "error_code": "",
                "updated_at": self.clock.now(),
                **{k: selection[k] for k in ("scheduled_at", "local_time", "coalesced_from")},
            },
            agent,
        )
        self.connection.execute(
            update(s.schedule_progress)
            .where(s.schedule_progress.c.schedule_id == key)
            .values(
                next_at=selection["next_at"],
                last_scheduled_at=selection["scheduled_at"],
                sequence=occurrence["sequence"],
                revision=s.schedule_progress.c.revision + 1,
                error_code="",
                updated_at=self.clock.now(),
            )
        )
        self.audit(agent, "schedule.due", occurrence["id"], {"schedule_id": key, "sequence": occurrence["sequence"]})
        return {**occurrence, "value": row["value"]}

    def schedule_error(self, agent_id: str, schedule_id: str, code: str) -> None:
        self._one(s.schedules, agent_id, schedule_id)
        self.connection.execute(
            update(s.schedule_progress)
            .where(s.schedule_progress.c.schedule_id == schedule_id)
            .values(error_code=code, updated_at=self.clock.now())
        )

    def schedule_enqueue(
        self, occurrence: Mapping[str, Any], accept: Callable[[Mapping[str, object], str], Record]
    ) -> dict | None:
        """A savepoint keeps the due ledger even when admission fails before commit."""
        agent, key = occurrence["agent_id"], occurrence["id"]
        try:
            with self.connection.begin_nested():
                receipt = accept(occurrence["value"], "schedule:" + key)
                self.connection.execute(
                    update(s.occurrences)
                    .where(s.occurrences.c.id == key)
                    .values(
                        state="enqueued",
                        run_id=receipt["run_id"],
                        error_code="",
                        updated_at=self.clock.now(),
                    )
                )
                self.schedule_error(agent, occurrence["schedule_id"], "")
                self.audit(agent, "schedule.enqueued", key, {"run_id": receipt["run_id"]})
                return receipt
        except BusinessError as exc:
            self.connection.execute(
                update(s.occurrences)
                .where(s.occurrences.c.id == key)
                .values(
                    state="blocked",
                    error_code=exc.code,
                    updated_at=self.clock.now(),
                )
            )
            self.schedule_error(agent, occurrence["schedule_id"], exc.code)
            return None

    def schedule_review(self, agent_id: str, schedule_id: str, occurrence_id: str, fingerprint: str) -> None:
        row = self.schedule_get(agent_id, schedule_id)
        blocker = row["blocker"]
        if (
            not blocker
            or blocker["id"] != occurrence_id
            or blocker["reason"] != "unknown"
            or blocker["fingerprint"] != fingerprint
        ):
            raise BusinessError("review_conflict", "Unknown outcome changed; inspect the current records again")
        for item in blocker["items"]:
            column = {"model_calls": "model_call_id", "actions": "action_id", "outbox": "delivery_id"}[item["kind"]]
            self._record(
                s.schedule_reviews,
                {
                    "occurrence_id": occurrence_id,
                    column: item["id"],
                    "state": item["state"],
                    "delivery_revision": item["revision"] if item["kind"] == "outbox" else None,
                    "fingerprint": fingerprint,
                    "review_epoch": row["progress"]["review_epoch"],
                },
                agent_id,
            )
        self.connection.execute(
            update(s.schedule_progress)
            .where(s.schedule_progress.c.schedule_id == schedule_id)
            .values(
                reviewed_sequence=max(row["progress"]["reviewed_sequence"], blocker["sequence"]),
                revision=s.schedule_progress.c.revision + 1,
                error_code="",
                updated_at=self.clock.now(),
            )
        )
        self.audit(
            agent_id,
            "schedule.continuation_reviewed",
            occurrence_id,
            {"fingerprint": fingerprint, "sequence": blocker["sequence"], "unknown_records_preserved": True},
        )

    def schedule_cancel(self, agent_id: str, schedule_id: str, occurrence_id: str) -> str | None:
        self._one(s.schedules, agent_id, schedule_id)
        row = self._one(s.occurrences, agent_id, occurrence_id)
        if row["schedule_id"] != schedule_id:
            raise BusinessError("not_found", "Occurrence does not belong to this schedule", status=404)
        if not row["run_id"] and row["state"] in {"pending", "blocked"}:
            self.connection.execute(
                update(s.occurrences)
                .where(s.occurrences.c.id == occurrence_id)
                .values(state="cancelled", error_code="owner_cancelled", updated_at=self.clock.now())
            )
        self.audit(agent_id, "schedule.occurrence_cancel_requested", occurrence_id, {"run_id": row["run_id"]})
        return row["run_id"]
