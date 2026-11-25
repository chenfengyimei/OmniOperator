"""Bounded post-turn review proposals and owner review; applying still uses the memory/skill chains."""

from __future__ import annotations

import json
import re
from typing import cast as tcast

from sqlalchemy import func, select, update

from ...application.ports import Record
from ...domain.background_review import (
    MAX_CALLS,
    MAX_PROPOSALS,
    POLICY,
    page,
    parse_decision,
    parse_request,
    proposal_fingerprint,
    request_fingerprint,
)
from ...domain.contracts import BusinessError
from ...domain.maintenance import ACTIVE, MAX_PENDING
from . import schema as s
from .memory_repository import MemoryRepositories

LIVE_BATCHES = frozenset({"queued", "running", "ready", "complete"})


def _proposal_json(proposal: dict[str, object]) -> str:
    return json.dumps(proposal, ensure_ascii=False, sort_keys=True, separators=(",", ":"), allow_nan=False)


class BackgroundReviewRepositories(MemoryRepositories):
    def background_review_sources(self, agent_id: str, run_id: str) -> list[Record]:
        run = self._one(s.runs, agent_id, run_id)
        rows = self.connection.execute(
            select(s.messages.c.id, s.messages.c.role, s.messages.c.content)
            .where(
                s.messages.c.agent_id == agent_id,
                s.messages.c.session_id == run["session_id"],
                s.messages.c.deleted_at.is_(None),
                (s.messages.c.id == run["input_message_id"])
                | ((s.messages.c.run_id == run_id) & (s.messages.c.role == "assistant")),
            )
            .order_by(s.messages.c.seq)
            .limit(2)
        ).mappings()
        return [dict(row) for row in rows]

    def _review_agent(self, agent_id: str) -> None:
        if not self.connection.execute(select(s.agents.c.id).where(s.agents.c.id == agent_id)).first():
            raise BusinessError("not_found", "Agent does not exist", status=404)

    def _review_epoch(self) -> str:
        return tcast(str, self.connection.execute(select(s.history_identity.c.epoch)).scalar_one())

    @staticmethod
    def _review_stale() -> BusinessError:
        return BusinessError("background_review_stale", "Captured turn changed; review a new proposal")

    def _review_change(self, batch: Record, **values: object) -> Record:
        changed = self.connection.execute(
            update(s.background_review_batches)
            .where(
                s.background_review_batches.c.agent_id == batch["agent_id"],
                s.background_review_batches.c.id == batch["id"],
                s.background_review_batches.c.revision == batch["revision"],
            )
            .values(revision=tcast(int, batch["revision"]) + 1, updated_at=self.clock.now(), **values)
        ).rowcount
        if changed != 1:
            raise BusinessError("revision_conflict", "Background review changed; reload before editing")
        return self._one(s.background_review_batches, tcast(str, batch["agent_id"]), tcast(str, batch["id"]))

    def _review_candidates(self, agent_id: str, batch_id: str) -> list[Record]:
        return [
            tcast(Record, dict(row))
            for row in self.connection.execute(
                select(s.background_review_candidates)
                .where(
                    s.background_review_candidates.c.agent_id == agent_id,
                    s.background_review_candidates.c.batch_id == batch_id,
                )
                .order_by(s.background_review_candidates.c.position)
                .limit(MAX_PROPOSALS)
            ).mappings()
        ]

    def background_review_get(self, agent_id: str, batch_id: str) -> Record:
        batch = self._one(s.background_review_batches, agent_id, batch_id)
        valid = batch["state"] in LIVE_BATCHES and batch["history_epoch"] == self._review_epoch()
        candidates = self._review_candidates(agent_id, batch_id)
        return {
            **batch,
            "valid": valid,
            "candidates": [
                {
                    **candidate,
                    "proposal": json.loads(tcast(str, candidate["proposal"])),
                    "valid": valid and candidate["state"] not in {"invalidated", "cancelled"},
                }
                for candidate in candidates
            ],
        }

    def background_review_enqueue(self, agent_id: str, request: Record) -> Record:
        normalized, sha = parse_request(request), request_fingerprint(request)
        self._review_agent(agent_id)
        run_id = tcast(str, normalized["run_id"])
        run = self._one(s.runs, agent_id, run_id)
        if run["state"] != "succeeded":
            raise BusinessError("background_review_state", "Only a finished turn can be reviewed", status=422)
        prior = (
            self.connection.execute(
                select(s.background_review_batches).where(
                    s.background_review_batches.c.agent_id == agent_id,
                    s.background_review_batches.c.request_key == normalized["key"],
                )
            )
            .mappings()
            .first()
        )
        if prior:
            if prior["request_sha256"] != sha:
                raise BusinessError("idempotency_conflict", "This key already identifies another review request")
            return self.background_review_get(agent_id, tcast(str, prior["id"]))
        count = self.connection.execute(
            select(func.count()).select_from(s.maintenance_jobs).where(s.maintenance_jobs.c.state.in_(ACTIVE))
        ).scalar_one()
        count += self.connection.execute(
            select(func.count())
            .select_from(s.background_review_batches)
            .where(s.background_review_batches.c.state.in_(["queued", "running", "ready"]))
        ).scalar_one()
        count += self.connection.execute(
            select(func.count())
            .select_from(s.memory_organization_batches)
            .where(s.memory_organization_batches.c.state.in_(["queued", "ready"]))
        ).scalar_one()
        if count >= MAX_PENDING:
            raise BusinessError("maintenance_busy", "The shared maintenance queue is full", retryable=True, status=503)
        batch = self._record(
            s.background_review_batches,
            {
                "run_id": run_id,
                "request_key": tcast(str, normalized["key"]),
                "request_sha256": sha,
                "policy": POLICY,
                "history_epoch": self._review_epoch(),
                "budget_micro": 0,
                "call_count": 0,
                "candidate_count": 0,
                "state": "queued",
                "revision": 1,
                "updated_at": self.clock.now(),
                "prepared_at": None,
                "error_code": "",
            },
            agent_id,
        )
        self.audit(agent_id, "background.review.queued", tcast(str, batch["id"]), {"policy": POLICY, "run_id": run_id})
        return self.background_review_get(agent_id, tcast(str, batch["id"]))

    def background_review_page(self, agent_id: str, after: int = 0, limit: int = 20) -> list[Record]:
        page(after, limit)
        self._review_agent(agent_id)
        epoch = self._review_epoch()
        return [
            {
                **dict(row),
                "valid": row["state"] in LIVE_BATCHES and row["history_epoch"] == epoch and row["policy"] == POLICY,
            }
            for row in self.connection.execute(
                select(s.background_review_batches)
                .where(
                    s.background_review_batches.c.agent_id == agent_id,
                    s.background_review_batches.c.sequence > after,
                )
                .order_by(s.background_review_batches.c.sequence)
                .limit(limit)
            ).mappings()
        ]

    def background_review_next(self) -> Record | None:
        row = (
            self.connection.execute(
                select(
                    s.background_review_batches.c.id,
                    s.background_review_batches.c.agent_id,
                    s.background_review_batches.c.run_id,
                )
                .where(s.background_review_batches.c.state == "queued")
                .order_by(s.background_review_batches.c.sequence)
                .limit(1)
            )
            .mappings()
            .first()
        )
        return tcast(Record, dict(row)) if row else None

    def background_review_claim(self, agent_id: str, batch_id: str) -> Record | None:
        batch = self._one(s.background_review_batches, agent_id, batch_id)
        if batch["state"] != "queued":
            return None
        if batch["history_epoch"] != self._review_epoch():
            return self._review_invalidate(batch)
        return self._review_change(batch, state="running")

    def background_review_record_call(self, agent_id: str, batch_id: str, cost_micro: int) -> Record:
        batch = self._one(s.background_review_batches, agent_id, batch_id)
        if batch["state"] != "running":
            raise BusinessError("background_review_state", "Only a running review can record a call")
        calls = tcast(int, batch["call_count"]) + 1
        if calls > MAX_CALLS:
            raise BusinessError("background_review_budget", "The review reached its independent call budget")
        return self._review_change(
            batch, call_count=calls, budget_micro=tcast(int, batch["budget_micro"]) + max(0, cost_micro)
        )

    def background_review_stage(self, agent_id: str, batch_id: str, proposals: list[dict[str, object]]) -> Record:
        batch = self._one(s.background_review_batches, agent_id, batch_id)
        if batch["state"] != "running":
            return self.background_review_get(agent_id, batch_id)
        if batch["history_epoch"] != self._review_epoch():
            return self._review_invalidate(batch)
        count = 0
        for proposal in proposals[:MAX_PROPOSALS]:
            self._record(
                s.background_review_candidates,
                {
                    "batch_id": batch_id,
                    "position": count,
                    "kind": tcast(str, proposal["kind"]),
                    "tool": tcast(str, proposal["tool"]),
                    "proposal": _proposal_json(proposal),
                    "state": "pending",
                    "revision": 1,
                    "fingerprint": proposal_fingerprint(
                        batch_id, agent_id, tcast(str, batch["history_epoch"]), proposal
                    ),
                    "updated_at": self.clock.now(),
                    "reviewed_at": None,
                    "review_audit_id": None,
                    "applied_memory_id": None,
                    "applied_memory_revision": None,
                    "applied_skill_id": None,
                    "applied_skill_revision_id": None,
                    "error_code": "",
                },
                agent_id,
            )
            count += 1
        self._review_change(
            batch, state="ready" if count else "complete", candidate_count=count, prepared_at=self.clock.now()
        )
        self.audit(agent_id, "background.review.prepared", batch_id, {"policy": POLICY, "candidate_count": count})
        return self.background_review_get(agent_id, batch_id)

    def _review_invalidate(self, batch: Record) -> Record:
        if batch["state"] in LIVE_BATCHES:
            self.connection.execute(
                update(s.background_review_candidates)
                .where(
                    s.background_review_candidates.c.agent_id == batch["agent_id"],
                    s.background_review_candidates.c.batch_id == batch["id"],
                    s.background_review_candidates.c.state.in_(["pending", "applied"]),
                )
                .values(
                    state="invalidated",
                    revision=s.background_review_candidates.c.revision + 1,
                    updated_at=self.clock.now(),
                    error_code="background_review_source_changed",
                )
            )
            batch = self._review_change(batch, state="invalidated", error_code="background_review_source_changed")
            self.audit(
                tcast(str, batch["agent_id"]),
                "background.review.invalidated",
                tcast(str, batch["id"]),
                {"policy": POLICY},
            )
        return batch

    def background_review_fail(self, agent_id: str, batch_id: str, error: str) -> Record:
        batch = self._one(s.background_review_batches, agent_id, batch_id)
        if batch["state"] in {"queued", "running"}:
            code = error if re.fullmatch(r"[a-z][a-z0-9_]{0,79}", error) else "background_review_failed"
            self._review_change(batch, state="failed", error_code=code)
            self.audit(agent_id, "background.review.failed", batch_id, {"error_code": code, "policy": POLICY})
        return self.background_review_get(agent_id, batch_id)

    def background_review_cancel(self, agent_id: str, batch_id: str, revision: int) -> Record:
        page(revision, 1)
        if revision < 1:
            raise BusinessError("background_review_invalid", "A positive revision is required", status=422)
        batch = self._one(s.background_review_batches, agent_id, batch_id)
        if batch["state"] == "cancelled":
            return self.background_review_get(agent_id, batch_id)
        if batch["revision"] != revision:
            raise BusinessError("revision_conflict", "Review changed; reload before cancelling")
        if batch["state"] not in {"queued", "running", "ready"}:
            raise BusinessError("background_review_state", "Only an unfinished review can be cancelled")
        self.connection.execute(
            update(s.background_review_candidates)
            .where(
                s.background_review_candidates.c.agent_id == agent_id,
                s.background_review_candidates.c.batch_id == batch_id,
                s.background_review_candidates.c.state == "pending",
            )
            .values(
                state="cancelled",
                revision=s.background_review_candidates.c.revision + 1,
                updated_at=self.clock.now(),
                error_code="background_review_cancelled",
            )
        )
        self._review_change(batch, state="cancelled", error_code="background_review_cancelled")
        self.audit(agent_id, "background.review.cancelled", batch_id, {"policy": POLICY})
        return self.background_review_get(agent_id, batch_id)

    def _review_decision(self, agent_id: str, candidate_id: str, request: Record) -> tuple[Record, Record, str]:
        decision = parse_decision(request)
        candidate = self._one(s.background_review_candidates, agent_id, candidate_id)
        batch = self._one(s.background_review_batches, agent_id, tcast(str, candidate["batch_id"]))
        if candidate["revision"] != decision["revision"]:
            raise BusinessError("revision_conflict", "Candidate changed; reload before deciding")
        if (
            decision["epoch"] != batch["history_epoch"]
            or decision["epoch"] != self._review_epoch()
            or candidate["fingerprint"] != decision["fingerprint"]
        ):
            raise self._review_stale()
        if batch["state"] not in LIVE_BATCHES or candidate["state"] in {"invalidated", "cancelled"}:
            raise self._review_stale()
        kind = tcast(str, decision["decision"])
        if kind == "undo":
            if candidate["state"] != "applied":
                raise BusinessError("background_review_state", "Only an applied proposal can be revoked")
        elif candidate["state"] != "pending" or batch["state"] != "ready":
            raise BusinessError("background_review_state", "Only a pending proposal can be reviewed")
        return candidate, batch, kind

    def _review_settle(self, candidate: Record, batch: Record, state: str, decision: str, **applied: object) -> Record:
        agent_id, candidate_id = tcast(str, candidate["agent_id"]), tcast(str, candidate["id"])
        audit_id = self.audit(
            agent_id,
            "background.review." + state,
            candidate_id,
            {"batch_id": batch["id"], "policy": POLICY, "fingerprint": candidate["fingerprint"], "decision": decision},
        )
        changed = self.connection.execute(
            update(s.background_review_candidates)
            .where(
                s.background_review_candidates.c.agent_id == agent_id,
                s.background_review_candidates.c.id == candidate_id,
                s.background_review_candidates.c.revision == candidate["revision"],
            )
            .values(
                state=state,
                revision=tcast(int, candidate["revision"]) + 1,
                reviewed_at=self.clock.now(),
                review_audit_id=audit_id,
                updated_at=self.clock.now(),
                error_code="",
                **applied,
            )
        ).rowcount
        if changed != 1:
            raise BusinessError("revision_conflict", "Candidate changed while reviewing")
        pending = self.connection.execute(
            select(s.background_review_candidates.c.id)
            .where(
                s.background_review_candidates.c.agent_id == agent_id,
                s.background_review_candidates.c.batch_id == batch["id"],
                s.background_review_candidates.c.state == "pending",
            )
            .limit(1)
        ).first()
        self._review_change(batch, state="complete" if batch["state"] == "ready" and not pending else batch["state"])
        return self.background_review_get(agent_id, tcast(str, batch["id"]))

    def background_review_candidate(self, agent_id: str, candidate_id: str, request: Record) -> Record:
        candidate, batch, kind = self._review_decision(agent_id, candidate_id, request)
        return {
            "candidate_id": candidate_id,
            "batch_id": batch["id"],
            "run_id": batch["run_id"],
            "kind": candidate["kind"],
            "decision": kind,
            "proposal": json.loads(tcast(str, candidate["proposal"])),
            "applied_skill_id": candidate["applied_skill_id"],
            "applied_skill_revision_id": candidate["applied_skill_revision_id"],
        }

    def background_review_decide_memory(self, agent_id: str, candidate_id: str, request: Record) -> Record:
        candidate, batch, kind = self._review_decision(agent_id, candidate_id, request)
        proposal = json.loads(tcast(str, candidate["proposal"]))
        if tcast(str, candidate["kind"]) != "memory":
            raise BusinessError("background_review_state", "This proposal is not a memory edit")
        if kind == "reject":
            return self._review_settle(candidate, batch, "rejected", kind)
        if kind == "apply":
            if proposal["tool"] == "memory_remember":
                run = self._one(s.runs, agent_id, tcast(str, batch["run_id"]))
                message_id = tcast(str | None, proposal.get("message_id")) or tcast(str, run["input_message_id"])
                row = self.remember(
                    agent_id,
                    tcast(str, proposal["content"]),
                    message_id=message_id,
                    quote=tcast(str, proposal["quote"]),
                    model_authored=True,
                    source_run_id=tcast(str, batch["run_id"]),
                )
                return self._review_settle(
                    candidate,
                    batch,
                    "applied",
                    kind,
                    applied_memory_id=row["id"],
                    applied_memory_revision=row["revision"],
                )
            self.forget(agent_id, tcast(str, proposal["memory_id"]), tcast(int, proposal["revision"]))
            return self._review_settle(candidate, batch, "applied", kind)
        # undo: revoke a previously applied memory write through the existing forget chain.
        if proposal["tool"] == "memory_remember":
            self.forget(
                agent_id, tcast(str, candidate["applied_memory_id"]), tcast(int, candidate["applied_memory_revision"])
            )
        else:
            raise BusinessError("background_review_state", "A forgotten fact cannot be restored by review")
        return self._review_settle(candidate, batch, "revoked", kind)

    def background_review_decide_skill(
        self,
        agent_id: str,
        candidate_id: str,
        request: Record,
        *,
        applied_skill_id: str | None = None,
        applied_skill_revision_id: str | None = None,
    ) -> Record:
        candidate, batch, kind = self._review_decision(agent_id, candidate_id, request)
        if tcast(str, candidate["kind"]) != "skill":
            raise BusinessError("background_review_state", "This proposal is not a skill edit")
        if kind == "reject":
            return self._review_settle(candidate, batch, "rejected", kind)
        if kind == "apply":
            return self._review_settle(
                candidate,
                batch,
                "applied",
                kind,
                applied_skill_id=applied_skill_id,
                applied_skill_revision_id=applied_skill_revision_id,
            )
        return self._review_settle(candidate, batch, "revoked", kind)
