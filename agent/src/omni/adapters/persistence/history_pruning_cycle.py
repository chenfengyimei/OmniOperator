"""One transactional history-pruning algorithm; stream policies own target eligibility and identity."""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass

from sqlalchemy import Table, delete, func, insert, select, update

from ...domain.contracts import BusinessError
from ...domain.retention import body_fingerprint
from . import schema as s
from .repository_base import RepositoryBase


@dataclass(frozen=True)
class PruningSpec:
    stream: str
    prefix: str
    source: Table
    batches: Table
    targets: Table
    source_key: str
    target_key: str
    identity_fields: tuple[tuple[str, str], ...]
    restored_field: str
    validate: Callable[..., str]
    other_batches: Table


class PruningCycle:
    def __init__(self, unit: RepositoryBase, spec: PruningSpec, resolve: Callable[..., dict]) -> None:
        self.unit, self.spec, self.resolve = unit, spec, resolve
        self.connection = unit.connection

    def batch(self, identity: str) -> dict | None:
        row = (
            self.connection.execute(select(self.spec.batches).where(self.spec.batches.c.id == identity))
            .mappings()
            .first()
        )
        return dict(row) if row else None

    def bound_batch(self, plan: dict, *, cancelling: bool = False) -> dict | None:
        sha = self.spec.validate(plan, self.unit.clock.now(), allow_future=cancelling)
        instance = self.connection.execute(select(s.instances.c.id)).scalar_one()
        version = self.connection.exec_driver_sql("SELECT version_num FROM alembic_version").scalar_one()
        if instance != plan["instance_id"] or version != plan["schema_version"]:
            raise BusinessError(f"{self.spec.prefix}_identity_mismatch", "Plan belongs to another instance or schema")
        if not self.connection.execute(select(s.agents.c.id).where(s.agents.c.id == plan["agent_id"])).first():
            raise BusinessError(f"{self.spec.prefix}_identity_mismatch", "Plan Agent does not exist in this instance")
        batch = self.batch(plan["id"])
        if batch and (batch["plan_sha256"] != sha or batch["agent_id"] != plan["agent_id"]):
            raise BusinessError(f"{self.spec.prefix}_conflict", "Persisted plan identity changed")
        if not cancelling:
            epoch = self.connection.execute(select(s.history_identity.c.epoch)).scalar_one()
            if epoch != plan["epoch"]:
                raise BusinessError(
                    f"{self.spec.prefix}_history_changed", "Restored history requires a fresh reviewed plan"
                )
            high_water = self.connection.execute(
                select(s.history_sequences.c.last_issued).where(s.history_sequences.c.kind == self.spec.stream)
            ).scalar_one()
            if high_water < plan["high_water"]:
                raise BusinessError(f"{self.spec.prefix}_history_changed", "Plan ceiling exceeds allocated history")
            if batch and batch["state"] == "cancelled":
                raise BusinessError(f"{self.spec.prefix}_conflict", "The history maintenance plan was cancelled")
        return batch

    def progress(self, plan: dict, batch: dict | None) -> dict[str | int, dict]:
        rows = {
            row[self.spec.target_key]: dict(row)
            for row in self.connection.execute(
                select(self.spec.targets).where(self.spec.targets.c.batch_id == plan["id"])
            ).mappings()
        }
        expected = {target[self.spec.target_key] for target in plan["targets"]}
        if (batch and set(rows) != expected) or (not batch and rows):
            raise BusinessError(f"{self.spec.prefix}_conflict", "Persisted target set differs from the reviewed plan")
        for target in plan["targets"]:
            old = rows.get(target[self.spec.target_key])
            if old and any(old[key] != value for key, value in target.items()):
                raise BusinessError(
                    f"{self.spec.prefix}_conflict", "Persisted target identity differs from the reviewed plan"
                )
        # A complete receipt stays valid after later legitimate cleanup. It still needs
        # the exact plan/instance/schema/epoch/backup contract and all deletion proofs.
        if not batch or batch["state"] != "complete":
            current = self.connection.execute(
                select(s.history_scopes.c.trim_revision).where(
                    s.history_scopes.c.kind == self.spec.stream, s.history_scopes.c.scope_id == plan["agent_id"]
                )
            ).scalar_one()
            deleted = sum(row["state"] == "deleted" for row in rows.values())
            if current != plan["trim_revision"] + deleted:
                raise BusinessError(
                    f"{self.spec.prefix}_history_changed", "History changed outside this maintenance plan"
                )
        return rows

    def recheck(self, plan: dict, target: dict, progress: dict | None) -> bool:
        if progress and progress["state"] == "deleted":
            if self.connection.execute(
                select(self.spec.source.c[self.spec.source_key]).where(
                    self.spec.source.c[self.spec.source_key] == target[self.spec.target_key]
                )
            ).first():
                raise BusinessError(f"{self.spec.prefix}_conflict", "Deleted history identity has reappeared")
            return False
        if progress and progress["state"] != "pending":
            raise BusinessError(f"{self.spec.prefix}_conflict", "Target is not pending")
        current = self.resolve(plan["agent_id"], target[self.spec.target_key], plan["cutoff"], plan["id"])
        if current != target:
            raise BusinessError(f"{self.spec.prefix}_conflict", "History content, subject or pruning proof changed")
        return True

    def validate(self, plan: dict) -> None:
        batch = self.bound_batch(plan)
        other = self.connection.execute(
            select(self.spec.batches.c.id)
            .where(
                self.spec.batches.c.agent_id == plan["agent_id"],
                self.spec.batches.c.id != plan["id"],
                self.spec.batches.c.state == "prepared",
            )
            .limit(1)
        ).first()
        peer = self.connection.execute(
            select(self.spec.other_batches.c.id)
            .where(
                self.spec.other_batches.c.agent_id == plan["agent_id"],
                self.spec.other_batches.c.state == "prepared",
            )
            .limit(1)
        ).first()
        if (other or peer) and (not batch or batch["state"] != "complete"):
            raise BusinessError(f"{self.spec.prefix}_conflict", "Another history maintenance plan owns this Agent")
        rows = self.progress(plan, batch)
        for target in plan["targets"]:
            pending = self.recheck(plan, target, rows.get(target[self.spec.target_key]))
            if batch and batch["state"] == "complete" and pending:
                raise BusinessError(f"{self.spec.prefix}_conflict", "Complete receipt has an undeleted target")

    def prepare(self, plan: dict, backup_path: str, backup_sha256: str) -> dict:
        self.validate(plan)
        batch = self.batch(plan["id"])
        if batch:
            if batch["backup_path"] != backup_path or batch["backup_sha256"] != backup_sha256:
                raise BusinessError(
                    f"{self.spec.prefix}_backup_changed", "Resume requires the original verified snapshot"
                )
            return batch
        batch = self.unit._record(
            self.spec.batches,
            {
                "id": plan["id"],
                "plan_sha256": body_fingerprint([plan])[0],
                **{
                    key: plan[key]
                    for key in (
                        "instance_id",
                        "policy",
                        "schema_version",
                        "as_of",
                        "cutoff",
                        "epoch",
                        "trim_revision",
                        "high_water",
                    )
                },
                "backup_path": backup_path,
                "backup_sha256": backup_sha256,
                "state": "prepared",
            },
            plan["agent_id"],
        )
        for target in plan["targets"]:
            self.connection.execute(insert(self.spec.targets).values(batch_id=plan["id"], state="pending", **target))
        self.unit.audit(
            plan["agent_id"],
            f"{self.spec.prefix}.prepared",
            plan["id"],
            {
                "targets": len(plan["targets"]),
                "plan_sha256": batch["plan_sha256"],
                "backup_sha256": backup_sha256,
            },
        )
        return batch

    def prune(self, plan: dict, target: dict) -> None:
        batch = self.bound_batch(plan)
        if not batch or target not in plan["targets"]:
            raise BusinessError(f"{self.spec.prefix}_conflict", "Deletion requires an original prepared target")
        rows = self.progress(plan, batch)
        progress = rows.get(target[self.spec.target_key])
        if not self.recheck(plan, target, progress):
            return
        if batch["state"] != "prepared" or not progress:
            raise BusinessError(f"{self.spec.prefix}_conflict", "Deletion requires a persisted pending target")
        deleted = self.connection.execute(
            delete(self.spec.source).where(
                self.spec.source.c.agent_id == plan["agent_id"],
                *(self.spec.source.c[source] == target[key] for source, key in self.spec.identity_fields),
            )
        ).rowcount
        if deleted != 1:
            raise BusinessError(f"{self.spec.prefix}_conflict", "The exact original history row could not be deleted")
        self.connection.execute(
            update(self.spec.targets)
            .where(
                self.spec.targets.c.batch_id == plan["id"],
                self.spec.targets.c[self.spec.target_key] == target[self.spec.target_key],
                self.spec.targets.c.state == "pending",
            )
            .values(state="deleted", ended_at=self.unit.clock.now())
        )
        self.unit.audit(
            plan["agent_id"],
            f"{self.spec.prefix}.deleted",
            plan["id"],
            {
                self.spec.target_key: target[self.spec.target_key],
                "sequence": target["sequence"],
                "fingerprint": target["fingerprint"],
                "backup_sha256": batch["backup_sha256"],
            },
        )

    def complete(self, identity: str) -> dict:
        batch = self.batch(identity)
        if not batch or batch["state"] == "cancelled":
            raise BusinessError(f"{self.spec.prefix}_conflict", "Completion requires an active original plan")
        states = (
            self.connection.execute(select(self.spec.targets.c.state).where(self.spec.targets.c.batch_id == identity))
            .scalars()
            .all()
        )
        if not states or any(state != "deleted" for state in states):
            raise BusinessError(f"{self.spec.prefix}_incomplete", "History targets remain pending")
        current_epoch = self.connection.execute(select(s.history_identity.c.epoch)).scalar_one()
        if current_epoch != batch["epoch"]:
            raise BusinessError(f"{self.spec.prefix}_history_changed", "Restored history cannot complete an old plan")
        if batch["state"] != "complete":
            current_revision = self.connection.execute(
                select(s.history_scopes.c.trim_revision).where(
                    s.history_scopes.c.kind == self.spec.stream, s.history_scopes.c.scope_id == batch["agent_id"]
                )
            ).scalar_one()
            if current_revision != batch["trim_revision"] + len(states):
                raise BusinessError(f"{self.spec.prefix}_history_changed", "History changed before completion")
            self.connection.execute(
                update(self.spec.batches)
                .where(self.spec.batches.c.id == identity)
                .values(state="complete", completed_at=self.unit.clock.now())
            )
            self.unit.audit(batch["agent_id"], f"{self.spec.prefix}.completed", identity, {"deleted": len(states)})
        return {
            "plan_id": identity,
            "complete": True,
            "deleted": len(states),
            "backup_sha256": batch["backup_sha256"],
            "business_records_deleted": False,
        }

    def cancel(self, plan: dict, reason: str) -> dict:
        batch = self.bound_batch(plan, cancelling=True)
        if not isinstance(reason, str) or not 1 <= len(reason.strip()) <= 500:
            raise BusinessError(
                f"{self.spec.prefix}_invalid", "Cancellation needs a reason of 1 to 500 characters", status=422
            )
        if not batch or batch["state"] == "complete":
            raise BusinessError(f"{self.spec.prefix}_conflict", "Cancellation requires an unfinished original plan")
        if batch["state"] != "cancelled":
            self.connection.execute(
                update(self.spec.targets)
                .where(
                    self.spec.targets.c.batch_id == plan["id"],
                    self.spec.targets.c.state == "pending",
                )
                .values(state="cancelled", ended_at=self.unit.clock.now())
            )
            self.connection.execute(
                update(self.spec.batches)
                .where(self.spec.batches.c.id == plan["id"])
                .values(state="cancelled", completed_at=self.unit.clock.now())
            )
            self.unit.audit(
                plan["agent_id"],
                f"{self.spec.prefix}.cancelled",
                plan["id"],
                {"reason": reason.strip(), self.spec.restored_field: False},
            )
        deleted = self.connection.execute(
            select(func.count())
            .select_from(self.spec.targets)
            .where(
                self.spec.targets.c.batch_id == plan["id"],
                self.spec.targets.c.state == "deleted",
            )
        ).scalar_one()
        return {"plan_id": plan["id"], "cancelled": True, "already_deleted": deleted, self.spec.restored_field: False}
