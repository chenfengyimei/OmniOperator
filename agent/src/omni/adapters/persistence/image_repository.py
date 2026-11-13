"""Scoped image metadata, model-revision policies and per-call references; never reads files or encodes images."""

from __future__ import annotations

from typing import TYPE_CHECKING, cast

from sqlalchemy import func, select, update

from ...application.ports import Record
from ...domain.contracts import BusinessError
from ...domain.images import POLICY_DEFAULT, ImageInfo, fingerprint, model_tokens, policy
from . import schema as s
from .configuration_repository import ConfigurationRepositories

if TYPE_CHECKING:
    from .repository import Repositories


class ImageRepositories(ConfigurationRepositories):
    def image_register(self, agent_id: str, object_id: str, info: ImageInfo) -> Record:
        info.validate()
        unit = cast("Repositories", self)
        obj = unit.object_get(agent_id, object_id)
        if obj["sha256"] != info.sha256 or obj["bytes"] != info.bytes:
            raise BusinessError("image_integrity_failed", "Image object changed during inspection")
        prior = (
            self.connection.execute(
                select(s.image_objects).where(
                    s.image_objects.c.agent_id == agent_id, s.image_objects.c.object_id == object_id
                )
            )
            .mappings()
            .first()
        )
        if prior:
            if any(prior[k] != getattr(info, k) for k in ("width", "height", "media_type")):
                raise BusinessError("image_integrity_failed", "Image metadata is inconsistent")
            row = cast(Record, dict(prior))
        else:
            row = self._record(
                s.image_objects,
                {"object_id": object_id, "width": info.width, "height": info.height, "media_type": info.media_type},
                agent_id,
            )
            self.audit(
                agent_id,
                "image.registered",
                object_id,
                {"sha256": info.sha256, "width": info.width, "height": info.height},
            )
        return {**row, "sha256": obj["sha256"], "bytes": obj["bytes"], "state": "registered_not_attached"}

    def model_image_policy(self, agent_id: str, revision_id: str) -> Record:
        revision = self._one(s.configuration_revisions, agent_id, revision_id)
        if revision["kind"] != "model":
            raise BusinessError("image_policy_invalid", "Image policies belong to model revisions", status=422)
        row = (
            self.connection.execute(
                select(s.model_image_policies).where(
                    s.model_image_policies.c.agent_id == agent_id, s.model_image_policies.c.revision_id == revision_id
                )
            )
            .mappings()
            .first()
        )
        return cast(
            Record,
            dict(row)
            if row
            else {**POLICY_DEFAULT, "id": "", "revision_id": revision_id, "fingerprint": fingerprint(POLICY_DEFAULT)},
        )

    def model_image_policy_add(self, agent_id: str, revision_id: str, value: Record) -> Record:
        revision = self._configuration_revision(agent_id, revision_id)
        if revision["kind"] != "model":
            raise BusinessError("image_policy_invalid", "Only models have image policies", status=422)
        checked = policy(value, cast(str, cast(Record, revision["value"])["model"]))
        row = self._record(
            s.model_image_policies,
            {"revision_id": revision_id, **checked, "fingerprint": fingerprint(checked)},
            agent_id,
        )
        self.audit(
            agent_id,
            "model.image_policy_recorded",
            cast(str, row["id"]),
            {"revision_id": revision_id, "fingerprint": row["fingerprint"]},
        )
        return row

    def run_image_policy(self, agent_id: str, run_id: str) -> Record:
        run = self._one(s.runs, agent_id, run_id)
        if not run["model_revision_id"]:
            return cast(Record, {**POLICY_DEFAULT, "id": "", "fingerprint": fingerprint(POLICY_DEFAULT)})
        return self.model_image_policy(agent_id, cast(str, run["model_revision_id"]))

    def image_parts(self, agent_id: str, message_ids: list[str]) -> list[Record]:
        if not message_ids:
            return []
        if len(message_ids) > 128:
            raise BusinessError("invalid_context", "Image source query exceeds the context limit")
        rows = (
            self.connection.execute(
                select(
                    s.message_parts.c.id.label("part_id"),
                    s.message_parts.c.message_id,
                    s.message_parts.c.position,
                    s.message_parts.c.object_id,
                    s.image_objects.c.width,
                    s.image_objects.c.height,
                    s.image_objects.c.media_type,
                    s.objects.c.sha256,
                    s.objects.c.bytes,
                )
                .join(
                    s.image_objects,
                    (s.image_objects.c.object_id == s.message_parts.c.object_id)
                    & (s.image_objects.c.agent_id == s.message_parts.c.agent_id),
                )
                .join(
                    s.objects,
                    (s.objects.c.id == s.message_parts.c.object_id)
                    & (s.objects.c.agent_id == s.message_parts.c.agent_id),
                )
                .join(
                    s.messages,
                    (s.messages.c.id == s.message_parts.c.message_id)
                    & (s.messages.c.agent_id == s.message_parts.c.agent_id),
                )
                .where(
                    s.message_parts.c.agent_id == agent_id,
                    s.message_parts.c.kind == "image",
                    s.message_parts.c.message_id.in_(message_ids),
                    s.messages.c.deleted_at.is_(None),
                )
                .order_by(s.message_parts.c.message_id, s.message_parts.c.position)
                .limit(513)
            )
            .mappings()
            .all()
        )
        if len(rows) > 512:
            raise BusinessError("invalid_context", "Image source query exceeds its bound")
        return [cast(Record, dict(r)) for r in rows]

    def image_admit(self, agent_id: str, model_revision_id: str, ids: list[str]) -> None:
        config = self.model_image_policy(agent_id, model_revision_id)
        if config["protocol"] == "disabled":
            raise BusinessError(
                "image_capability_disabled", "Enable a supported image protocol on the selected model first", status=422
            )
        if len(ids) > cast(int, config["per_call_limit"]):
            raise BusinessError(
                "image_call_limit_exceeded", "Current images exceed this model's per-call image limit", status=422
            )
        for object_id in ids:
            cast("Repositories", self).object_get(agent_id, object_id)
            if (
                self.connection.execute(
                    select(s.image_objects.c.id).where(
                        s.image_objects.c.agent_id == agent_id, s.image_objects.c.object_id == object_id
                    )
                ).first()
                is None
            ):
                raise BusinessError("image_unverified", "Verify the image before attaching it", status=422)

    def model_images_prepare(self, agent_id: str, run_id: str, call_id: str, refs: list[Record]) -> None:
        unit = cast("Repositories", self)
        unit.assert_run_authorized(agent_id, run_id)
        call = self._one(s.model_calls, agent_id, call_id)
        config = self.run_image_policy(agent_id, run_id)
        if (
            call["run_id"] != run_id
            or call["state"] != "reserved"
            or call["purpose"] != "turn"
            or config["protocol"] == "disabled"
        ):
            raise BusinessError("image_call_invalid", "Image call is not an authorized chat request")
        snapshot = self.connection.execute(
            select(s.context_snapshots.c.id)
            .where(s.context_snapshots.c.agent_id == agent_id, s.context_snapshots.c.run_id == run_id)
            .order_by(s.context_snapshots.c.ordinal.desc())
            .limit(1)
        ).scalar_one()
        ids = cast(
            list[str],
            list(
                self.connection.execute(
                    select(s.context_messages.c.message_id).where(s.context_messages.c.snapshot_id == snapshot)
                ).scalars()
            ),
        )
        sources = {r["part_id"]: r for r in self.image_parts(agent_id, ids)}
        spent = self.connection.execute(
            select(func.count())
            .select_from(s.model_call_images)
            .join(s.model_calls, s.model_calls.c.id == s.model_call_images.c.call_id)
            .where(
                s.model_call_images.c.agent_id == agent_id,
                s.model_calls.c.run_id == run_id,
                s.model_call_images.c.state != "not_sent",
            )
        ).scalar_one()
        if len(refs) > cast(int, config["per_call_limit"]) or spent + len(refs) > cast(int, config["run_budget"]):
            raise BusinessError("image_budget_exceeded", "This run has exhausted its cumulative image budget")
        settings = self.run_settings(agent_id, run_id)
        for ref in refs:
            source = sources.get(cast(str, ref["part_id"]))
            if (
                not source
                or ref["policy_id"] != config["id"]
                or ref["token_cost"] != model_tokens(cast(str, settings["model"]))
                or any(source[k] != ref[k] for k in ("object_id", "sha256", "width", "height", "media_type", "bytes"))
            ):
                raise BusinessError("image_source_changed", "Image is outside the captured context or policy")
            self._record(
                s.model_call_images,
                {
                    "call_id": call_id,
                    "snapshot_id": snapshot,
                    "policy_id": config["id"],
                    "message_id": source["message_id"],
                    "part_id": ref["part_id"],
                    "object_id": ref["object_id"],
                    "sha256": ref["sha256"],
                    "state": "prepared",
                },
                agent_id,
            )
        self.audit(
            agent_id,
            "model.images_prepared",
            call_id,
            {"count": len(refs), "policy_id": config["id"], "used_before": spent},
        )

    def model_images_dispatch(self, agent_id: str, run_id: str, call_id: str) -> None:
        cast("Repositories", self).assert_run_authorized(agent_id, run_id)
        rows = (
            self.connection.execute(
                select(s.model_call_images).where(
                    s.model_call_images.c.agent_id == agent_id, s.model_call_images.c.call_id == call_id
                )
            )
            .mappings()
            .all()
        )
        if not rows or any(r["state"] != "prepared" or r["erased_at"] or not r["object_id"] for r in rows):
            raise BusinessError("image_source_changed", "Image intent changed before dispatch")
        self.connection.execute(
            update(s.model_call_images)
            .where(s.model_call_images.c.agent_id == agent_id, s.model_call_images.c.call_id == call_id)
            .values(state="dispatched")
        )
        self.audit(agent_id, "model.images_attached_to_request", call_id, {"count": len(rows)})

    def model_images_report(self, agent_id: str, run_id: str) -> Record:
        run = self._one(s.runs, agent_id, run_id)
        config = self.run_image_policy(agent_id, run_id)
        incoming = self.image_parts(agent_id, [cast(str, run["input_message_id"])])
        rows = (
            self.connection.execute(
                select(s.model_call_images, s.model_calls.c.state.label("call_state"))
                .join(s.model_calls, s.model_calls.c.id == s.model_call_images.c.call_id)
                .where(s.model_call_images.c.agent_id == agent_id, s.model_calls.c.run_id == run_id)
                .order_by(s.model_call_images.c.created_at, s.model_call_images.c.id)
                .limit(257)
            )
            .mappings()
            .all()
        )
        scope = (s.model_call_images.c.agent_id == agent_id, s.model_calls.c.run_id == run_id)
        used = self.connection.execute(
            select(func.count())
            .select_from(s.model_call_images)
            .join(s.model_calls, s.model_calls.c.id == s.model_call_images.c.call_id)
            .where(*scope, s.model_call_images.c.state != "not_sent")
        ).scalar_one()
        selected = self.connection.execute(
            select(s.context_snapshots.c.id)
            .where(s.context_snapshots.c.run_id == run_id, s.context_snapshots.c.agent_id == agent_id)
            .order_by(s.context_snapshots.c.ordinal.desc())
            .limit(1)
        ).scalar_one_or_none()
        source_ids = (
            cast(
                list[str],
                list(
                    self.connection.execute(
                        select(s.context_messages.c.message_id).where(
                            s.context_messages.c.snapshot_id == selected,
                            s.context_messages.c.agent_id == agent_id,
                        )
                    ).scalars()
                ),
            )
            if selected
            else []
        )
        parts = {r["part_id"]: r for r in self.image_parts(agent_id, source_ids) + incoming}
        omitted: list[Record] = []
        for part_id, item in parts.items():
            evidence = [r for r in rows if r["part_id"] == part_id]
            if not any(r["state"] in {"dispatched", "attached", "unknown"} for r in evidence):
                if evidence:
                    reason = (
                        "request_preparing"
                        if any(r["state"] == "prepared" for r in evidence)
                        else cast(str, run["error_code"]) or "known_not_sent"
                    )
                elif config["protocol"] == "disabled":
                    reason = "image_capability_disabled"
                elif item["message_id"] == run["input_message_id"]:
                    reason = cast(str, run["error_code"]) or "request_not_dispatched"
                else:
                    reason = "context_image_limit" if rows else "request_not_dispatched"
                omitted.append({"part_id": part_id, "message_id": item["message_id"], "reason": reason})
        return cast(
            Record,
            {
                "policy": config,
                "registered": incoming,
                "requests": [dict(r) for r in rows[:256]],
                "has_more": len(rows) > 256,
                "unattached": omitted,
                "used": used,
                "budget": config["run_budget"],
                "model_seen": "not_claimed",
            },
        )
