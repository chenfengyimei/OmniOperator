"""Owner observation ledger for uncertain actions; original actions, costs and delivery state remain unchanged."""

from __future__ import annotations

import re
from typing import cast as tcast

from sqlalchemy import Column, LargeBinary, cast, func, literal_column, or_, select, update

from ...application.ports import Record
from ...domain.action_review import (
    POLICY,
    TERMINAL_RUNS,
    evidence_sha256,
    page,
    parse_review,
    proof_fingerprint,
    request_fingerprint,
)
from ...domain.contracts import BusinessError
from ...domain.retention import body_fingerprint
from . import schema as s
from .repository_base import RepositoryBase

MAX_OBSERVATION_BYTES = 8 * 1024 * 1024
ACTION_FIELDS = (
    "id",
    "agent_id",
    "run_id",
    "tool",
    "writes",
    "state",
    "effect",
    "policy_revision",
    "arguments_sha256",
    "ended_at",
    "body_pruned_at",
    "result_sha256",
)


class ActionReviewRepositories(RepositoryBase):
    def _action_review_agent(self, agent_id: str) -> None:
        if not self.connection.execute(select(s.agents.c.id).where(s.agents.c.id == agent_id)).first():
            raise BusinessError("not_found", "Agent space does not exist", status=404)

    def _action_review_action(self, agent_id: str, action_id: str) -> Record:
        row = (
            self.connection.execute(
                select(
                    tcast(Column[int], literal_column("actions.rowid").label("sequence")),
                    *(tcast(Column[str], s.actions.c[key]) for key in ACTION_FIELDS),
                    tcast(Column[int], func.length(cast(s.actions.c.result, LargeBinary)).label("result_bytes")),
                ).where(s.actions.c.agent_id == agent_id, s.actions.c.id == action_id)
            )
            .mappings()
            .first()
        )
        if row is None:
            raise BusinessError("not_found", "Action does not exist in this Agent space", status=404)
        return tcast(Record, dict(row))

    def _action_review_source(self, action: Record) -> Record:
        binding = self.connection.execute(
            select(s.action_extension_bindings.c.run_tool_id).where(
                s.action_extension_bindings.c.agent_id == action["agent_id"],
                s.action_extension_bindings.c.action_id == action["id"],
            )
        ).scalar_one_or_none()
        if binding is None:
            if tcast(str, action["tool"]).startswith("ext__"):
                raise BusinessError("action_review_source_changed", "The action has no verifiable plugin identity")
            return {"kind": "builtin_unversioned"}
        agent = tcast(str, action["agent_id"])
        captured = self._one(s.run_extension_tools, agent, binding)
        version = self._one(s.extension_revisions, agent, tcast(str, captured["extension_revision_id"]))
        tool = self._one(s.extension_tools, agent, tcast(str, captured["tool_id"]))
        grant = self._one(s.grants, agent, tcast(str, captured["grant_id"]))
        if (
            captured["run_id"] != action["run_id"]
            or captured["name"] != action["tool"]
            or version["extension_id"] != captured["extension_id"]
            or tool["extension_revision_id"] != version["id"]
            or tool["extension_id"] != captured["extension_id"]
            or tool["name"] != action["tool"]
            or grant["tool_id"] != tool["id"]
            or grant["extension_revision_id"] != version["id"]
            or grant["extension_id"] != captured["extension_id"]
            or grant["authority_epoch"] != captured["authority_epoch"]
            or grant["history_epoch"] != captured["history_epoch"]
        ):
            raise BusinessError("action_review_source_changed", "The captured plugin identity is inconsistent")
        # Historical observations remain reviewable after ordinary plugin revocation or replacement.
        return tcast(
            Record,
            {
                "kind": "plugin",
                "binding_id": binding,
                "extension_id": captured["extension_id"],
                "extension_revision_id": version["id"],
                "tool_id": tool["id"],
                "grant_id": grant["id"],
                "authority_epoch": captured["authority_epoch"],
                "history_epoch": captured["history_epoch"],
                "package_sha256": version["package_sha256"],
                "catalog_sha256": version["catalog_sha256"],
                "spec_sha256": tool["spec_sha256"],
            },
        )

    def _action_review_proof(self, action: Record) -> tuple[Record | None, list[str], Record, str]:
        run = self._one(s.runs, tcast(str, action["agent_id"]), tcast(str, action["run_id"]))
        session = self._one(s.sessions, tcast(str, action["agent_id"]), tcast(str, run["session_id"]))
        epoch = tcast(str, self.connection.execute(select(s.history_identity.c.epoch)).scalar_one())
        reasons: list[str] = []
        if not action["writes"]:
            reasons.append("action_not_write")
        if action["state"] != "unknown" and action["effect"] != "unknown":
            reasons.append("action_not_unknown")
        if action["state"] in {"pending", "approved", "executing"}:
            reasons.append("action_still_active")
        if run["state"] not in TERMINAL_RUNS:
            reasons.append("run_not_terminal")
        if not action["ended_at"]:
            reasons.append("action_not_ended")
        if reasons:
            return None, reasons, run, epoch
        try:
            if action["body_pruned_at"]:
                digest = action["result_sha256"]
                if not isinstance(digest, str) or re.fullmatch(r"[0-9a-f]{64}", digest) is None:
                    raise BusinessError("action_review_source_changed", "The pruned observation fingerprint is missing")
            else:
                if (tcast(int, action["result_bytes"]) or 0) > MAX_OBSERVATION_BYTES:
                    raise BusinessError(
                        "action_review_observation_too_large", "Inspect this oversized observation offline"
                    )
                result = self.connection.execute(
                    select(s.actions.c.result).where(
                        s.actions.c.agent_id == action["agent_id"], s.actions.c.id == action["id"]
                    )
                ).scalar_one()
                digest = body_fingerprint([{"result": result}])[0]
            proof: Record = {
                "policy": POLICY,
                "agent_id": action["agent_id"],
                "action_id": action["id"],
                "run_id": run["id"],
                "run_state": run["state"],
                "session_id": session["id"],
                "session_epoch": session["content_epoch"],
                "tool": action["tool"],
                "writes": bool(action["writes"]),
                "state": action["state"],
                "effect": action["effect"],
                "policy_revision": action["policy_revision"],
                "arguments_sha256": action["arguments_sha256"],
                "result_sha256": digest,
                "ended_at": action["ended_at"],
                "history_epoch": epoch,
                "source": self._action_review_source(action),
            }
            proof_fingerprint(proof)
            return proof, [], run, epoch
        except BusinessError as exc:
            return None, [exc.code], run, epoch

    def _action_review_head(self, action: Record, proof: Record | None) -> Record | None:
        row = (
            self.connection.execute(
                select(s.action_review_heads).where(
                    s.action_review_heads.c.agent_id == action["agent_id"],
                    s.action_review_heads.c.action_id == action["id"],
                )
            )
            .mappings()
            .first()
        )
        head = tcast(Record, dict(row)) if row else None
        if proof is None:
            return head
        fingerprint = proof_fingerprint(proof)
        values: Record = {
            "history_epoch": proof["history_epoch"],
            "fingerprint": fingerprint,
            "arguments_sha256": proof["arguments_sha256"],
            "result_sha256": proof["result_sha256"],
            "session_epoch": proof["session_epoch"],
            "state": "pending",
            "resolution": None,
            "last_review_id": None,
            "updated_at": self.clock.now(),
            "error_code": "",
        }
        if head is None:
            head = self._record(
                s.action_review_heads,
                {"action_id": action["id"], "run_id": action["run_id"], "revision": 0, "policy": POLICY, **values},
                tcast(str, action["agent_id"]),
            )
        elif (
            head["fingerprint"] != fingerprint
            or head["history_epoch"] != proof["history_epoch"]
            or head["session_epoch"] != proof["session_epoch"]
            or head["state"] == "invalidated"
        ):
            changed = self.connection.execute(
                update(s.action_review_heads)
                .where(
                    s.action_review_heads.c.agent_id == action["agent_id"],
                    s.action_review_heads.c.id == head["id"],
                    s.action_review_heads.c.revision == head["revision"],
                )
                .values(revision=tcast(int, head["revision"]) + 1, **values)
            ).rowcount
            if changed != 1:
                raise BusinessError("action_review_conflict", "Action observation changed while inspecting")
        return self._one(s.action_review_heads, tcast(str, action["agent_id"]), tcast(str, head["id"]))

    def _action_review_public(self, review: Record) -> Record:
        evidence = (
            self.connection.execute(
                select(s.action_review_evidence).where(
                    s.action_review_evidence.c.agent_id == review["agent_id"],
                    s.action_review_evidence.c.review_id == review["id"],
                )
            )
            .mappings()
            .first()
        )
        available = bool(evidence and evidence["erased_at"] is None and evidence["body"])
        return tcast(
            Record,
            {
                **{key: value for key, value in review.items() if key != "request_sha256"},
                "revision": tcast(int, review["head_revision"]) + 1,
                "sequence": tcast(int, review["head_revision"]) + 1,
                "evidence_available": available,
                **({"evidence": evidence["body"]} if available and evidence is not None else {}),
            },
        )

    def action_review_history(self, agent_id: str, action_id: str, after: int = 0, limit: int = 20) -> list[Record]:
        page(after, limit)
        self._action_review_action(agent_id, action_id)
        sequence = s.action_reviews.c.head_revision + 1
        return [
            self._action_review_public(tcast(Record, dict(row)))
            for row in self.connection.execute(
                select(s.action_reviews, sequence.label("sequence"))
                .where(
                    s.action_reviews.c.agent_id == agent_id, s.action_reviews.c.action_id == action_id, sequence > after
                )
                .order_by(sequence)
                .limit(limit)
            ).mappings()
        ]

    def _action_review_detail(self, agent_id: str, action_id: str, *, history: bool) -> Record:
        action = self._action_review_action(agent_id, action_id)
        proof, reasons, run, epoch = self._action_review_proof(action)
        head = self._action_review_head(action, proof)
        fingerprint = proof_fingerprint(proof) if proof else ""
        valid = bool(
            head
            and proof
            and head["state"] == "reviewed"
            and head["fingerprint"] == fingerprint
            and head["history_epoch"] == epoch
            and head["last_review_id"]
        )
        resolution = head["resolution"] if valid and head is not None else None
        result: dict[str, object] = {
            "sequence": action["sequence"],
            "agent_id": agent_id,
            "action_id": action_id,
            "run_id": run["id"],
            "tool": action["tool"],
            "writes": bool(action["writes"]),
            "state": action["state"],
            "effect": action["effect"],
            "run_state": run["state"],
            "ended_at": action["ended_at"],
            "revision": head["revision"] if head else 0,
            "policy": POLICY,
            "history_epoch": epoch,
            "fingerprint": fingerprint,
            "eligible": proof is not None,
            "protected_reasons": reasons,
            "proof": proof,
            "review_valid": valid,
            "resolution": resolution,
            "resolved": valid and resolution in {"applied", "not_applied"},
            "requires_review": (action["state"] == "unknown" or action["effect"] == "unknown")
            and not (valid and resolution in {"applied", "not_applied"}),
        }
        if history:
            rows = self.action_review_history(agent_id, action_id)
            latest = (
                self._one(s.action_reviews, agent_id, tcast(str, head["last_review_id"]))
                if head and head["last_review_id"]
                else None
            )
            result.update(
                proof=proof,
                head=head,
                last_review=self._action_review_public(latest) if latest else None,
                history=rows,
                history_next_after=rows[-1]["revision"] if len(rows) == 20 else None,
            )
        return tcast(Record, result)

    def action_review_get(self, agent_id: str, action_id: str) -> Record:
        return self._action_review_detail(agent_id, action_id, history=True)

    def action_review_page(self, agent_id: str, after: int = 0, limit: int = 20) -> list[Record]:
        page(after, limit)
        self._action_review_agent(agent_id)
        sequence = tcast(Column[int], literal_column("actions.rowid"))
        identities = tcast(
            list[str],
            list(
                self.connection.execute(
                    select(s.actions.c.id)
                    .where(
                        s.actions.c.agent_id == agent_id,
                        s.actions.c.writes.is_(True),
                        or_(s.actions.c.state == "unknown", s.actions.c.effect == "unknown"),
                        sequence > after,
                    )
                    .order_by(sequence)
                    .limit(limit)
                ).scalars()
            ),
        )
        return [self._action_review_detail(agent_id, action_id, history=False) for action_id in identities]

    def action_review_replay(self, agent_id: str, action_id: str, request: Record) -> Record | None:
        decision = parse_review(request)
        sha = request_fingerprint(decision)
        previous = (
            self.connection.execute(select(s.action_reviews).where(s.action_reviews.c.id == decision["review_id"]))
            .mappings()
            .first()
        )
        if previous is not None:
            if previous["agent_id"] != agent_id:
                raise BusinessError("not_found", "Review does not exist in this Agent space", status=404)
            if previous["action_id"] != action_id or previous["request_sha256"] != sha:
                raise BusinessError(
                    "action_review_conflict", "This review identity already records another observation"
                )
            return tcast(
                Record,
                {
                    **self.action_review_get(agent_id, action_id),
                    "replayed": True,
                    "review": self._action_review_public(tcast(Record, dict(previous))),
                },
            )
        return None

    def action_review_commit(self, agent_id: str, action_id: str, request: Record) -> Record:
        replay = self.action_review_replay(agent_id, action_id, request)
        if replay is not None:
            return replay
        decision = parse_review(request)
        sha = request_fingerprint(decision)
        current = self.action_review_get(agent_id, action_id)
        if not current["eligible"] or current["head"] is None:
            raise BusinessError("action_review_ineligible", "This action cannot accept an owner observation yet")
        if (
            decision["revision"] != current["revision"]
            or decision["history_epoch"] != current["history_epoch"]
            or decision["fingerprint"] != current["fingerprint"]
            or decision["policy"] != POLICY
        ):
            raise BusinessError(
                "action_review_conflict", "Action proof changed; inspect it before recording a decision"
            )
        head, proof = tcast(Record, current["head"]), tcast(Record, current["proof"])
        audit_id = self.audit(
            agent_id,
            "action.reviewed",
            action_id,
            tcast(
                Record,
                {
                    "review_id": decision["review_id"],
                    "decision": decision["decision"],
                    "fingerprint": decision["fingerprint"],
                    "unknown_preserved": True,
                },
            ),
        )
        evidence_sha = evidence_sha256(tcast(str, decision["evidence"]))
        review = self._record(
            s.action_reviews,
            {
                "id": decision["review_id"],
                "head_id": head["id"],
                "action_id": action_id,
                "run_id": current["run_id"],
                "head_revision": current["revision"],
                "history_epoch": current["history_epoch"],
                "policy": POLICY,
                "fingerprint": current["fingerprint"],
                "request_sha256": sha,
                "proof": proof,
                "decision": decision["decision"],
                "evidence_sha256": evidence_sha,
                "review_audit_id": audit_id,
            },
            agent_id,
        )
        self._record(
            s.action_review_evidence,
            {"review_id": review["id"], "body": decision["evidence"], "body_sha256": evidence_sha, "erased_at": None},
            agent_id,
        )
        changed = self.connection.execute(
            update(s.action_review_heads)
            .where(
                s.action_review_heads.c.agent_id == agent_id,
                s.action_review_heads.c.id == head["id"],
                s.action_review_heads.c.revision == current["revision"],
            )
            .values(
                revision=tcast(int, current["revision"]) + 1,
                state="reviewed",
                resolution=decision["decision"],
                last_review_id=review["id"],
                updated_at=self.clock.now(),
                error_code="",
            )
        ).rowcount
        if changed != 1:
            raise BusinessError("action_review_conflict", "Another owner observation committed first")
        return tcast(
            Record,
            {
                **self.action_review_get(agent_id, action_id),
                "replayed": False,
                "review": self._action_review_public(review),
            },
        )
