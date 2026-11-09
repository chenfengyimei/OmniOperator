"""Owner credential references, operation state and review; no native I/O or connection creation."""

from __future__ import annotations

import re
import uuid

from sqlalchemy import insert, select, update

from ...domain.contracts import BusinessError
from ...domain.credentials import AUTHORITY_POLICY, authority_fingerprint, reference_fingerprint, review_value
from . import schema as s
from .repository_base import RepositoryBase


class CredentialRepositories(RepositoryBase):
    @staticmethod
    def _credential_cursor(after: str, limit: int, maximum: int) -> None:
        try:
            valid = isinstance(after, str) and (not after or str(uuid.UUID(after)) == after)
        except (ValueError, AttributeError):
            valid = False
        if not valid or type(limit) is not int or not 1 <= limit <= maximum:
            raise BusinessError(
                "invalid_credential_query", "A full UUID cursor and bounded page are required", status=422
            )

    def _credential_owner(self) -> str:
        return self.connection.execute(select(s.instances.c.owner_id).where(s.instances.c.slot == 1)).scalar_one()

    def _credential_audit(self, identity: str, kind: str, outcome: str, data: dict) -> None:
        self.connection.execute(
            insert(s.instance_audit).values(
                id=self.ids.new(),
                created_at=self.clock.now(),
                owner_id=self._credential_owner(),
                operation_id=identity,
                kind=kind,
                outcome=outcome,
                data={"schema": 1, "boot_id": self.boot_id, **data},
            )
        )

    def _credential_row(self, identity: str) -> dict:
        row = (
            self.connection.execute(
                select(s.credential_references).where(
                    s.credential_references.c.id == identity,
                    s.credential_references.c.owner_id == self._credential_owner(),
                )
            )
            .mappings()
            .first()
        )
        if row is None:
            raise BusinessError("credential_not_found", "Credential reference does not exist", status=404)
        return dict(row)

    def credential_register(self, reference: str, *, audit: bool = True) -> dict:
        fingerprint = reference_fingerprint(reference)
        row = (
            self.connection.execute(
                select(s.credential_references).where(s.credential_references.c.reference_sha256 == fingerprint)
            )
            .mappings()
            .first()
        )
        if row:
            value = dict(row)
            if value["reference"] is None:
                self.connection.execute(
                    update(s.credential_references)
                    .where(s.credential_references.c.id == value["id"])
                    .values(
                        reference=reference,
                        revision=s.credential_references.c.revision + 1,
                        updated_at=self.clock.now(),
                    )
                )
                if audit:
                    self._credential_audit(
                        self.ids.new(),
                        "credential.registered",
                        "registered",
                        {
                            "credential_id": value["id"],
                            "reference_sha256": fingerprint,
                            "authority_state": value["state"],
                        },
                    )
            elif value["reference"] != reference:
                raise BusinessError("credential_reference_conflict", "Credential reference identity conflicts")
            return self.credential_get(value["id"])
        value = self._record(
            s.credential_references,
            {
                "owner_id": self._credential_owner(),
                "reference": reference,
                "reference_sha256": fingerprint,
                "state": "ready",
                "revision": 1,
                "authority_epoch": 1,
                "current_operation_id": None,
                "updated_at": self.clock.now(),
            },
        )
        if audit:
            self._credential_audit(
                self.ids.new(),
                "credential.registered",
                "registered",
                {"credential_id": value["id"], "reference_sha256": fingerprint, "authority_state": "ready"},
            )
        return self.credential_get(value["id"])

    def credential_get(self, identity: str) -> dict:
        row = self._credential_row(identity)
        operation = None
        if row["current_operation_id"]:
            operation = dict(
                self.connection.execute(
                    select(s.credential_operations).where(
                        s.credential_operations.c.id == row["current_operation_id"],
                        s.credential_operations.c.credential_id == identity,
                    )
                )
                .mappings()
                .one()
            )
            operation.pop("execution_token")
        return {
            **row,
            "policy": AUTHORITY_POLICY,
            "fingerprint": authority_fingerprint(row),
            "current_operation": operation,
            "owner_authorization_only": True,
        }

    def credential_page(self, after: str = "", limit: int = 100) -> list[dict]:
        self._credential_cursor(after, limit, 200)
        ids = self.connection.execute(
            select(s.credential_references.c.id)
            .where(s.credential_references.c.id > after, s.credential_references.c.owner_id == self._credential_owner())
            .order_by(s.credential_references.c.id)
            .limit(limit)
        ).scalars()
        return [self.credential_get(identity) for identity in ids]

    def credential_authorize(self, reference: str) -> None:
        if not reference:
            return
        fingerprint = reference_fingerprint(reference)
        state = self.connection.execute(
            select(s.credential_references.c.state).where(
                s.credential_references.c.reference_sha256 == fingerprint,
                s.credential_references.c.owner_id == self._credential_owner(),
            )
        ).scalar_one_or_none()
        code = {
            None: "credential_unregistered",
            "changing": "credential_changing",
            "unknown": "credential_requires_review",
            "revoked": "credential_revoked",
        }.get(state)
        if code:
            raise BusinessError(code, "Credential reference is not authorized for use; inspect owner credential state")

    def credential_begin(self, reference: str, operation: str, identity: str) -> dict:
        if operation not in {"set", "delete"}:
            raise BusinessError("invalid_credential_operation", "Credential operation is invalid", status=422)
        row = self.credential_register(reference, audit=False)
        if row["state"] == "changing":
            raise BusinessError("credential_changing", "An actual credential operation is still active")
        if row["state"] == "unknown":
            raise BusinessError(
                "credential_requires_review", "Review the unknown credential reference before another change"
            )
        token = self.ids.new()
        value = self._record(
            s.credential_operations,
            {
                "id": identity,
                "owner_id": row["owner_id"],
                "credential_id": row["id"],
                "operation": operation,
                "state": "prepared",
                "prior_state": row["state"],
                "revision": 1,
                "authority_epoch": row["authority_epoch"],
                "boot_id": self.boot_id,
                "execution_token": token,
                "legacy": False,
                "error_code": "",
            },
        )
        self.connection.execute(
            update(s.credential_references)
            .where(s.credential_references.c.id == row["id"])
            .values(
                state="changing",
                current_operation_id=identity,
                revision=s.credential_references.c.revision + 1,
                updated_at=self.clock.now(),
            )
        )
        self._credential_audit_intent(identity, row["reference_sha256"], operation)
        return value

    def _credential_operation(self, identity: str, token: str) -> dict:
        row = (
            self.connection.execute(
                select(s.credential_operations).where(
                    s.credential_operations.c.id == identity,
                    s.credential_operations.c.owner_id == self._credential_owner(),
                )
            )
            .mappings()
            .first()
        )
        if row is None or row["execution_token"] != token or row["boot_id"] != self.boot_id or row["legacy"]:
            raise BusinessError("credential_operation_fenced", "Credential execution identity is no longer current")
        return dict(row)

    def credential_dispatch(self, identity: str, token: str) -> None:
        operation = self._credential_operation(identity, token)
        row = self._credential_row(operation["credential_id"])
        if (
            operation["state"] != "prepared"
            or row["state"] != "changing"
            or row["current_operation_id"] != identity
            or row["authority_epoch"] != operation["authority_epoch"]
        ):
            raise BusinessError("credential_operation_fenced", "Credential operation cannot be dispatched")
        self.connection.execute(
            update(s.credential_operations)
            .where(s.credential_operations.c.id == identity)
            .values(state="running", started_at=self.clock.now(), revision=s.credential_operations.c.revision + 1)
        )
        self.connection.execute(
            update(s.credential_references)
            .where(s.credential_references.c.id == row["id"])
            .values(revision=s.credential_references.c.revision + 1, updated_at=self.clock.now())
        )

    def _credential_finish(self, operation: dict, outcome: str, code: str = "") -> dict:
        row = self._credential_row(operation["credential_id"])
        if outcome not in {"applied", "not_applied", "unknown"}:
            raise BusinessError("invalid_credential_outcome", "Credential outcome is invalid")
        if operation["state"] in {"applied", "not_applied", "unknown"}:
            if operation["state"] != outcome:
                raise BusinessError("credential_outcome_conflict", "Credential outcome has already been recorded")
            return self.credential_get(row["id"])
        if (
            row["current_operation_id"] != operation["id"]
            or row["state"] != "changing"
            or row["authority_epoch"] != operation["authority_epoch"]
            or (outcome == "applied" and operation["state"] != "running")
        ):
            raise BusinessError("credential_operation_fenced", "Credential result does not match the current operation")
        state = (
            "unknown"
            if outcome == "unknown"
            else operation["prior_state"]
            if outcome == "not_applied"
            else "ready"
            if operation["operation"] == "set"
            else "revoked"
        )
        self.connection.execute(
            update(s.credential_operations)
            .where(s.credential_operations.c.id == operation["id"])
            .values(
                state=outcome,
                error_code=code,
                ended_at=self.clock.now(),
                revision=s.credential_operations.c.revision + 1,
            )
        )
        self.connection.execute(
            update(s.credential_references)
            .where(s.credential_references.c.id == row["id"])
            .values(state=state, revision=s.credential_references.c.revision + 1, updated_at=self.clock.now())
        )
        self._credential_audit_outcome(operation["id"], outcome)
        return self.credential_get(row["id"])

    def credential_settle(self, identity: str, token: str, outcome: str, code: str = "") -> dict:
        return self._credential_finish(self._credential_operation(identity, token), outcome, code)

    def credential_review(
        self,
        identity: str,
        revision: int,
        epoch: int,
        fingerprint: str,
        policy: str,
        decision: str,
        evidence: str,
        *,
        review_id: str | None = None,
    ) -> dict:
        review_value(decision, evidence)
        if type(revision) is not int or revision < 1 or type(epoch) is not int or epoch < 1:
            raise BusinessError(
                "invalid_credential_review", "Review revision and epoch must be positive integers", status=422
            )
        row = self._credential_row(identity)
        if (
            policy != AUTHORITY_POLICY
            or revision != row["revision"]
            or epoch != row["authority_epoch"]
            or fingerprint != authority_fingerprint(row)
        ):
            raise BusinessError(
                "credential_review_changed", "Credential state or policy changed; reload before reviewing"
            )
        if row["state"] == "changing":
            raise BusinessError("credential_changing", "An actual credential operation is still active")
        if row["state"] != "unknown":
            raise BusinessError(
                "credential_review_not_required", "Only an unknown credential reference can be reviewed"
            )
        if decision == "allow_current" and not row["reference"]:
            raise BusinessError(
                "credential_reference_unbound", "Bind the exact reference name in configuration before allowing use"
            )
        review = self._record(
            s.credential_reviews,
            {
                "id": review_id or self.ids.new(),
                "owner_id": row["owner_id"],
                "credential_id": identity,
                "operation_id": row["current_operation_id"],
                "reference_revision": revision,
                "authority_epoch": epoch,
                "fingerprint": fingerprint,
                "policy": policy,
                "decision": decision,
                "evidence": evidence.strip(),
            },
        )
        self.connection.execute(
            update(s.credential_references)
            .where(s.credential_references.c.id == identity)
            .values(
                state="ready" if decision == "allow_current" else "revoked",
                revision=s.credential_references.c.revision + 1,
                updated_at=self.clock.now(),
            )
        )
        self._credential_audit(
            review["id"],
            "credential.reviewed",
            "allowed" if decision == "allow_current" else "revoked",
            {
                "credential_id": identity,
                "reference_sha256": row["reference_sha256"],
                "authority_epoch": epoch,
                "review_id": review["id"],
            },
        )
        return self.credential_get(identity)

    def credential_history(self, identity: str, after: str = "", limit: int = 50) -> list[dict]:
        self._credential_row(identity)
        self._credential_cursor(after, limit, 100)
        return [
            {key: value for key, value in row.items() if key != "execution_token"}
            for row in self.connection.execute(
                select(s.credential_operations)
                .where(s.credential_operations.c.credential_id == identity, s.credential_operations.c.id > after)
                .order_by(s.credential_operations.c.id)
                .limit(limit)
            ).mappings()
        ]

    def credential_review_history(self, identity: str, after: str = "", limit: int = 50) -> list[dict]:
        self._credential_row(identity)
        self._credential_cursor(after, limit, 100)
        return [
            dict(row)
            for row in self.connection.execute(
                select(s.credential_reviews)
                .where(s.credential_reviews.c.credential_id == identity, s.credential_reviews.c.id > after)
                .order_by(s.credential_reviews.c.id)
                .limit(limit)
            ).mappings()
        ]

    def credential_recover(self) -> int:
        rows = (
            self.connection.execute(
                select(s.credential_operations).where(
                    s.credential_operations.c.state.in_(["prepared", "running"]),
                    s.credential_operations.c.boot_id != self.boot_id,
                )
            )
            .mappings()
            .all()
        )
        for item in rows:
            operation = dict(item)
            outcome = "not_applied" if operation["state"] == "prepared" else "unknown"
            self._credential_finish(operation, outcome, "credential_process_interrupted")
            self._credential_audit(
                operation["id"],
                "credential.recovered",
                outcome,
                {"credential_id": operation["credential_id"], "original_boot_id": operation["boot_id"]},
            )
        return len(rows)

    def _credential_audit_intent(self, operation_id: str, reference_hash: str, operation: str) -> None:
        if operation not in {"set", "delete"} or not re.fullmatch(r"[a-f0-9]{64}", reference_hash):
            raise BusinessError("invalid_credential_intent", "Credential intent metadata is invalid", status=422)
        owner = self.connection.execute(select(s.instances.c.owner_id).where(s.instances.c.slot == 1)).scalar_one()
        self.connection.execute(
            insert(s.instance_audit).values(
                id=self.ids.new(),
                created_at=self.clock.now(),
                owner_id=owner,
                operation_id=operation_id,
                kind="credential.requested",
                outcome="requested",
                data={"schema": 1, "reference_sha256": reference_hash, "operation": operation, "boot_id": self.boot_id},
            )
        )

    def _credential_audit_outcome(self, operation_id: str, outcome: str) -> None:
        if outcome not in {"applied", "not_applied", "unknown"}:
            raise BusinessError("invalid_credential_outcome", "Credential outcome is invalid")
        requested = (
            self.connection.execute(
                select(s.instance_audit).where(
                    s.instance_audit.c.operation_id == operation_id, s.instance_audit.c.kind == "credential.requested"
                )
            )
            .mappings()
            .one()
        )
        existing = self.connection.execute(
            select(s.instance_audit.c.outcome).where(
                s.instance_audit.c.operation_id == operation_id, s.instance_audit.c.kind == "credential.resolved"
            )
        ).scalar_one_or_none()
        if existing is not None:
            if existing != outcome:
                raise BusinessError("credential_outcome_conflict", "Credential outcome has already been recorded")
            return
        self.connection.execute(
            insert(s.instance_audit).values(
                id=self.ids.new(),
                created_at=self.clock.now(),
                owner_id=requested["owner_id"],
                operation_id=operation_id,
                kind="credential.resolved",
                outcome=outcome,
                data=requested["data"],
            )
        )
