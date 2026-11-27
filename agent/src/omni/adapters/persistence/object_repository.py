"""Object metadata, pins and real foreign-key references; never touches files."""

from __future__ import annotations

from typing import cast

from sqlalchemy import delete, func, select, update

from ...application.ports import Record
from ...domain.contracts import JSON, BusinessError
from ...domain.desktop import CapturedFrame
from ...domain.objects import Blob, media_type
from ...domain.omniweave_artifacts import ARTIFACT_MEDIA_TYPE
from . import schema as s
from .repository_base import RepositoryBase


class ObjectRepositories(RepositoryBase):
    def capture_session(self, agent_id: str, session_id: str) -> int:
        row = self._one(s.sessions, agent_id, session_id)
        if row["deleted_at"]:
            raise BusinessError("session_deleted", "The capture conversation was deleted", status=409)
        return cast(int, row["content_epoch"])

    def capture_attach(
        self, blob: Blob, session_id: str, epoch: int, operation_id: str, frame: CapturedFrame, expires: str
    ) -> Record:
        if self.capture_session(blob.agent_id, session_id) != epoch:
            raise BusinessError("context_invalidated", "Conversation content changed during capture", status=409)
        obj = self.object_register(blob, "image/png", expires)
        row = self._record(
            s.session_captures,
            {
                "session_id": session_id,
                "object_id": obj["id"],
                "operation_id": operation_id,
                "content_epoch": epoch,
                "width": frame.width,
                "height": frame.height,
                "method": frame.method,
            },
            blob.agent_id,
        )
        self.audit(
            blob.agent_id,
            "android.capture_published" if frame.method == "adb_screencap" else "desktop.capture_published",
            operation_id,
            {
                "capture_id": row["id"],
                "session_id": session_id,
                "object_id": obj["id"],
                "width": frame.width,
                "height": frame.height,
                "bytes": blob.size,
            },
        )
        return {**row, "sha256": blob.sha256, "bytes": blob.size, "media_type": "image/png"}

    def capture_page(self, agent_id: str, session_id: str, after: str, limit: int) -> list[Record]:
        self.capture_session(agent_id, session_id)
        if not 1 <= limit <= 100:
            raise BusinessError("invalid_page", "Capture pages contain at most 100 entries", status=422)
        return [
            cast(Record, dict(row))
            for row in self.connection.execute(
                select(s.session_captures)
                .where(
                    s.session_captures.c.agent_id == agent_id,
                    s.session_captures.c.session_id == session_id,
                    s.session_captures.c.id > after,
                )
                .order_by(s.session_captures.c.id)
                .limit(limit)
            ).mappings()
        ]

    def capture_remove(self, agent_id: str, session_id: str, capture_id: str) -> None:
        row = self._one(s.session_captures, agent_id, capture_id)
        if row["session_id"] != session_id:
            raise BusinessError("not_found", "Capture does not belong to this conversation", status=404)
        self.connection.execute(delete(s.session_captures).where(s.session_captures.c.id == capture_id))
        self.audit(
            agent_id,
            "desktop.capture_released",
            cast(str | None, row["operation_id"]),
            {"capture_id": capture_id, "object_id": row["object_id"]},
        )

    def object_register(self, blob: Blob, mime: str, expires_at: str, pinned: bool = False) -> Record:
        media_type(mime)
        if mime == ARTIFACT_MEDIA_TYPE:
            raise BusinessError(
                "invalid_media_type", "This media type belongs to scoped OmniWeave artifacts", status=422
            )
        if type(pinned) is not bool:
            raise BusinessError("invalid_pin", "Pin must be boolean", status=422)
        return self._register_object(blob, mime, expires_at, pinned)

    def _register_object(self, blob: Blob, mime: str, expires_at: str, pinned: bool) -> Record:
        prior = (
            self.connection.execute(
                select(s.objects).where(s.objects.c.agent_id == blob.agent_id, s.objects.c.sha256 == blob.sha256)
            )
            .mappings()
            .first()
        )
        if prior:
            if (mime == ARTIFACT_MEDIA_TYPE) != (prior["media_type"] == ARTIFACT_MEDIA_TYPE):
                raise BusinessError("object_scope_conflict", "Object content belongs to a different scope", status=409)
            if prior["bytes"] != blob.size:
                raise BusinessError(
                    "object_integrity_failed", "Stored object size conflicts with its digest", status=503
                )
            return (
                self.object_pin(blob.agent_id, prior["id"], True, prior["revision"])
                if pinned and not prior["pinned"]
                else cast(Record, dict(prior))
            )
        row = self._record(
            s.objects,
            {
                "sha256": blob.sha256,
                "bytes": blob.size,
                "media_type": mime,
                "expires_at": expires_at,
                "pinned": pinned,
                "revision": 1,
            },
            blob.agent_id,
        )
        if mime != ARTIFACT_MEDIA_TYPE:
            self.audit(
                blob.agent_id, "object.registered", cast(str, row["id"]), {"sha256": blob.sha256, "bytes": blob.size}
            )
        return row

    def object_register_owa(self, blob: Blob, expires_at: str) -> Record:
        return self._register_object(blob, ARTIFACT_MEDIA_TYPE, expires_at, False)

    def object_get(self, agent_id: str, object_id: str) -> Record:
        row = self._one(s.objects, agent_id, object_id)
        if row["media_type"] == ARTIFACT_MEDIA_TYPE:
            raise BusinessError("not_found", "Object does not exist in this scope", status=404)
        return self._object_references(agent_id, object_id, row)

    def _object_references(self, agent_id: str, object_id: str, row: Record) -> Record:
        row["references"] = sum(
            self.connection.execute(
                select(func.count()).select_from(t).where(t.c.agent_id == agent_id, column == object_id)
            ).scalar_one()
            for t, column in s.OBJECT_REFERENCES
            if t is not s.owa_artifacts or row["media_type"] == ARTIFACT_MEDIA_TYPE
        )
        return row

    def object_pin(self, agent_id: str, object_id: str, pinned: bool, revision: int) -> Record:
        self.object_get(agent_id, object_id)
        if type(pinned) is not bool or type(revision) is not int or revision < 1:
            raise BusinessError("invalid_revision", "A pin value and revision are required", status=422)
        changed = self.connection.execute(
            update(s.objects)
            .where(s.objects.c.agent_id == agent_id, s.objects.c.id == object_id, s.objects.c.revision == revision)
            .values(pinned=pinned, revision=revision + 1)
        ).rowcount
        if changed != 1:
            raise BusinessError("revision_conflict", "Object changed; reload before editing")
        self.audit(agent_id, "object.pin_changed", object_id, {"pinned": pinned, "revision": revision + 1})
        return self.object_get(agent_id, object_id)

    def object_remove(self, agent_id: str, object_id: str, revision: int) -> None:
        row = self._object_references(agent_id, object_id, self._one(s.objects, agent_id, object_id))
        expires_at = cast(str | None, row["expires_at"])
        if row["pinned"] or row["references"] or not expires_at or expires_at > self.clock.now():
            raise BusinessError("object_protected", "A pinned, referenced or unexpired object cannot be collected")
        changed = self.connection.execute(
            delete(s.objects).where(
                s.objects.c.agent_id == agent_id, s.objects.c.id == object_id, s.objects.c.revision == revision
            )
        ).rowcount
        if changed != 1:
            raise BusinessError("revision_conflict", "Object changed after the collection plan")
        self.audit(agent_id, "object.collected", object_id, {"sha256": row["sha256"]})

    def object_page(self, agent_id: str, after: str, limit: int) -> list[Record]:
        if not 1 <= limit <= 200:
            raise BusinessError("invalid_page", "Object pages contain at most 200 entries", status=422)
        if not self.connection.execute(select(s.agents.c.id).where(s.agents.c.id == agent_id)).first():
            raise BusinessError("not_found", "Agent does not exist", status=404)
        statement = (
            select(s.objects)
            .where(
                s.objects.c.agent_id == agent_id, s.objects.c.id > after, s.objects.c.media_type != ARTIFACT_MEDIA_TYPE
            )
            .order_by(s.objects.c.id)
            .limit(limit)
        )
        return [cast(Record, dict(row)) for row in self.connection.execute(statement).mappings()]

    def object_link(self, agent_id: str, object_id: str, kind: str, owner_id: str) -> str:
        self.object_get(agent_id, object_id)
        if kind == "action":
            action = self._one(s.actions, agent_id, owner_id)
            if action["body_pruned_at"]:
                raise BusinessError("action_body_pruned", "Pruned actions cannot acquire new artifacts")
            table, column = s.action_objects, "action_id"
        elif kind == "message":
            message = self._one(s.messages, agent_id, owner_id)
            if message["deleted_at"]:
                raise BusinessError("message_deleted", "Cannot attach an object to a deleted message")
            table, column = s.message_parts, "message_id"
        else:
            raise BusinessError("invalid_object_owner", "Object owners must be a message or action", status=422)
        existing = self.connection.execute(
            select(table.c.id).where(
                table.c.agent_id == agent_id, table.c.object_id == object_id, table.c[column] == owner_id
            )
        ).scalar()
        if existing:
            return existing
        values: dict[str, JSON] = {column: owner_id, "object_id": object_id}
        if kind == "message":
            position = (
                self.connection.execute(
                    select(func.coalesce(func.max(s.message_parts.c.position), -1)).where(
                        s.message_parts.c.message_id == owner_id
                    )
                ).scalar_one()
                + 1
            )
            values.update(position=position, kind="object")
        row = self._record(table, values, agent_id)
        self.audit(agent_id, "object.linked", object_id, {"kind": kind, "owner_id": owner_id})
        return cast(str, row["id"])
