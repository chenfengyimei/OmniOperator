"""Release only reviewed retired action-object references; history is not a live object reference."""

from __future__ import annotations

from sqlalchemy import delete, insert, select, update

from ...domain.contracts import BusinessError
from ...domain.reference_release import FORMAT, POLICY, validate_reference_plan
from ...domain.retention import body_fingerprint, retention_page
from . import schema as s
from .execution_retention_repository import ExecutionRetentionRepositories


class ReferenceReleaseRepositories(ExecutionRetentionRepositories):
    def reference_inspect(self, agent_id: str, reference_id: str, *, batch_id: str = "") -> dict:
        reference = self._one(s.action_objects, agent_id, reference_id)
        action = self._one(s.actions, agent_id, reference["action_id"])
        run = self._one(s.runs, agent_id, action["run_id"])
        obj = self._one(s.objects, agent_id, reference["object_id"])
        inspected = self.retention_inspect(agent_id, run["id"])
        reasons = [reason for reason in inspected["protected_reasons"] if reason != "body_already_pruned"]
        if not run["body_pruned_at"] or action["body_pruned_at"] != run["body_pruned_at"]:
            reasons.append("body_not_pruned")
        completed = self.connection.execute(
            select(s.retention_targets.c.run_id)
            .join(s.retention_batches, s.retention_batches.c.id == s.retention_targets.c.batch_id)
            .where(
                s.retention_targets.c.batch_id == run["body_pruned_plan_id"],
                s.retention_targets.c.run_id == run["id"],
                s.retention_targets.c.agent_id == agent_id,
                s.retention_targets.c.state == "pruned",
                s.retention_targets.c.fingerprint == run["body_sha256"],
                s.retention_batches.c.state == "complete",
            )
        ).first()
        if not completed:
            reasons.append("pruning_batch_incomplete")
        if obj["pinned"] and "artifact_pinned" not in reasons:
            reasons.append("artifact_pinned")
        held = self.connection.execute(
            select(s.reference_release_targets.c.reference_id)
            .join(
                s.reference_release_batches, s.reference_release_batches.c.id == s.reference_release_targets.c.batch_id
            )
            .where(
                s.reference_release_targets.c.reference_id == reference_id,
                s.reference_release_targets.c.batch_id != batch_id,
                s.reference_release_targets.c.state == "pending",
                s.reference_release_batches.c.state == "prepared",
            )
        ).first()
        if held:
            reasons.append("reference_release_held")
        return {
            "agent_id": agent_id,
            "run_id": run["id"],
            "action_id": action["id"],
            "reference_id": reference_id,
            "object_id": obj["id"],
            "object_revision": obj["revision"],
            "object_sha256": obj["sha256"],
            "object_bytes": obj["bytes"],
            "prune_plan_id": run["body_pruned_plan_id"],
            "fingerprint": body_fingerprint(
                [{"reference": reference, "object": obj, "body": inspected["fingerprint"]}]
            )[0],
            "eligible": not reasons,
            "protected_reasons": reasons,
        }

    def reference_preview(self, after: str = "", limit: int = 20) -> dict:
        retention_page(after, limit)
        rows = list(
            self.connection.execute(
                select(s.action_objects.c.id, s.action_objects.c.agent_id)
                .join(s.actions, s.actions.c.id == s.action_objects.c.action_id)
                .where(s.action_objects.c.id > after, s.actions.c.body_pruned_at.is_not(None))
                .order_by(s.action_objects.c.id)
                .limit(limit + 1)
            )
        )
        return {
            "format": "omni-reference-release-preview-1",
            "policy": POLICY,
            "instance_id": self.connection.execute(
                select(s.instances.c.id).where(s.instances.c.slot == 1)
            ).scalar_one(),
            "schema_version": self.connection.exec_driver_sql("SELECT version_num FROM alembic_version").scalar_one(),
            "created_at": self.clock.now(),
            "items": [self.reference_inspect(agent, identity) for identity, agent in rows[:limit]],
            "next_after": rows[limit - 1].id if len(rows) > limit else None,
            "can_apply": False,
            "scope": "Only action-object references on pruned bodies; no object deletion or expiry changes",
        }

    def reference_plan(self, after: str = "", limit: int = 20) -> dict:
        preview = self.reference_preview(after, limit)
        targets = [
            {key: value for key, value in row.items() if key not in {"eligible", "protected_reasons"}}
            for row in preview["items"]
            if row["eligible"]
        ]
        if not targets:
            raise BusinessError(
                "no_reference_candidates", "No eligible references in this page; inspect the preview cursor"
            )
        return {
            "format": FORMAT,
            "id": self.ids.new(),
            "targets": targets,
            **{key: preview[key] for key in ("instance_id", "schema_version", "policy", "created_at")},
        }

    def reference_batch(self, identity: str) -> dict | None:
        row = (
            self.connection.execute(
                select(s.reference_release_batches).where(s.reference_release_batches.c.id == identity)
            )
            .mappings()
            .first()
        )
        return dict(row) if row else None

    def _reference_target(self, plan: dict, target: dict) -> bool:
        item = (
            self.connection.execute(
                select(s.reference_release_targets).where(
                    s.reference_release_targets.c.batch_id == plan["id"],
                    s.reference_release_targets.c.reference_id == target["reference_id"],
                )
            )
            .mappings()
            .first()
        )
        if item and item["state"] == "released":
            if any(item[key] != value for key, value in target.items()):
                raise BusinessError("reference_release_conflict", "Committed target identity changed")
            return False
        current = self.reference_inspect(target["agent_id"], target["reference_id"], batch_id=plan["id"])
        if not current["eligible"] or any(current[key] != value for key, value in target.items()):
            raise BusinessError(
                "reference_release_conflict", "Reference, object revision or business protection changed"
            )
        return True

    def reference_validate(self, plan: dict) -> None:
        sha = validate_reference_plan(plan, self.clock.now())
        instance = self.connection.execute(select(s.instances.c.id).where(s.instances.c.slot == 1)).scalar_one()
        version = self.connection.exec_driver_sql("SELECT version_num FROM alembic_version").scalar_one()
        if instance != plan["instance_id"] or version != plan["schema_version"]:
            raise BusinessError("reference_release_identity_mismatch", "Plan belongs to another instance or schema")
        batch = self.reference_batch(plan["id"])
        if batch and (batch["plan_sha256"] != sha or batch["state"] == "cancelled"):
            raise BusinessError("reference_release_conflict", "Plan was changed or cancelled")
        for target in plan["targets"]:
            self._reference_target(plan, target)

    def reference_prepare(self, plan: dict, backup_path: str, backup_sha256: str) -> dict:
        self.reference_validate(plan)
        batch = self.reference_batch(plan["id"])
        if batch:
            if batch["backup_path"] != backup_path or batch["backup_sha256"] != backup_sha256:
                raise BusinessError("reference_release_backup_changed", "Resume requires the original verified backup")
            return batch
        batch = self._record(
            s.reference_release_batches,
            {
                "id": plan["id"],
                "instance_id": plan["instance_id"],
                "plan_sha256": body_fingerprint([plan])[0],
                "policy": POLICY,
                "schema_version": plan["schema_version"],
                "backup_path": backup_path,
                "backup_sha256": backup_sha256,
                "state": "prepared",
            },
        )
        for target in plan["targets"]:
            self.connection.execute(
                insert(s.reference_release_targets).values(batch_id=plan["id"], state="pending", **target)
            )
            self.audit(
                target["agent_id"],
                "reference_release.prepared",
                plan["id"],
                {
                    "reference_id": target["reference_id"],
                    "fingerprint": target["fingerprint"],
                    "backup_sha256": backup_sha256,
                },
            )
        return batch

    def reference_release(self, plan: dict, target: dict) -> None:
        batch = self.reference_batch(plan["id"])
        sha = validate_reference_plan(plan, self.clock.now())
        if not batch or batch["plan_sha256"] != sha or target not in plan["targets"] or batch["state"] == "cancelled":
            raise BusinessError("reference_release_conflict", "A matching prepared plan is required")
        if not self._reference_target(plan, target):
            return
        pending = self.connection.execute(
            select(s.reference_release_targets.c.reference_id).where(
                s.reference_release_targets.c.batch_id == plan["id"],
                s.reference_release_targets.c.reference_id == target["reference_id"],
                s.reference_release_targets.c.state == "pending",
            )
        ).first()
        if not pending or batch["state"] != "prepared":
            raise BusinessError("reference_release_conflict", "A matching pending target is required")
        deleted = self.connection.execute(
            delete(s.action_objects).where(
                s.action_objects.c.id == target["reference_id"],
                s.action_objects.c.agent_id == target["agent_id"],
                s.action_objects.c.action_id == target["action_id"],
                s.action_objects.c.object_id == target["object_id"],
            )
        ).rowcount
        if deleted != 1:
            raise BusinessError("reference_release_conflict", "Reference identity changed")
        self.connection.execute(
            update(s.reference_release_targets)
            .where(
                s.reference_release_targets.c.batch_id == plan["id"],
                s.reference_release_targets.c.reference_id == target["reference_id"],
            )
            .values(state="released", ended_at=self.clock.now())
        )
        self.audit(
            target["agent_id"],
            "reference_release.released",
            plan["id"],
            {
                "reference_id": target["reference_id"],
                "object_id": target["object_id"],
                "fingerprint": target["fingerprint"],
                "backup_sha256": batch["backup_sha256"],
                "object_deleted": False,
            },
        )

    def reference_complete(self, identity: str) -> dict:
        batch = self.reference_batch(identity)
        if not batch or batch["state"] == "cancelled":
            raise BusinessError("reference_release_conflict", "A matching active plan is required")
        if self.connection.execute(
            select(s.reference_release_targets.c.reference_id).where(
                s.reference_release_targets.c.batch_id == identity,
                s.reference_release_targets.c.state != "released",
            )
        ).first():
            raise BusinessError("reference_release_incomplete", "Targets remain pending")
        if batch["state"] != "complete":
            self.connection.execute(
                update(s.reference_release_batches)
                .where(s.reference_release_batches.c.id == identity)
                .values(
                    state="complete",
                    completed_at=self.clock.now(),
                )
            )
        return {
            "plan_id": identity,
            "complete": True,
            "backup_sha256": batch["backup_sha256"],
            "objects_deleted": False,
        }

    def reference_cancel(self, plan: dict, reason: str) -> dict:
        sha = validate_reference_plan(plan, self.clock.now(), allow_future=True)
        batch = self.reference_batch(plan["id"])
        if not isinstance(reason, str) or not 1 <= len(reason.strip()) <= 500:
            raise BusinessError(
                "reference_release_invalid", "A cancellation reason of 1 to 500 characters is required", status=422
            )
        if not batch or batch["plan_sha256"] != sha or batch["state"] == "complete":
            raise BusinessError("reference_release_conflict", "Cancellation requires an unfinished original plan")
        if batch["state"] != "cancelled":
            self.connection.execute(
                update(s.reference_release_targets)
                .where(
                    s.reference_release_targets.c.batch_id == plan["id"],
                    s.reference_release_targets.c.state == "pending",
                )
                .values(state="cancelled", ended_at=self.clock.now())
            )
            self.connection.execute(
                update(s.reference_release_batches)
                .where(s.reference_release_batches.c.id == plan["id"])
                .values(
                    state="cancelled",
                    completed_at=self.clock.now(),
                )
            )
            for target in plan["targets"]:
                self.audit(
                    target["agent_id"],
                    "reference_release.cancelled",
                    plan["id"],
                    {
                        "reference_id": target["reference_id"],
                        "reason": reason.strip(),
                        "references_restored": False,
                    },
                )
        return {"plan_id": plan["id"], "cancelled": True, "references_restored": False}
