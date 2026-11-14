"""Immutable skill versions, review state and per-run bindings; no package or filesystem access."""

from __future__ import annotations

from typing import TYPE_CHECKING, cast

from sqlalchemy import and_, func, select, update

from ...application.ports import Record
from ...domain.contracts import BusinessError
from ...domain.skills import MAX_ACTIVE, PLATFORMS, file_path, invalid, manifest
from . import schema as s
from .repository_base import RepositoryBase

if TYPE_CHECKING:
    from .repository import Repositories


class SkillRepositories(RepositoryBase):
    def skill_available(self, agent_id: str, platform: str) -> list[Record]:
        self._skill_agent(agent_id)
        if platform not in PLATFORMS:
            raise invalid()
        return [
            cast(Record, dict(r))
            for r in self.connection.execute(
                select(s.skills.c.id, s.skills.c.name, s.skills.c.current_revision_id, s.skill_manifests.c.description)
                .join(s.skill_manifests, s.skill_manifests.c.revision_id == s.skills.c.current_revision_id)
                .where(
                    s.skills.c.agent_id == agent_id,
                    s.skills.c.state == "enabled",
                    s.skill_manifests.c[platform].is_(True),
                )
                .order_by(s.skills.c.name)
                .limit(32)
            ).mappings()
        ]

    def _skill_agent(self, agent_id: str) -> None:
        if not self.connection.execute(select(s.agents.c.id).where(s.agents.c.id == agent_id)).first():
            raise BusinessError("not_found", "Agent does not exist", status=404)

    def skill_get(self, agent_id: str, skill_id: str, revision_id: str | None = None) -> Record:
        head = self._one(s.skills, agent_id, skill_id)
        revision = self._one(s.skill_revisions, agent_id, revision_id or cast(str, head["current_revision_id"]))
        detail = (
            self.connection.execute(
                select(s.skill_manifests).where(
                    s.skill_manifests.c.revision_id == revision["id"],
                    s.skill_manifests.c.skill_id == skill_id,
                    s.skill_manifests.c.agent_id == agent_id,
                )
            )
            .mappings()
            .first()
        )
        if not detail:
            raise BusinessError("not_found", "Revision does not belong to this skill", status=404)
        files = [
            cast(Record, dict(r))
            for r in self.connection.execute(
                select(s.skill_files)
                .where(s.skill_files.c.revision_id == revision["id"], s.skill_files.c.agent_id == agent_id)
                .order_by(s.skill_files.c.path)
                .limit(18)
            ).mappings()
        ]
        return cast(
            Record,
            {
                **head,
                "revision_id": revision["id"],
                "content_revision": revision["revision"],
                **{k: detail[k] for k in ("description", "manifest_sha256", *PLATFORMS)},
                "files": files,
            },
        )

    def skill_page(self, agent_id: str, after: str = "", limit: int = 50) -> list[Record]:
        self._skill_agent(agent_id)
        if (
            not isinstance(cast(object, after), str)
            or len(after) > 36
            or type(limit) is not int
            or not 1 <= limit <= 50
        ):
            raise invalid()
        return [
            cast(Record, dict(r))
            for r in self.connection.execute(
                select(
                    s.skills,
                    s.skill_manifests.c.description,
                    s.skill_manifests.c.manifest_sha256,
                    *[s.skill_manifests.c[p] for p in PLATFORMS],
                )
                .join(
                    s.skill_manifests,
                    and_(
                        s.skill_manifests.c.revision_id == s.skills.c.current_revision_id,
                        s.skill_manifests.c.agent_id == s.skills.c.agent_id,
                    ),
                )
                .where(s.skills.c.agent_id == agent_id, s.skills.c.id > after)
                .order_by(s.skills.c.id)
                .limit(limit)
            ).mappings()
        ]

    def skill_history(self, agent_id: str, skill_id: str, after: int = 0, limit: int = 50) -> list[Record]:
        self._one(s.skills, agent_id, skill_id)
        if type(after) is not int or after < 0 or type(limit) is not int or not 1 <= limit <= 50:
            raise invalid()
        return [
            cast(Record, dict(r))
            for r in self.connection.execute(
                select(
                    s.skill_revisions.c.id,
                    s.skill_revisions.c.revision,
                    s.skill_revisions.c.created_at,
                    s.skill_manifests.c.manifest_sha256,
                )
                .join(s.skill_manifests, s.skill_manifests.c.revision_id == s.skill_revisions.c.id)
                .where(
                    s.skill_manifests.c.skill_id == skill_id,
                    s.skill_manifests.c.agent_id == agent_id,
                    s.skill_revisions.c.revision > after,
                )
                .order_by(s.skill_revisions.c.revision)
                .limit(limit)
            ).mappings()
        ]

    def skill_install(
        self, agent_id: str, metadata: Record, files: list[Record], skill_id: str | None = None, revision: int = 0
    ) -> Record:
        self._skill_agent(agent_id)
        if type(revision) is not int or revision < 0:
            raise invalid()
        old = self.skill_get(agent_id, skill_id) if skill_id else None
        if old and (old["revision"] != revision or old["state"] == "archived"):
            raise BusinessError("revision_conflict", "Skill changed or is archived; retain the draft and reload")
        if (not old and revision != 0) or (old and old["name"] != metadata["name"]):
            raise invalid()
        if (
            not 1 <= len(files) <= 17
            or sum(cast(int, r["bytes"]) for r in files) > 512 * 1024
            or len({cast(str, r["path"]).casefold() for r in files}) != len(files)
        ):
            raise invalid()
        for row in files:
            file_path(cast(str, row["path"]))
            obj = self._one(s.objects, agent_id, cast(str, row["object_id"]))
            if obj["sha256"] != row["sha256"] or obj["bytes"] != row["bytes"]:
                raise invalid("skill_object_changed")
        main = next((r for r in files if r["path"] == "SKILL.md"), None)
        if not main:
            raise invalid()
        if old and cast(int, old["content_revision"]) >= 128:
            raise BusinessError("skill_capacity", "Skill revision limit reached", status=422)
        if (
            not old
            and self.connection.execute(
                select(func.count()).select_from(s.skills).where(s.skills.c.agent_id == agent_id)
            ).scalar_one()
            >= 256
        ):
            raise BusinessError("skill_capacity", "Agent skill capacity reached", status=422)
        version_id = self.ids.new()
        if not old:
            if self.connection.execute(
                select(s.skill_revisions.c.id).where(
                    s.skill_revisions.c.agent_id == agent_id, s.skill_revisions.c.name == metadata["name"]
                )
            ).first():
                raise BusinessError("skill_name_exists", "Skill name already identifies retained content")
            head = self._record(
                s.skills,
                {
                    "name": metadata["name"],
                    "current_revision_id": version_id,
                    "revision": 1,
                    "authority_epoch": 1,
                    "state": "disabled",
                },
                agent_id,
            )
            skill_id = cast(str, head["id"])
        self._record(
            s.skill_revisions,
            {
                "id": version_id,
                "name": metadata["name"],
                "revision": cast(int, old["content_revision"]) + 1 if old else 1,
                "object_id": main["object_id"],
            },
            agent_id,
        )
        self.connection.execute(
            s.skill_manifests.insert().values(
                revision_id=version_id,
                skill_id=skill_id,
                agent_id=agent_id,
                description=metadata["description"],
                manifest_sha256=manifest(cast(list[dict[str, object]], files)),
                **{p: metadata[p] for p in PLATFORMS},
            )
        )
        for row in files:
            self._record(
                s.skill_files,
                {"revision_id": version_id, **{k: row[k] for k in ("path", "object_id", "sha256", "bytes")}},
                agent_id,
            )
        if old:
            self.connection.execute(
                update(s.skills)
                .where(s.skills.c.id == skill_id, s.skills.c.agent_id == agent_id)
                .values(
                    current_revision_id=version_id,
                    state="disabled",
                    revision=revision + 1,
                    authority_epoch=cast(int, old["authority_epoch"]) + 1,
                )
            )
        self.audit(
            agent_id,
            "skill.version_saved",
            skill_id,
            {
                "revision_id": version_id,
                "manifest_sha256": manifest(cast(list[dict[str, object]], files)),
                "state": "disabled",
                "file_count": len(files),
            },
        )
        return self.skill_get(agent_id, cast(str, skill_id))

    def skill_state(
        self, agent_id: str, skill_id: str, revision: int, state: str, revision_id: str, sha256: str
    ) -> Record:
        row = self.skill_get(agent_id, skill_id)
        if row["revision"] != revision or row["revision_id"] != revision_id or row["manifest_sha256"] != sha256:
            raise BusinessError("revision_conflict", "Skill changed; review its exact revision")
        if state not in {"disabled", "enabled", "archived"} or (row["state"] == "archived" and state != "disabled"):
            raise invalid()
        if row["state"] == state:
            return row
        if (
            state == "enabled"
            and self.connection.execute(
                select(func.count())
                .select_from(s.skills)
                .where(s.skills.c.agent_id == agent_id, s.skills.c.state == "enabled")
            ).scalar_one()
            >= 32
        ):
            raise BusinessError("skill_capacity", "At most 32 skills may be enabled", status=422)
        audit_id = self.audit(
            agent_id,
            "skill." + state,
            skill_id,
            {
                "revision_id": revision_id,
                "manifest_sha256": sha256,
                "authority_epoch": cast(int, row["authority_epoch"]) + 1,
            },
        )
        self.connection.execute(
            update(s.skills)
            .where(s.skills.c.id == skill_id, s.skills.c.agent_id == agent_id)
            .values(
                state=state,
                revision=revision + 1,
                authority_epoch=cast(int, row["authority_epoch"]) + 1,
                review_audit_id=audit_id if state == "enabled" else row["review_audit_id"],
            )
        )
        return self.skill_get(agent_id, skill_id)

    def skill_capture_run(self, agent_id: str, run_id: str, selected: tuple[str, ...] = (), platform: str = "") -> None:
        run = self._one(s.runs, agent_id, run_id)
        if run["skill_capture_epoch"] is not None:
            raise invalid("skill_catalog_sealed")
        enabled = [
            cast(Record, dict(r))
            for r in self.connection.execute(
                select(s.skills)
                .where(s.skills.c.agent_id == agent_id, s.skills.c.state == "enabled")
                .order_by(s.skills.c.id)
                .limit(33)
            ).mappings()
        ]
        if len(enabled) > 32:
            raise invalid("skill_capacity")
        for position, row in enumerate(enabled):
            self._record(
                s.run_skill_catalog,
                {
                    "run_id": run_id,
                    "skill_id": row["id"],
                    "revision_id": row["current_revision_id"],
                    "authority_epoch": row["authority_epoch"],
                    "review_audit_id": row["review_audit_id"],
                    "position": position,
                },
                agent_id,
            )
        epoch = self.connection.execute(select(s.history_identity.c.epoch)).scalar_one()
        self.connection.execute(update(s.runs).where(s.runs.c.id == run_id).values(skill_capture_epoch=epoch))
        for skill_id in selected:
            self.skill_activate_run(agent_id, run_id, skill_id, platform)

    def skill_authorize_run(self, agent_id: str, run_id: str) -> None:
        rows = (
            self.connection.execute(
                select(
                    s.run_skill_catalog.c.authority_epoch,
                    s.skills.c.authority_epoch.label("current_epoch"),
                    s.skills.c.state,
                    s.runs.c.skill_capture_epoch,
                )
                .join(s.run_skill_activations, s.run_skill_activations.c.catalog_id == s.run_skill_catalog.c.id)
                .join(s.skills, s.skills.c.id == s.run_skill_catalog.c.skill_id)
                .join(s.runs, s.runs.c.id == s.run_skill_catalog.c.run_id)
                .where(s.run_skill_catalog.c.agent_id == agent_id, s.run_skill_catalog.c.run_id == run_id)
            )
            .mappings()
            .all()
        )
        epoch = self.connection.execute(select(s.history_identity.c.epoch)).scalar_one()
        if any(
            r["state"] != "enabled" or r["authority_epoch"] != r["current_epoch"] or r["skill_capture_epoch"] != epoch
            for r in rows
        ):
            raise BusinessError("skill_authority_revoked", "An active skill changed or was revoked; use a new run")

    def skill_run_report(self, agent_id: str, run_id: str, platform: str) -> Record:
        run = self._one(s.runs, agent_id, run_id)
        if platform not in PLATFORMS:
            raise invalid()
        epoch = self.connection.execute(select(s.history_identity.c.epoch)).scalar_one()
        rows = self.connection.execute(
            select(
                s.run_skill_catalog,
                s.skills.c.name,
                s.skills.c.state,
                s.skills.c.authority_epoch.label("current_epoch"),
                s.skill_manifests.c[platform].label("platform_supported"),
                s.run_skill_activations.c.id.label("activation_id"),
            )
            .join(s.skills, s.skills.c.id == s.run_skill_catalog.c.skill_id)
            .join(s.skill_manifests, s.skill_manifests.c.revision_id == s.run_skill_catalog.c.revision_id)
            .outerjoin(s.run_skill_activations, s.run_skill_activations.c.catalog_id == s.run_skill_catalog.c.id)
            .where(s.run_skill_catalog.c.agent_id == agent_id, s.run_skill_catalog.c.run_id == run_id)
            .order_by(s.run_skill_catalog.c.position)
            .limit(32)
        ).mappings()
        return cast(
            Record,
            {
                "captured": run["skill_capture_epoch"] is not None,
                "platform": platform,
                "items": [
                    {
                        **dict(r),
                        "active": r["activation_id"] is not None,
                        "available": r["state"] == "enabled"
                        and r["authority_epoch"] == r["current_epoch"]
                        and run["skill_capture_epoch"] == epoch
                        and r["platform_supported"],
                    }
                    for r in rows
                ],
            },
        )

    def skill_catalog(self, agent_id: str, run_id: str, platform: str, *, active: bool = False) -> list[Record]:
        self._one(s.runs, agent_id, run_id)
        self.skill_authorize_run(agent_id, run_id)
        if platform not in PLATFORMS:
            raise invalid()
        statement = select(
            s.run_skill_catalog,
            s.skills.c.name,
            s.skills.c.revision,
            s.skill_revisions.c.revision.label("content_revision"),
            s.skill_manifests.c.description,
            s.skill_manifests.c.manifest_sha256,
        )
        statement = (
            statement.join(s.skills, s.skills.c.id == s.run_skill_catalog.c.skill_id)
            .join(s.skill_manifests, s.skill_manifests.c.revision_id == s.run_skill_catalog.c.revision_id)
            .join(s.skill_revisions, s.skill_revisions.c.id == s.run_skill_catalog.c.revision_id)
        )
        if active:
            statement = statement.join(
                s.run_skill_activations, s.run_skill_activations.c.catalog_id == s.run_skill_catalog.c.id
            )
        return [
            cast(Record, dict(r))
            for r in self.connection.execute(
                statement.where(
                    s.run_skill_catalog.c.agent_id == agent_id,
                    s.run_skill_catalog.c.run_id == run_id,
                    s.skills.c.state == "enabled",
                    s.skills.c.authority_epoch == s.run_skill_catalog.c.authority_epoch,
                    s.skill_manifests.c[platform].is_(True),
                )
                .order_by(s.run_skill_activations.c.position if active else s.run_skill_catalog.c.position)
                .limit(32)
            ).mappings()
        ]

    def skill_activate_run(
        self, agent_id: str, run_id: str, skill_id: str, platform: str, action_id: str | None = None
    ) -> Record:
        if action_id:
            unit = cast("Repositories", self)
            unit.assert_run_authorized(agent_id, run_id)
            action = unit.action(agent_id, action_id)
            current = unit.settings(agent_id)
            if (
                action["run_id"] != run_id
                or action["tool"] not in {"skill_activate", "skill_view"}
                or action["state"] != "executing"
                or current["policy_revision"] != action["policy_revision"]
                or action["tool"] in cast(list[object], cast(Record, current["value"])["disabled_tools"])
            ):
                raise BusinessError("policy_changed", "Skill read authority changed before publication")
        catalog = next((r for r in self.skill_catalog(agent_id, run_id, platform) if r["skill_id"] == skill_id), None)
        if not catalog:
            raise BusinessError("skill_not_captured", "Skill is unavailable in this run or platform", status=403)
        old = (
            self.connection.execute(
                select(s.run_skill_activations).where(
                    s.run_skill_activations.c.agent_id == agent_id,
                    s.run_skill_activations.c.run_id == run_id,
                    s.run_skill_activations.c.skill_id == skill_id,
                )
            )
            .mappings()
            .first()
        )
        if not old:
            count = self.connection.execute(
                select(func.count())
                .select_from(s.run_skill_activations)
                .where(s.run_skill_activations.c.run_id == run_id)
            ).scalar_one()
            if count >= MAX_ACTIVE:
                raise invalid("skill_activation_limit")
            self._record(
                s.run_skill_activations,
                {
                    "catalog_id": catalog["id"],
                    "run_id": run_id,
                    "skill_id": skill_id,
                    "position": count,
                    "platform": platform,
                    "source": "model" if action_id else "owner",
                    "action_id": action_id,
                },
                agent_id,
            )
            self.audit(
                agent_id,
                "skill.activated",
                run_id,
                {
                    "skill_id": skill_id,
                    "revision_id": catalog["revision_id"],
                    "source": "model" if action_id else "owner",
                },
            )
        return self.skill_get(agent_id, skill_id, cast(str, catalog["revision_id"]))
