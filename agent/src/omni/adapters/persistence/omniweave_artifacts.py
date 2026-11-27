"""Versioned objects and explicit reader grants scoped to a Worker plan."""

from __future__ import annotations

from dataclasses import asdict
from typing import cast

from sqlalchemy import Select, func, select, update
from sqlalchemy.sql.elements import ColumnElement

from ...application.ports import Record, UnitOfWork
from ...domain.contracts import JSON, BusinessError, ToolSpec
from ...domain.objects import Blob
from ...domain.omniweave import goal_fingerprint, mandate_tool
from ...domain.omniweave_artifacts import (
    ARTIFACT_MEDIA_TYPE,
    ARTIFACTS_PER_ROOT,
    PUBLISH_ARTIFACT,
    READ_SHARED,
    SUBSCRIBE_ARTIFACTS,
    ArtifactDraft,
)
from . import schema as s
from .delegation_authority import authorize
from .omniweave_attempts import OmniWeaveAttemptRepositories, latest_goal_version, latest_run_id, task_for_run
from .omniweave_authority import mandate
from .repository_base import RepositoryBase

_artifacts = s.owa_artifacts
_grants = s.owa_artifact_grants
_links = s.owa_artifact_links


def _current_version() -> ColumnElement[bool]:
    later = _artifacts.alias()
    return (
        ~select(later.c.id)
        .where(
            later.c.producer_id == _artifacts.c.producer_id,
            later.c.goal_version == _artifacts.c.goal_version,
            later.c.name == _artifacts.c.name,
            later.c.version > _artifacts.c.version,
        )
        .exists()
    )


def artifact_notice_tasks() -> Select[str]:
    return _notices().with_only_columns(_links.c.reader_id.label("task_id"))


def _notices() -> Select[str, str, str, int]:
    return (
        select(_grants.c.id, _links.c.reader_id, _grants.c.artifact_id, _artifacts.c.version)
        .select_from(_grants)
        .join(_links, _links.c.id == _grants.c.link_id)
        .join(
            _artifacts,
            _artifacts.c.id == _grants.c.artifact_id,
        )
        .where(
            _grants.c.revoked_at.is_(None),
            _grants.c.ack_run_id.is_(None),
            _links.c.subscribed.is_(True),
            _links.c.subscription_revision == _grants.c.subscription_revision,
            _artifacts.c.goal_version == latest_goal_version(_links.c.producer_id),
            _grants.c.reader_version == latest_goal_version(_links.c.reader_id),
            _current_version(),
        )
    )


def has_artifact_notice(repo: RepositoryBase, agent_id: str, task_id: str) -> bool:
    return (
        repo.connection.execute(
            _notices()
            .where(
                _grants.c.agent_id == agent_id,
                _links.c.reader_id == task_id,
            )
            .limit(1)
        ).first()
        is not None
    )


def _artifact_view(artifact: Record, obj: Record, *, duplicate: bool = False) -> Record:
    return {
        "artifact_id": artifact["id"],
        "name": artifact["name"],
        "version": artifact["version"],
        "goal_version": artifact["goal_version"],
        "sha256": obj["sha256"],
        "bytes": obj["bytes"],
        "duplicate": duplicate,
    }


class OmniWeaveArtifactRepositories(OmniWeaveAttemptRepositories):
    def _owa_artifact_worker(self, agent_id: str, run_id: str, spec: ToolSpec) -> tuple[Record, Record]:
        unit = cast(UnitOfWork, self)
        unit.assert_run_authorized(agent_id, run_id)
        task = task_for_run(self, agent_id, run_id)
        grant = mandate(self, agent_id, run_id)
        if (
            task is None
            or grant is None
            or unit.get_run(agent_id, run_id)["state"] != "running"
            or cast(Record, grant["tools"]).get(spec.name) != mandate_tool(spec)
            or self.connection.execute(select(latest_run_id(str(task["id"])))).scalar_one() != run_id
        ):
            raise BusinessError(
                "owa_artifact_not_authorized", "An active Worker with this artifact capability is required"
            )
        return task, grant

    def _owa_publish_info(self, agent_id: str, run_id: str, draft: ArtifactDraft) -> tuple[Record, list[Record]]:
        task, captured = self._owa_artifact_worker(agent_id, run_id, PUBLISH_ARTIFACT)
        version = captured["goal_version"]
        fingerprint = goal_fingerprint(cast(JSON, asdict(draft)))
        prior = (
            self.connection.execute(
                select(_artifacts).where(
                    _artifacts.c.agent_id == agent_id,
                    _artifacts.c.producer_id == task["id"],
                    _artifacts.c.goal_version == version,
                    _artifacts.c.command_key == draft.key,
                )
            )
            .mappings()
            .first()
        )
        if prior:
            if prior["payload_sha256"] != fingerprint:
                raise BusinessError(
                    "idempotency_conflict", "Artifact key already identifies another publication", status=409
                )
            view = _artifact_view(
                cast(Record, dict(prior)), self._one(s.objects, agent_id, prior["object_id"]), duplicate=True
            )
            return {"receipt": view}, []
        latest = (
            self.connection.execute(
                select(func.max(_artifacts.c.version)).where(
                    _artifacts.c.producer_id == task["id"],
                    _artifacts.c.goal_version == version,
                    _artifacts.c.name == draft.name,
                )
            ).scalar_one()
            or 0
        )
        if latest != draft.expected_version:
            raise BusinessError(
                "revision_conflict", "Artifact version changed; read the current version before publishing", status=409
            )
        count = self.connection.execute(
            select(func.count())
            .select_from(_artifacts)
            .where(
                _artifacts.c.ticket_id == task["ticket_id"],
                _artifacts.c.goal_version == version,
            )
        ).scalar_one()
        if count >= ARTIFACTS_PER_ROOT:
            raise BusinessError(
                "owa_artifact_limit", "This version of the plan has reached its publication limit", status=409
            )
        recipients = [
            cast(Record, dict(row))
            for row in self.connection.execute(
                select(_links, latest_run_id(s.owa_tasks.c.id).label("reader_run"))
                .join(s.owa_tasks, s.owa_tasks.c.id == _links.c.reader_id)
                .where(
                    _links.c.agent_id == agent_id,
                    _links.c.producer_id == task["id"],
                    s.owa_tasks.c.task_key.in_(draft.readers),
                )
            ).mappings()
        ]
        if len(recipients) != len(draft.readers):
            raise BusinessError("not_found", "A reader is outside the authorized plan", status=404)
        for reader in recipients:
            reader_run = str(reader["reader_run"])
            reader_grant = mandate(self, agent_id, reader_run)
            if reader_grant is None or reader_grant["goal_version"] != version:
                raise BusinessError("owa_artifact_version_changed", "The reader goal version changed", status=409)
            authorize(self, agent_id, reader_run)
        return {
            "producer_id": task["id"],
            "ticket_id": task["ticket_id"],
            "goal_version": version,
            "payload_sha256": fingerprint,
            "version": latest + 1,
        }, recipients

    def omniweave_artifact_prepare(self, agent_id: str, run_id: str, draft: ArtifactDraft) -> Record:
        return self._owa_publish_info(agent_id, run_id, draft)[0]

    def omniweave_artifact_attach(
        self, agent_id: str, run_id: str, action_id: str, draft: ArtifactDraft, blob: Blob, expires_at: str
    ) -> Record:
        info, readers = self._owa_publish_info(agent_id, run_id, draft)
        if "receipt" in info:
            return cast(Record, info["receipt"])
        action = self._one(s.actions, agent_id, action_id)
        if (
            blob.agent_id != agent_id
            or action["run_id"] != run_id
            or action["tool"] != PUBLISH_ARTIFACT.name
            or action["state"] != "executing"
        ):
            raise BusinessError("owa_artifact_source_invalid", "Publication no longer has its source action")
        obj = cast(UnitOfWork, self).object_register_owa(blob, expires_at)
        artifact = self._record(
            _artifacts,
            {
                **info,
                "producer_run_id": run_id,
                "action_id": action_id,
                "name": draft.name,
                "command_key": draft.key,
                "object_id": obj["id"],
            },
            agent_id,
        )
        for reader in readers:
            self._record(
                _grants,
                {
                    "artifact_id": artifact["id"],
                    "link_id": reader["id"],
                    "reader_version": info["goal_version"],
                    "subscription_revision": reader["subscription_revision"] if reader["subscribed"] else None,
                },
                agent_id,
            )
        self.audit(
            agent_id,
            "omniweave.artifact_published",
            action_id,
            {"artifact_id": artifact["id"], "ticket_id": artifact["ticket_id"], "version": artifact["version"]},
        )
        return _artifact_view(artifact, obj)

    def omniweave_artifact_read(self, agent_id: str, run_id: str, artifact_id: str) -> Record:
        task, captured = self._owa_artifact_worker(agent_id, run_id, READ_SHARED)
        row = (
            self.connection.execute(
                select(_artifacts, _current_version().label("current_version"))
                .join(_grants, _grants.c.artifact_id == _artifacts.c.id)
                .join(_links, _links.c.id == _grants.c.link_id)
                .where(
                    _artifacts.c.id == artifact_id,
                    _artifacts.c.agent_id == agent_id,
                    _artifacts.c.ticket_id == task["ticket_id"],
                    _artifacts.c.goal_version == captured["goal_version"],
                    _links.c.reader_id == task["id"],
                    _grants.c.reader_version == captured["goal_version"],
                    _grants.c.revoked_at.is_(None),
                )
            )
            .mappings()
            .first()
        )
        if row is None:
            raise BusinessError("not_found", "Artifact is outside the authorized reader scope", status=404)
        if not row["current_version"]:
            raise BusinessError("owa_artifact_superseded", "This artifact version has been superseded", status=409)
        producer_run = self.connection.execute(select(latest_run_id(row["producer_id"]))).scalar_one()
        producer_grant = mandate(self, agent_id, producer_run)
        action = self._one(s.actions, agent_id, row["action_id"])
        if producer_grant is None or producer_grant["goal_version"] != row["goal_version"] or action["body_pruned_at"]:
            raise BusinessError("not_found", "Artifact source is no longer readable", status=404)
        authorize(self, agent_id, producer_run)
        obj = self._one(s.objects, agent_id, row["object_id"])
        if obj["media_type"] != ARTIFACT_MEDIA_TYPE:
            raise BusinessError("object_scope_conflict", "Artifact object scope is invalid", status=409)
        return {**_artifact_view(cast(Record, dict(row)), obj), "agent_id": agent_id}

    def omniweave_artifact_subscribe(self, agent_id: str, run_id: str, producer: str, enabled: bool) -> Record:
        task, _ = self._owa_artifact_worker(agent_id, run_id, SUBSCRIBE_ARTIFACTS)
        row = (
            self.connection.execute(
                select(_links)
                .join(s.owa_tasks, s.owa_tasks.c.id == _links.c.producer_id)
                .where(
                    _links.c.agent_id == agent_id,
                    _links.c.reader_id == task["id"],
                    s.owa_tasks.c.task_key == producer,
                )
            )
            .mappings()
            .first()
        )
        if row is None:
            raise BusinessError("not_found", "Producer is outside the authorized subscription scope", status=404)
        revision = row["subscription_revision"]
        if row["subscribed"] != enabled:
            revision += 1
            self.connection.execute(
                update(_links)
                .where(_links.c.id == row["id"])
                .values(subscribed=enabled, subscription_revision=revision)
            )
            self.audit(
                agent_id,
                "omniweave.artifact_subscription",
                run_id,
                {"link_id": row["id"], "revision": revision, "enabled": enabled},
            )
        return {"producer": producer, "enabled": enabled, "revision": revision}

    def omniweave_artifact_revoke(self, agent_id: str, session_id: str, artifact_id: str, reader: str) -> None:
        row = (
            self.connection.execute(
                select(_grants)
                .join(_artifacts, _artifacts.c.id == _grants.c.artifact_id)
                .join(s.owa_tickets, s.owa_tickets.c.id == _artifacts.c.ticket_id)
                .join(s.owa_threads, s.owa_threads.c.id == s.owa_tickets.c.thread_id)
                .join(s.sessions, s.sessions.c.id == s.owa_threads.c.main_session_id)
                .join(s.instances, (s.instances.c.slot == 1) & (s.instances.c.owner_id == s.owa_threads.c.owner_id))
                .join(_links, _links.c.id == _grants.c.link_id)
                .join(s.owa_tasks, s.owa_tasks.c.id == _links.c.reader_id)
                .where(
                    _grants.c.agent_id == agent_id,
                    _artifacts.c.id == artifact_id,
                    s.owa_threads.c.main_session_id == session_id,
                    s.sessions.c.deleted_at.is_(None),
                    s.owa_tasks.c.task_key == reader,
                )
            )
            .mappings()
            .first()
        )
        if row is None:
            raise BusinessError("not_found", "Artifact grant is outside this main conversation", status=404)
        if row["revoked_at"] is None:
            self.connection.execute(
                update(_grants).where(_grants.c.id == row["id"]).values(revoked_at=self.clock.now())
            )
            self.audit(
                agent_id, "omniweave.artifact_share_revoked", None, {"artifact_id": artifact_id, "grant_id": row["id"]}
            )

    def _owa_public_artifacts(self, agent_id: str, ticket_id: str) -> list[JSON]:
        published = (
            self.connection.execute(
                select(_artifacts, latest_run_id(_artifacts.c.producer_id).label("current_run"))
                .where(_artifacts.c.agent_id == agent_id, _artifacts.c.ticket_id == ticket_id, _current_version())
                .order_by(_artifacts.c.created_at, _artifacts.c.id)
            )
            .mappings()
            .all()
        )
        result: list[JSON] = []
        for row in published:
            run_id = row["current_run"]
            captured = mandate(self, agent_id, run_id)
            action = self._one(s.actions, agent_id, row["action_id"])
            if captured is None or captured["goal_version"] != row["goal_version"] or action["body_pruned_at"]:
                continue
            try:
                authorize(self, agent_id, run_id)
            except BusinessError:
                continue
            readers = list(
                self.connection.execute(
                    select(s.owa_tasks.c.task_key)
                    .select_from(_grants)
                    .join(_links, _links.c.id == _grants.c.link_id)
                    .join(s.owa_tasks, s.owa_tasks.c.id == _links.c.reader_id)
                    .where(_grants.c.artifact_id == row["id"], _grants.c.revoked_at.is_(None))
                    .order_by(s.owa_tasks.c.task_key)
                ).scalars()
            )
            obj = self._one(s.objects, agent_id, row["object_id"])
            result.append({**_artifact_view(cast(Record, dict(row)), obj), "readers": readers})
        return result

    def _owa_artifact_notices(self, agent_id: str, task_id: str, version: int) -> list[tuple[str, Record]]:
        return [
            (row.id, {"type": "artifact.published", "artifact_id": row.artifact_id, "version": row.version})
            for row in self.connection.execute(
                _notices()
                .where(
                    _grants.c.agent_id == agent_id,
                    _links.c.reader_id == task_id,
                    _grants.c.reader_version == version,
                )
                .order_by(_artifacts.c.created_at, _artifacts.c.id)
            )
        ]

    def _owa_ack_artifact(self, grant_id: str, run_id: str) -> None:
        self.connection.execute(
            update(_grants).where(_grants.c.id == grant_id, _grants.c.ack_run_id.is_(None)).values(ack_run_id=run_id)
        )
