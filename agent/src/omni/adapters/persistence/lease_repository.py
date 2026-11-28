"""Persistent FIFO claims and fenced resource leases, within the caller's transaction."""

from __future__ import annotations

import datetime as dt
import hashlib
import json
from typing import cast

from sqlalchemy import Table, func, insert, literal, or_, select, update
from sqlalchemy.sql.elements import ColumnElement

from ...application.ports import Record
from ...domain.contracts import BusinessError
from ...domain.scheduling import LEASE_SECONDS, instant, timestamp
from . import schema as s
from .action_predicates import ACTIVE_ACTION_STATES, unresolved_action
from .delivery_predicates import held_outbound, pending_for_run, unresolved_outbound
from .repository_base import RepositoryBase

LIVE = ("queued", "running", "waiting_approval", "cancelling")


class LeaseRepositories(RepositoryBase):
    def require_turn_capacity(self, count: int) -> None:
        queued, live = self.connection.execute(
            select(func.count().filter(s.runs.c.state == "queued"), func.count()).where(s.runs.c.state.in_(LIVE))
        ).one()
        if queued + count > 64 or live + count > 68:
            raise BusinessError("queue_full", "Execution queue is full", retryable=True, status=429)

    def lease_acquire(self, agent_id: str, resource: str, run_id: str | None = None) -> Record | None:
        key = (s.leases.c.agent_id == agent_id, s.leases.c.resource == resource)
        old = self.connection.execute(select(s.leases).where(*key)).mappings().first()
        now = self.clock.now()
        if old and old["state"] == "held":
            if old["expires_at"] > now:
                return None
            # Expiration fences the old caller, but is not proof it has stopped.
            if old["run_id"] and self._one(s.runs, agent_id, cast(str, old["run_id"]))["state"] in LIVE:
                return None
        value = {
            "token": self.ids.new(),
            "boot_id": self.boot_id,
            "fence": cast(int, old["fence"]) + 1 if old else 1,
            "expires_at": timestamp(instant(now) + dt.timedelta(seconds=LEASE_SECONDS)),
            "state": "held",
            "run_id": run_id,
        }
        if old:
            self.connection.execute(update(s.leases).where(*key).values(**value))
        else:
            self.connection.execute(insert(s.leases).values(agent_id=agent_id, resource=resource, **value))
        return cast(Record, {"agent_id": agent_id, "resource": resource, **value})

    def lease_release(self, agent_id: str, resource: str, token: str, fence: int) -> bool:
        return (
            self.connection.execute(
                update(s.leases)
                .where(
                    s.leases.c.agent_id == agent_id,
                    s.leases.c.resource == resource,
                    s.leases.c.token == token,
                    s.leases.c.fence == fence,
                    s.leases.c.boot_id == self.boot_id,
                    s.leases.c.state == "held",
                )
                .values(state="released")
            ).rowcount
            == 1
        )

    def _work(self, agent_id: str, run_id: str) -> Record:
        row = (
            self.connection.execute(
                select(s.work_items).where(
                    s.work_items.c.agent_id == agent_id,
                    s.work_items.c.run_id == run_id,
                    s.work_items.c.kind == "turn",
                )
            )
            .mappings()
            .one()
        )
        return cast(Record, dict(row))

    def _requeue_turn(self, run_id: str, work_id: str) -> None:
        self.connection.execute(update(s.runs).where(s.runs.c.id == run_id).values(state="queued", ended_at=None))
        self.connection.execute(
            update(s.work_items)
            .where(s.work_items.c.id == work_id)
            .values(state="pending", claim_token=None, lease_fence=None, claimed_by=None)
        )

    def assert_run_lease(self, agent_id: str, run_id: str) -> None:
        work = self._work(agent_id, run_id)
        if work["state"] not in {"claimed", "running"}:
            return
        run = self._one(s.runs, agent_id, run_id)
        lease = self.connection.execute(
            select(s.leases).where(
                s.leases.c.agent_id == agent_id,
                s.leases.c.resource == "session/" + cast(str, run["session_id"]),
                s.leases.c.token == work["claim_token"],
                s.leases.c.fence == work["lease_fence"],
                s.leases.c.boot_id == self.boot_id,
                s.leases.c.run_id == run_id,
                s.leases.c.state == "held",
                s.leases.c.expires_at > self.clock.now(),
            )
        ).first()
        if not lease or work["claimed_by"] != self.boot_id:
            raise BusinessError("run_lease_lost", "Execution ownership expired or changed")
        occurrence = (
            self.connection.execute(select(s.occurrences).where(s.occurrences.c.run_id == run_id)).mappings().first()
        )
        if occurrence and self.schedule_blocker(
            agent_id, cast(str, occurrence["schedule_id"]), cast(int, occurrence["sequence"])
        ):
            raise BusinessError("previous_outcome_unknown", "A previous scheduled outcome requires review")

    def renew_run_leases(self) -> None:
        now = self.clock.now()
        self.connection.execute(
            update(s.leases)
            .where(
                s.leases.c.boot_id == self.boot_id,
                s.leases.c.state == "held",
                s.leases.c.expires_at > now,
                s.leases.c.run_id.in_(select(s.runs.c.id).where(s.runs.c.state.in_(LIVE))),
            )
            .values(expires_at=timestamp(instant(now) + dt.timedelta(seconds=LEASE_SECONDS)))
        )

    def schedule_blocker(self, agent_id: str, schedule_id: str, before_sequence: int | None = None) -> Record | None:
        progress = (
            self.connection.execute(
                select(s.schedule_progress).where(
                    s.schedule_progress.c.agent_id == agent_id, s.schedule_progress.c.schedule_id == schedule_id
                )
            )
            .mappings()
            .one()
        )
        active_delivery = pending_for_run(cast(str, s.occurrences.c.run_id))
        specs = (
            (s.model_calls, ("reserved", "unknown")),
            (s.actions, ("executing", "unknown")),
            (s.outbox, ("unknown",)),
        )

        def conditions(table: Table, states: tuple[str, ...], occurrence_id: object) -> list[ColumnElement[bool]]:
            column = {"model_calls": "model_call_id", "actions": "action_id", "outbox": "delivery_id"}[table.name]
            clauses: list[ColumnElement[bool]] = [
                s.schedule_reviews.c.occurrence_id == occurrence_id,
                s.schedule_reviews.c[column] == table.c.id,
                s.schedule_reviews.c.state == table.c.state,
                s.schedule_reviews.c.review_epoch == progress["review_epoch"],
            ]
            if table is s.outbox:
                clauses.append(s.schedule_reviews.c.delivery_revision == table.c.outcome_revision)
            reviewed = select(s.schedule_reviews.c.id).where(*clauses).correlate(s.occurrences, table).exists()
            if table is s.actions:
                # An old continuation acknowledgment is no proof that an active
                # executor stopped. Only terminal unknown observations may be acknowledged.
                return [or_(table.c.state.in_(ACTIVE_ACTION_STATES), unresolved_action(table) & ~reviewed)]
            values: list[ColumnElement[bool]] = [table.c.state.in_(states), ~reviewed]
            if table is s.model_calls:
                # Only turn calls can carry an unreviewed external outcome. Auxiliary summary
                # calls (run compaction, post-turn review) are internal bookkeeping with no
                # external effect; counting them stalled the next occurrence for the whole
                # review window (K13 delivery blocker, contract V2_SCHEDULE_REVIEW_BLOCKER).
                values.append(table.c.purpose == "turn")
            if table is s.outbox:
                values.extend((unresolved_outbound(), ~held_outbound()))
            return values

        unknown_exists = [
            select(table.c.id)
            .where(table.c.run_id == s.occurrences.c.run_id, *conditions(table, states, s.occurrences.c.id))
            .exists()
            for table, states in specs
        ]
        query = (
            select(s.occurrences, s.runs.c.state.label("run_state"), active_delivery.label("delivery_pending"))
            .outerjoin(s.runs, s.runs.c.id == s.occurrences.c.run_id)
            .where(
                s.occurrences.c.agent_id == agent_id,
                s.occurrences.c.schedule_id == schedule_id,
                or_(
                    s.runs.c.state.in_(LIVE),
                    s.occurrences.c.run_id.is_(None) & s.occurrences.c.state.in_(("pending", "blocked")),
                    active_delivery,
                    *unknown_exists,
                ),
            )
        )
        if before_sequence is not None:
            query = query.where(s.occurrences.c.sequence < before_sequence)
        row = self.connection.execute(query.order_by(s.occurrences.c.sequence).limit(1)).mappings().first()
        if row is None:
            return None
        base: Record = {"id": row["id"], "sequence": row["sequence"], "run_id": row["run_id"]}
        if row["run_state"] in LIVE or row["run_id"] is None:
            return {**base, "reason": "active"}
        if row["delivery_pending"]:
            return {**base, "reason": "delivery_pending"}
        unknown: list[tuple[str, str, str, int]] = []
        for table, states in specs:
            revision = table.c.outcome_revision if table is s.outbox else literal(0)
            unknown.extend(
                (table.name, r[0], r[1], r[2])
                for r in self.connection.execute(
                    select(table.c.id, table.c.state, revision)
                    .where(table.c.run_id == row["run_id"], *conditions(table, states, row["id"]))
                    .order_by(table.c.id)
                )
            )
        if not unknown:
            return None
        if any(kind == "actions" and state in ACTIVE_ACTION_STATES for kind, _identity, state, _revision in unknown):
            return {**base, "reason": "active", "records": len(unknown)}
        if any(kind == "actions" and state != "unknown" for kind, _identity, state, _revision in unknown):
            return {**base, "reason": "action_review_required", "records": len(unknown)}
        fingerprint = hashlib.sha256(
            json.dumps([progress["review_epoch"], unknown], separators=(",", ":")).encode()
        ).hexdigest()
        return {
            **base,
            "reason": "unknown",
            "fingerprint": fingerprint,
            "records": len(unknown),
            "items": [
                {"kind": kind, "id": identity, "state": state, "revision": revision}
                for kind, identity, state, revision in unknown
            ],
        }

    def _claim_run(self, agent_id: str, run_id: str) -> Record | None:
        run = self._one(s.runs, agent_id, run_id)
        work = self._work(agent_id, run_id)
        if run["state"] != "queued" or work["state"] != "pending":
            return None
        occurrence = (
            self.connection.execute(select(s.occurrences).where(s.occurrences.c.run_id == run_id)).mappings().first()
        )
        if occurrence and self.schedule_blocker(
            agent_id, cast(str, occurrence["schedule_id"]), cast(int, occurrence["sequence"])
        ):
            return None
        message = self._one(s.messages, agent_id, cast(str, run["input_message_id"]))
        earlier = self.connection.execute(
            select(s.runs.c.id)
            .join(s.messages, s.messages.c.id == s.runs.c.input_message_id)
            .join(s.work_items, (s.work_items.c.run_id == s.runs.c.id) & (s.work_items.c.kind == "turn"))
            .where(
                s.runs.c.session_id == run["session_id"],
                s.runs.c.state.in_(LIVE),
                s.messages.c.seq < message["seq"],
                (s.runs.c.state != "queued") | func.json_extract(s.work_items.c.payload, "$.owa_delivery").is_not(True),
            )
            .limit(1)
        ).first()
        if earlier:
            return None
        lease = self.lease_acquire(agent_id, "session/" + cast(str, run["session_id"]), run_id)
        if not lease:
            return None
        self.connection.execute(
            update(s.work_items)
            .where(s.work_items.c.id == work["id"])
            .values(
                state="claimed",
                claimed_by=self.boot_id,
                claim_token=lease["token"],
                lease_fence=lease["fence"],
                attempt=cast(int, work["attempt"]) + 1,
            )
        )
        payload = work["payload"]
        job_id = str(payload["owa_job"]) if isinstance(payload, dict) and payload.get("owa_job") else None
        workspace_id = None
        if job_id:
            task_id = self.connection.execute(
                select(s.owa_jobs.c.task_id).where(s.owa_jobs.c.agent_id == agent_id, s.owa_jobs.c.id == job_id)
            ).scalar_one_or_none()
            workspace_id = (
                str(
                    self.connection.execute(
                        select(s.owa_tasks.c.run_id).where(
                            s.owa_tasks.c.agent_id == agent_id, s.owa_tasks.c.id == task_id
                        )
                    ).scalar_one()
                )
                if task_id
                else None
            )
        return {
            **run,
            "claim_token": lease["token"],
            "lease_fence": lease["fence"],
            "owa_delivery": isinstance(payload, dict) and payload.get("owa_delivery") is True,
            "owa_job": job_id,
            "worker_workspace_id": workspace_id,
        }

    def claim_runs(
        self, limit: int, *, include_owa_deliveries: bool = False, include_owa_jobs: bool = False
    ) -> list[Record]:
        result: list[Record] = []
        rows = self.connection.execute(
            select(s.work_items.c.agent_id, s.work_items.c.run_id)
            .join(s.runs, s.runs.c.id == s.work_items.c.run_id)
            .join(
                s.agents,
                s.agents.c.id == s.work_items.c.agent_id,
            )
            .where(
                s.work_items.c.kind == "turn",
                s.work_items.c.state == "pending",
                s.agents.c.enabled.is_(True),
                s.runs.c.parent_id.is_(None),
                *(
                    ()
                    if include_owa_deliveries
                    else (func.json_extract(s.work_items.c.payload, "$.owa_delivery").is_not(True),)
                ),
                *(() if include_owa_jobs else (func.json_extract(s.work_items.c.payload, "$.owa_job").is_(None),)),
            )
            .order_by(s.work_items.c.queue_sequence)
            .limit(64)
        ).all()
        for agent_id, run_id in rows:
            if len(result) >= max(0, min(4, limit)):
                break
            claim = self._claim_run(agent_id, run_id)
            if claim:
                result.append(claim)
        return result

    def start_claimed_run(self, agent_id: str, run_id: str, token: str, fence: int) -> None:
        work = self._work(agent_id, run_id)
        if work["state"] != "claimed" or work["claim_token"] != token or work["lease_fence"] != fence:
            raise BusinessError("run_claim_lost", "Task is no longer owned by this execution")
        self.assert_run_lease(agent_id, run_id)
