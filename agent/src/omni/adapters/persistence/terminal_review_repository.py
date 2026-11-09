"""Append owner observations and erasable evidence; terminal receipts and native lifecycle remain unchanged."""

from __future__ import annotations

import hashlib

from sqlalchemy import select, update

from ...domain.contracts import BusinessError
from ...domain.terminal_review import MAX_REVIEWS, POLICY, fingerprint, page, parse_review, request_fingerprint
from ...domain.terminals import identity, integer
from . import schema as s
from .terminal_repository import TerminalRepositories


class TerminalReviewRepositories(TerminalRepositories):
    def _terminal_review_source(self, agent_id: str, operation_id: str) -> tuple[dict, dict, str]:
        operation = self._one(s.terminal_operations, agent_id, identity(operation_id))
        session = self.terminal_get(agent_id, operation["session_id"])
        epoch = self.connection.execute(select(s.history_identity.c.epoch)).scalar_one()
        return operation, session, epoch

    @staticmethod
    def _terminal_review_proof(operation: dict, session: dict, epoch: str) -> dict:
        return {
            "schema": 1,
            "policy": POLICY,
            "history_epoch": epoch,
            "agent_id": operation["agent_id"],
            "operation": {
                key: operation[key]
                for key in (
                    "id",
                    "session_id",
                    "boot_id",
                    "kind",
                    "payload_sha256",
                    "bytes",
                    "state",
                    "intent_audit_id",
                    "created_at",
                    "ended_at",
                    "error_code",
                )
            },
            "session": {
                key: session[key]
                for key in (
                    "id",
                    "boot_id",
                    "state",
                    "revision",
                    "workspace_sha256",
                    "permission_epoch",
                    "pid",
                    "process_created",
                    "cleanup_pending",
                    "exit_code",
                    "ended_at",
                )
            },
        }

    def terminal_review_detail(self, agent_id: str, operation_id: str) -> dict:
        operation, session, epoch = self._terminal_review_source(agent_id, operation_id)
        latest = (
            self.connection.execute(
                select(s.terminal_reviews)
                .where(s.terminal_reviews.c.agent_id == agent_id, s.terminal_reviews.c.operation_id == operation_id)
                .order_by(s.terminal_reviews.c.revision.desc())
                .limit(1)
            )
            .mappings()
            .first()
        )
        reasons = []
        if operation["state"] != "unknown":
            reasons.append("terminal_receipt_not_unknown")
        if operation["ended_at"] is None:
            reasons.append("terminal_operation_not_ended")
        if latest and latest["revision"] >= MAX_REVIEWS:
            reasons.append("terminal_review_capacity")
        proof = self._terminal_review_proof(operation, session, epoch)
        sha = fingerprint(proof)
        current = bool(latest and latest["history_epoch"] == epoch and latest["fingerprint"] == sha)
        return {
            "operation": {**operation, "operation_id": operation["id"]},
            "session": {
                key: session[key]
                for key in ("id", "shell", "state", "revision", "cleanup_pending", "boot_id", "exit_code")
            },
            "policy": POLICY,
            "history_epoch": epoch,
            "fingerprint": sha,
            "revision": latest["revision"] if latest else 0,
            "reviewable": not reasons,
            "reasons": reasons,
            "latest_review": dict(latest) if latest else None,
            "latest_matches_source": current,
            "receipt_unchanged": True,
            "authorizes_replay": False,
            "process_reclamation_verified": session["state"] == "exited" and not session["cleanup_pending"],
        }

    def terminal_review_page(self, agent_id: str, after: str = "", limit: int = 20) -> list[dict]:
        page(after, limit)
        self._terminal_agent(agent_id)
        operations = self.connection.execute(
            select(s.terminal_operations.c.id)
            .where(
                s.terminal_operations.c.agent_id == agent_id,
                s.terminal_operations.c.state == "unknown",
                s.terminal_operations.c.id > after,
            )
            .order_by(s.terminal_operations.c.id)
            .limit(limit)
        ).scalars()
        return [self.terminal_review_detail(agent_id, operation_id) for operation_id in operations]

    def terminal_review_replay(self, agent_id: str, operation_id: str, data: dict) -> dict | None:
        self._terminal_review_source(agent_id, operation_id)
        request = parse_review(data)
        prior = (
            self.connection.execute(select(s.terminal_reviews).where(s.terminal_reviews.c.id == request["review_id"]))
            .mappings()
            .first()
        )
        if prior is None:
            return None
        if prior["agent_id"] != agent_id:
            raise BusinessError("not_found", "Review does not exist in this Agent space", status=404)
        if prior["operation_id"] != operation_id or prior["request_sha256"] != request_fingerprint(request):
            raise BusinessError("terminal_review_conflict", "Review identity already binds different evidence")
        return {**dict(prior), "replayed": True}

    def terminal_review_commit(self, agent_id: str, operation_id: str, data: dict) -> dict:
        request = parse_review(data)
        replay = self.terminal_review_replay(agent_id, operation_id, request)
        if replay is not None:
            return replay
        inspected = self.terminal_review_detail(agent_id, operation_id)
        if not inspected["reviewable"]:
            raise BusinessError(
                "terminal_review_unavailable", "Only ended unknown terminal observations may be reviewed"
            )
        if any(request[key] != inspected[key] for key in ("revision", "history_epoch", "fingerprint", "policy")):
            raise BusinessError(
                "terminal_review_changed", "Reload this terminal observation before reviewing", status=409
            )
        operation, session, epoch = self._terminal_review_source(agent_id, operation_id)
        evidence_sha = hashlib.sha256(request["evidence"].encode("utf-8")).hexdigest()
        audit_id = self.audit(
            agent_id,
            "terminal.review.recorded",
            operation_id,
            {
                "review_id": request["review_id"],
                "session_id": session["id"],
                "decision": request["decision"],
                "policy": POLICY,
                "fingerprint": inspected["fingerprint"],
                "evidence_sha256": evidence_sha,
                "history_epoch": epoch,
                "receipt_unchanged": True,
                "authorizes_replay": False,
            },
        )
        row = self._record(
            s.terminal_reviews,
            {
                "id": request["review_id"],
                "session_id": session["id"],
                "operation_id": operation_id,
                "revision": inspected["revision"] + 1,
                "history_epoch": epoch,
                "policy": POLICY,
                "fingerprint": inspected["fingerprint"],
                "request_sha256": request_fingerprint(request),
                "proof": self._terminal_review_proof(operation, session, epoch),
                "decision": request["decision"],
                "evidence_sha256": evidence_sha,
                "evidence": request["evidence"],
                "evidence_erased_at": None,
                "audit_id": audit_id,
            },
            agent_id,
        )
        return {**row, "replayed": False}

    def terminal_review_history(self, agent_id: str, operation_id: str, after: int = 0, limit: int = 20) -> list[dict]:
        self._terminal_review_source(agent_id, operation_id)
        integer(after, 0, MAX_REVIEWS)
        integer(limit, 1, 50)
        return [
            dict(row)
            for row in self.connection.execute(
                select(s.terminal_reviews)
                .where(
                    s.terminal_reviews.c.agent_id == agent_id,
                    s.terminal_reviews.c.operation_id == operation_id,
                    s.terminal_reviews.c.revision > after,
                )
                .order_by(s.terminal_reviews.c.revision)
                .limit(limit)
            ).mappings()
        ]

    def terminal_review_erase_evidence(self, agent_id: str, operation_id: str, review_id: str) -> dict:
        self._terminal_review_source(agent_id, operation_id)
        row = self._one(s.terminal_reviews, agent_id, identity(review_id))
        if row["operation_id"] != operation_id:
            raise BusinessError("not_found", "Review does not belong to this operation", status=404)
        if row["evidence"] is not None:
            self.connection.execute(
                update(s.terminal_reviews)
                .where(s.terminal_reviews.c.id == review_id)
                .values(evidence=None, evidence_erased_at=self.clock.now())
            )
            self.audit(
                agent_id,
                "terminal.review.evidence_erased",
                operation_id,
                {"review_id": review_id, "evidence_sha256": row["evidence_sha256"]},
            )
        return self._one(s.terminal_reviews, agent_id, review_id)
