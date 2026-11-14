"""Bounded exact-duplicate proposals and owner review; never changes, forgets or copies memory truth."""

from __future__ import annotations

import hashlib
import json
import re
from typing import cast as tcast

from sqlalchemy import Column, LargeBinary, cast, func, or_, select, update

from ...application.ports import Record
from ...domain.contracts import BusinessError
from ...domain.maintenance import ACTIVE, MAX_PENDING
from ...domain.memory_organization import (
    BUDGET_MICRO,
    MAX_ANCHORS,
    MAX_SOURCE_BYTES,
    MAX_SOURCES,
    POLICY,
    page,
    parse_decision,
    parse_request,
    request_fingerprint,
)
from . import schema as s
from .memory_organization_predicates import live_organization_relations
from .memory_repository import MemoryRepositories

SOURCE_IDENTITY = (
    "id",
    "agent_id",
    "batch_id",
    "memory_id",
    "memory_revision_id",
    "revision",
    "position",
    "raw_sha256",
    "evidence_sha256",
    "person_id",
    "session_id",
)
CAPTURE_IDENTITY = (
    "memory_id",
    "memory_revision_id",
    "revision",
    "raw_sha256",
    "evidence_sha256",
    "person_id",
    "session_id",
)
LIVE_BATCHES = frozenset({"queued", "ready", "complete", "cancelled"})


def _digest(value: object) -> str:
    encoded = json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"), allow_nan=False).encode(
        "utf-8"
    )
    return hashlib.sha256(encoded).hexdigest()


class MemoryOrganizationRepositories(MemoryRepositories):
    def _organization_agent(self, agent_id: str) -> None:
        if not self.connection.execute(select(s.agents.c.id).where(s.agents.c.id == agent_id)).first():
            raise BusinessError("not_found", "Agent does not exist", status=404)

    def _organization_epoch(self) -> str:
        return tcast(str, self.connection.execute(select(s.history_identity.c.epoch)).scalar_one())

    @staticmethod
    def _organization_stale() -> BusinessError:
        return BusinessError("memory_organization_stale", "Captured facts changed; review a new proposal")

    @staticmethod
    def _organization_budget() -> BusinessError:
        return BusinessError(
            "memory_organization_budget",
            "Selected facts and source excerpts exceed the bounded local budget",
            status=422,
        )

    def _organization_capture(
        self, agent_id: str, memory_id: str, revision: int, remaining: int, *, kinds: tuple[str, ...] = ("fact",)
    ) -> tuple[Record, int]:
        # Inspect byte lengths before fetching any potentially large body or excerpt.
        head = (
            self.connection.execute(
                select(
                    *[column for column in s.memory_entries.c if column.name != "content"],
                    tcast(
                        Column[int],
                        func.length(cast(s.memory_entries.c.content, LargeBinary)).label("content_bytes"),
                    ),
                ).where(s.memory_entries.c.agent_id == agent_id, s.memory_entries.c.id == memory_id)
            )
            .mappings()
            .first()
        )
        if head is None:
            raise BusinessError("not_found", "Memory does not exist in this Agent space", status=404)
        if head["deleted_at"] or head["kind"] not in kinds or head["revision"] != revision:
            raise self._organization_stale()
        if (
            head["person_id"] is not None
            and not self.connection.execute(
                select(s.people.c.id).where(
                    s.people.c.agent_id == agent_id, s.people.c.id == head["person_id"], s.people.c.deleted_at.is_(None)
                )
            ).first()
        ):
            raise self._organization_stale()
        version = (
            self.connection.execute(
                select(
                    s.memory_revisions.c.id,
                    tcast(
                        Column[int],
                        func.length(cast(s.memory_revisions.c.content, LargeBinary)).label("content_bytes"),
                    ),
                ).where(
                    s.memory_revisions.c.agent_id == agent_id,
                    s.memory_revisions.c.memory_id == memory_id,
                    s.memory_revisions.c.revision == revision,
                )
            )
            .mappings()
            .first()
        )
        if version is None or version["content_bytes"] != head["content_bytes"]:
            raise self._organization_stale()
        anchor_query = select(s.source_anchors).where(
            s.source_anchors.c.agent_id == agent_id,
            s.source_anchors.c.memory_id == memory_id,
            s.source_anchors.c.memory_revision_id == version["id"],
        )
        anchor_sizes = tcast(
            list[int],
            list(
                self.connection.execute(
                    anchor_query.with_only_columns(func.length(cast(s.source_anchors.c.quote, LargeBinary))).limit(
                        MAX_ANCHORS + 1
                    )
                ).scalars()
            ),
        )
        if len(anchor_sizes) > MAX_ANCHORS:
            raise BusinessError(
                "memory_organization_sources", "Each fact may have at most eight current source anchors", status=422
            )
        used = tcast(int, head["content_bytes"]) + sum(anchor_sizes)
        if used > remaining:
            raise self._organization_budget()
        content = tcast(
            str,
            self.connection.execute(
                select(s.memory_entries.c.content).where(
                    s.memory_entries.c.id == memory_id, s.memory_entries.c.agent_id == agent_id
                )
            ).scalar_one(),
        )
        historic = self.connection.execute(
            select(s.memory_revisions.c.content).where(
                s.memory_revisions.c.id == version["id"], s.memory_revisions.c.agent_id == agent_id
            )
        ).scalar_one()
        if content != historic:
            raise self._organization_stale()
        anchors = [
            tcast(Record, dict(row))
            for row in self.connection.execute(
                anchor_query.order_by(s.source_anchors.c.id).limit(MAX_ANCHORS)
            ).mappings()
        ]
        return {
            "memory_id": memory_id,
            "memory_revision_id": version["id"],
            "revision": revision,
            "raw_sha256": hashlib.sha256(content.encode("utf-8")).hexdigest(),
            "evidence_sha256": _digest(anchors),
            "person_id": head["person_id"],
            "session_id": head["session_id"],
            "content": content,
            "source_count": len(anchors),
        }, used

    def _organization_sources(self, batch: Record) -> list[Record]:
        return [
            tcast(Record, dict(row))
            for row in self.connection.execute(
                select(s.memory_organization_sources)
                .where(
                    s.memory_organization_sources.c.agent_id == batch["agent_id"],
                    s.memory_organization_sources.c.batch_id == batch["id"],
                )
                .order_by(s.memory_organization_sources.c.position)
                .limit(MAX_SOURCES + 1)
            ).mappings()
        ]

    def _organization_manifest(self, batch: Record, sources: list[Record]) -> list[Record]:
        if (
            batch["state"] not in LIVE_BATCHES
            or batch["policy"] != POLICY
            or batch["budget_micro"] != BUDGET_MICRO
            or batch["history_epoch"] != self._organization_epoch()
            or len(sources) != batch["source_count"]
            or not 2 <= len(sources) <= MAX_SOURCES
        ):
            raise self._organization_stale()
        remaining, current = MAX_SOURCE_BYTES, tcast(list[Record], [])
        for position, source in enumerate(sources):
            if source["position"] != position:
                raise self._organization_stale()
            captured, used = self._organization_capture(
                tcast(str, batch["agent_id"]),
                tcast(str, source["memory_id"]),
                tcast(int, source["revision"]),
                remaining,
            )
            if any(captured[key] != source[key] for key in CAPTURE_IDENTITY):
                raise self._organization_stale()
            remaining -= used
            current.append(captured)
        return current

    @staticmethod
    def _organization_fingerprint(batch: Record, sources: list[Record], keep: str, duplicate: str) -> str:
        return _digest(
            {
                "batch_id": batch["id"],
                "agent_id": batch["agent_id"],
                "history_epoch": batch["history_epoch"],
                "policy": batch["policy"],
                "budget_micro": batch["budget_micro"],
                "sources": [{key: source[key] for key in SOURCE_IDENTITY} for source in sources],
                "keep_source_id": keep,
                "duplicate_source_id": duplicate,
            }
        )

    def _organization_change(self, batch: Record, **values: object) -> Record:
        changed = self.connection.execute(
            update(s.memory_organization_batches)
            .where(
                s.memory_organization_batches.c.agent_id == batch["agent_id"],
                s.memory_organization_batches.c.id == batch["id"],
                s.memory_organization_batches.c.revision == batch["revision"],
            )
            .values(revision=tcast(int, batch["revision"]) + 1, updated_at=self.clock.now(), **values)
        ).rowcount
        if changed != 1:
            raise BusinessError("revision_conflict", "Memory organization changed; reload before editing")
        return self._one(s.memory_organization_batches, tcast(str, batch["agent_id"]), tcast(str, batch["id"]))

    def _organization_invalidate(self, batch: Record) -> Record:
        if batch["state"] in LIVE_BATCHES:
            self.connection.execute(
                update(s.memory_organization_candidates)
                .where(
                    s.memory_organization_candidates.c.agent_id == batch["agent_id"],
                    s.memory_organization_candidates.c.batch_id == batch["id"],
                    s.memory_organization_candidates.c.state.in_(["pending", "applied"]),
                )
                .values(
                    state="invalidated",
                    revision=s.memory_organization_candidates.c.revision + 1,
                    updated_at=self.clock.now(),
                    error_code="memory_organization_source_changed",
                )
            )
            batch = self._organization_change(
                batch, state="invalidated", error_code="memory_organization_source_changed"
            )
            self.audit(
                tcast(str, batch["agent_id"]),
                "memory.organization.invalidated",
                tcast(str, batch["id"]),
                {"policy": POLICY},
            )
        return batch

    def memory_organization_get(self, agent_id: str, batch_id: str) -> Record:
        batch = self._one(s.memory_organization_batches, agent_id, batch_id)
        sources = self._organization_sources(batch)
        try:
            current = self._organization_manifest(batch, sources)
            valid = True
        except BusinessError:
            current, valid = tcast(list[Record], []), False
        candidates = [
            tcast(Record, dict(row))
            for row in self.connection.execute(
                select(s.memory_organization_candidates)
                .where(
                    s.memory_organization_candidates.c.agent_id == agent_id,
                    s.memory_organization_candidates.c.batch_id == batch_id,
                )
                .order_by(s.memory_organization_candidates.c.created_at, s.memory_organization_candidates.c.id)
                .limit(MAX_SOURCES)
            ).mappings()
        ]
        return {
            **batch,
            "valid": valid,
            "sources": [
                {**source, "valid": valid, **({"content": current[index]["content"]} if valid else {})}
                for index, source in enumerate(sources)
            ],
            "candidates": [
                {**candidate, "valid": valid and candidate["state"] not in {"invalidated", "cancelled"}}
                for candidate in candidates
            ],
        }

    def memory_organization_enqueue(self, agent_id: str, request: Record) -> Record:
        normalized, sha = parse_request(request), request_fingerprint(request)
        self._organization_agent(agent_id)
        prior = (
            self.connection.execute(
                select(s.memory_organization_batches).where(
                    s.memory_organization_batches.c.agent_id == agent_id,
                    s.memory_organization_batches.c.request_key == normalized["key"],
                )
            )
            .mappings()
            .first()
        )
        if prior:
            if prior["request_sha256"] != sha:
                raise BusinessError("idempotency_conflict", "This key already identifies another organization request")
            return self.memory_organization_get(agent_id, tcast(str, prior["id"]))
        count = self.connection.execute(
            select(func.count()).select_from(s.maintenance_jobs).where(s.maintenance_jobs.c.state.in_(ACTIVE))
        ).scalar_one()
        count += self.connection.execute(
            select(func.count())
            .select_from(s.memory_organization_batches)
            .where(s.memory_organization_batches.c.state.in_(["queued", "ready"]))
        ).scalar_one()
        if count >= MAX_PENDING:
            raise BusinessError("maintenance_busy", "The shared maintenance queue is full", retryable=True, status=503)
        remaining, captured = MAX_SOURCE_BYTES, tcast(list[Record], [])
        for source in tcast(list[Record], normalized["sources"]):
            item, used = self._organization_capture(
                agent_id, tcast(str, source["memory_id"]), tcast(int, source["revision"]), remaining
            )
            remaining -= used
            captured.append(item)
        batch = self._record(
            s.memory_organization_batches,
            {
                "request_key": tcast(str, normalized["key"]),
                "request_sha256": sha,
                "policy": POLICY,
                "history_epoch": self._organization_epoch(),
                "budget_micro": BUDGET_MICRO,
                "source_count": len(captured),
                "candidate_count": 0,
                "state": "queued",
                "revision": 1,
                "updated_at": self.clock.now(),
                "prepared_at": None,
                "error_code": "",
            },
            agent_id,
        )
        for position, item in enumerate(captured):
            self._record(
                s.memory_organization_sources,
                {"batch_id": batch["id"], "position": position, **{key: item[key] for key in CAPTURE_IDENTITY}},
                agent_id,
            )
        self.audit(
            agent_id,
            "memory.organization.queued",
            tcast(str, batch["id"]),
            {"policy": POLICY, "source_count": len(captured), "budget_micro": BUDGET_MICRO},
        )
        return self.memory_organization_get(agent_id, tcast(str, batch["id"]))

    def memory_organization_page(self, agent_id: str, after: int = 0, limit: int = 20) -> list[Record]:
        page(after, limit)
        self._organization_agent(agent_id)
        epoch = self._organization_epoch()
        return [
            {
                **dict(row),
                "valid": row["state"] in LIVE_BATCHES and row["history_epoch"] == epoch and row["policy"] == POLICY,
            }
            for row in self.connection.execute(
                select(s.memory_organization_batches)
                .where(
                    s.memory_organization_batches.c.agent_id == agent_id,
                    s.memory_organization_batches.c.sequence > after,
                )
                .order_by(s.memory_organization_batches.c.sequence)
                .limit(limit)
            ).mappings()
        ]

    def memory_organization_next(self) -> Record | None:
        row = (
            self.connection.execute(
                select(s.memory_organization_batches.c.id, s.memory_organization_batches.c.agent_id)
                .where(s.memory_organization_batches.c.state == "queued")
                .order_by(s.memory_organization_batches.c.sequence)
                .limit(1)
            )
            .mappings()
            .first()
        )
        return tcast(Record, dict(row)) if row else None

    def memory_organization_process(self, agent_id: str, batch_id: str) -> Record:
        batch = self._one(s.memory_organization_batches, agent_id, batch_id)
        if batch["state"] != "queued":
            return self.memory_organization_get(agent_id, batch_id)
        sources = self._organization_sources(batch)
        try:
            current = self._organization_manifest(batch, sources)
        except BusinessError as exc:
            if exc.code not in {"memory_organization_stale", "not_found"}:
                raise
            self._organization_invalidate(batch)
            return self.memory_organization_get(agent_id, batch_id)
        groups: dict[tuple[bytes, str | None, str | None], list[Record]] = {}
        for source, content in zip(sources, current, strict=True):
            key = (
                tcast(str, content["content"]).encode("utf-8"),
                tcast(str | None, content["person_id"]),
                tcast(str | None, content["session_id"]),
            )
            groups.setdefault(key, []).append(source)
        count = 0
        for group in groups.values():
            keeper = group[0]
            for duplicate in group[1:]:
                self._record(
                    s.memory_organization_candidates,
                    {
                        "batch_id": batch_id,
                        "keep_source_id": keeper["id"],
                        "duplicate_source_id": duplicate["id"],
                        "state": "pending",
                        "revision": 1,
                        "fingerprint": self._organization_fingerprint(
                            batch, sources, tcast(str, keeper["id"]), tcast(str, duplicate["id"])
                        ),
                        "updated_at": self.clock.now(),
                        "reviewed_at": None,
                        "review_audit_id": None,
                        "error_code": "",
                    },
                    agent_id,
                )
                count += 1
        self._organization_change(
            batch, state="ready" if count else "complete", candidate_count=count, prepared_at=self.clock.now()
        )
        self.audit(
            agent_id,
            "memory.organization.prepared",
            batch_id,
            {"policy": POLICY, "candidate_count": count, "budget_micro": BUDGET_MICRO},
        )
        return self.memory_organization_get(agent_id, batch_id)

    def memory_organization_fail(self, agent_id: str, batch_id: str, error: str) -> Record:
        batch = self._one(s.memory_organization_batches, agent_id, batch_id)
        if batch["state"] == "queued":
            code = (
                error
                if isinstance(tcast(object, error), str) and re.fullmatch(r"[a-z][a-z0-9_]{0,79}", error)
                else "memory_organization_failed"
            )
            self._organization_change(batch, state="failed", error_code=code)
            self.audit(agent_id, "memory.organization.failed", batch_id, {"error_code": code, "policy": POLICY})
        return self.memory_organization_get(agent_id, batch_id)

    def memory_organization_cancel(self, agent_id: str, batch_id: str, revision: int) -> Record:
        page(revision, 1)
        if revision < 1:
            raise BusinessError("memory_organization_invalid", "A positive revision is required", status=422)
        batch = self._one(s.memory_organization_batches, agent_id, batch_id)
        if batch["state"] == "cancelled":
            return self.memory_organization_get(agent_id, batch_id)
        if batch["revision"] != revision:
            raise BusinessError("revision_conflict", "Organization changed; reload before cancelling")
        if batch["state"] not in {"queued", "ready"}:
            raise BusinessError("memory_organization_state", "Only unfinished proposals can be cancelled")
        self.connection.execute(
            update(s.memory_organization_candidates)
            .where(
                s.memory_organization_candidates.c.agent_id == agent_id,
                s.memory_organization_candidates.c.batch_id == batch_id,
                s.memory_organization_candidates.c.state == "pending",
            )
            .values(
                state="cancelled",
                revision=s.memory_organization_candidates.c.revision + 1,
                updated_at=self.clock.now(),
                error_code="memory_organization_cancelled",
            )
        )
        self._organization_change(batch, state="cancelled", error_code="memory_organization_cancelled")
        self.audit(agent_id, "memory.organization.cancelled", batch_id, {"policy": POLICY})
        return self.memory_organization_get(agent_id, batch_id)

    def memory_organization_decide(self, agent_id: str, candidate_id: str, request: Record) -> Record:
        decision = parse_decision(request)
        candidate = self._one(s.memory_organization_candidates, agent_id, candidate_id)
        batch = self._one(s.memory_organization_batches, agent_id, tcast(str, candidate["batch_id"]))
        if candidate["revision"] != decision["revision"]:
            raise BusinessError("revision_conflict", "Candidate changed; reload before deciding")
        if (
            decision["epoch"] != batch["history_epoch"]
            or decision["epoch"] != self._organization_epoch()
            or candidate["fingerprint"] != decision["fingerprint"]
        ):
            raise self._organization_stale()
        sources = self._organization_sources(batch)
        if (
            self._organization_fingerprint(
                batch,
                sources,
                tcast(str, candidate["keep_source_id"]),
                tcast(str, candidate["duplicate_source_id"]),
            )
            != candidate["fingerprint"]
        ):
            raise self._organization_stale()
        if batch["state"] not in LIVE_BATCHES or candidate["state"] in {"invalidated", "cancelled"}:
            raise self._organization_stale()
        try:
            current = self._organization_manifest(batch, sources)
        except BusinessError:
            self._organization_invalidate(batch)
            return self.memory_organization_get(agent_id, tcast(str, batch["id"]))
        kind = decision["decision"]
        if kind == "undo":
            if candidate["state"] != "applied":
                raise BusinessError("memory_organization_state", "Only an applied relationship can be revoked")
            state = "revoked"
        else:
            if candidate["state"] != "pending" or batch["state"] != "ready":
                raise BusinessError("memory_organization_state", "Only a pending proposal can be reviewed")
            state = "applied" if kind == "apply" else "rejected"
        if state == "applied":
            selected = {tcast(str, source["id"]): source for source in sources}
            keeper = selected[tcast(str, candidate["keep_source_id"])]
            duplicate = selected[tcast(str, candidate["duplicate_source_id"])]
            # Re-prove the classification, not merely a hash supplied by the caller.
            by_id = {tcast(str, source["id"]): item for source, item in zip(sources, current, strict=True)}
            if (
                by_id[tcast(str, keeper["id"])]["content"] != by_id[tcast(str, duplicate["id"])]["content"]
                or keeper["person_id"] != duplicate["person_id"]
                or keeper["session_id"] != duplicate["session_id"]
            ):
                raise self._organization_stale()
            relations = live_organization_relations().subquery("existing_organization")
            if self.connection.execute(
                select(relations.c.candidate_id)
                .where(
                    relations.c.agent_id == agent_id,
                    or_(
                        relations.c.duplicate_memory_id.in_(
                            [tcast(str, keeper["memory_id"]), tcast(str, duplicate["memory_id"])]
                        ),
                        relations.c.keep_memory_id == tcast(str, duplicate["memory_id"]),
                    ),
                )
                .limit(1)
            ).first():
                raise BusinessError(
                    "memory_organization_conflict",
                    "This relationship would form a duplicate chain or suppress a fact twice",
                )
        audit_id = self.audit(
            agent_id,
            "memory.organization." + state,
            candidate_id,
            tcast(
                Record,
                {
                    "batch_id": batch["id"],
                    "policy": POLICY,
                    "fingerprint": candidate["fingerprint"],
                    "decision": kind,
                },
            ),
        )
        changed = self.connection.execute(
            update(s.memory_organization_candidates)
            .where(
                s.memory_organization_candidates.c.agent_id == agent_id,
                s.memory_organization_candidates.c.id == candidate_id,
                s.memory_organization_candidates.c.revision == decision["revision"],
            )
            .values(
                state=state,
                revision=tcast(int, candidate["revision"]) + 1,
                reviewed_at=self.clock.now(),
                review_audit_id=audit_id,
                updated_at=self.clock.now(),
                error_code="",
            )
        ).rowcount
        if changed != 1:
            raise BusinessError("revision_conflict", "Candidate changed while reviewing")
        pending = self.connection.execute(
            select(s.memory_organization_candidates.c.id)
            .where(
                s.memory_organization_candidates.c.agent_id == agent_id,
                s.memory_organization_candidates.c.batch_id == batch["id"],
                s.memory_organization_candidates.c.state == "pending",
            )
            .limit(1)
        ).first()
        self._organization_change(
            batch, state="complete" if batch["state"] == "ready" and not pending else batch["state"]
        )
        return self.memory_organization_get(agent_id, tcast(str, batch["id"]))
