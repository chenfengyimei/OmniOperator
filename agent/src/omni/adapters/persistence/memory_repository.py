"""One memory truth for facts and personal documents, evidence, revisions and erasure."""

from __future__ import annotations

import re

from sqlalchemy import column, delete, literal_column, select, table, text, update

from ...domain.contracts import BusinessError
from ...domain.people import DOCUMENT_KINDS, content_fingerprint, source_fingerprint
from . import schema as s
from .document_repository import DocumentRepositories
from .memory_organization_predicates import memory_is_organized


class MemoryRepositories(DocumentRepositories):
    def _live_person(self, agent_id: str, person_id: str) -> dict:
        person = self._one(s.people, agent_id, person_id)
        if person["deleted_at"]:
            raise BusinessError("person_deleted", "Person was erased", status=410)
        return person

    def _touch_person(self, person_id: str | None) -> None:
        if person_id:
            self.connection.execute(
                update(s.people).where(s.people.c.id == person_id).values(revision=s.people.c.revision + 1)
            )

    def _evidence(
        self,
        agent_id: str,
        memory_id: str,
        revision_id: str,
        *,
        message_id: str | None = None,
        quote: str = "",
        object_id: str | None = None,
        model_authored: bool = False,
        action_id: str | None = None,
        document_revision_id: str | None = None,
        source_run_id: str | None = None,
    ) -> None:
        start = 0
        if sum(bool(v) for v in (message_id, object_id, action_id, document_revision_id)) > 1:
            raise BusinessError("invalid_evidence", "Evidence must identify one source", status=422)
        if message_id:
            source = self._one(s.messages, agent_id, message_id)
            if source["deleted_at"] or not quote or quote not in source["content"]:
                raise BusinessError("invalid_evidence", "Evidence must quote a live source message", status=422)
            session = self._one(s.sessions, agent_id, source["session_id"])
            if model_authored and source["seq"] <= session["context_floor_seq"]:
                raise BusinessError("memory_forgotten", "This source was excluded after an explicit erasure")
            kind, identity, column = "message", message_id, "message_id"
        elif document_revision_id:
            content = self.document_text(agent_id, document_revision_id)
            if not quote or quote not in content:
                raise BusinessError(
                    "invalid_evidence", "Document evidence must quote an exact live source revision", status=422
                )
            if model_authored:
                if not source_run_id:
                    raise BusinessError("evidence_required", "Read the source range before citing it", status=422)
                ranges = self.connection.execute(
                    select(s.context_documents.c.source_start, s.context_documents.c.source_end)
                    .join(s.context_snapshots, s.context_snapshots.c.id == s.context_documents.c.snapshot_id)
                    .where(
                        s.context_documents.c.agent_id == agent_id,
                        s.context_documents.c.document_revision_id == document_revision_id,
                        s.context_snapshots.c.run_id == source_run_id,
                    )
                ).all()
                offsets = [
                    left + content[left:right].index(quote) for left, right in ranges if quote in content[left:right]
                ]
                if not offsets:
                    raise BusinessError(
                        "evidence_required", "Quote must be inside a source range observed in this run", status=422
                    )
                start = min(offsets)
            else:
                start = content.index(quote)
            kind, identity, column = "document", document_revision_id, "document_revision_id"
        elif action_id:
            action = self._one(s.actions, agent_id, action_id)
            source_run = self._one(s.runs, agent_id, action["run_id"])
            session = self._one(s.sessions, agent_id, source_run["session_id"])
            incoming = self._one(s.messages, agent_id, source_run["input_message_id"])
            result = action["result"] or {}
            if (
                action["state"] != "ok"
                or not quote
                or quote not in str(result.get("text", ""))
                or result.get("data", {}).get("erased")
            ):
                raise BusinessError("invalid_evidence", "Tool evidence requires an exact successful result", status=422)
            if model_authored and (
                action["run_id"] != source_run_id or incoming["seq"] <= session["context_floor_seq"]
            ):
                raise BusinessError(
                    "evidence_required", "Tool evidence must be observed in the current live run", status=422
                )
            kind, identity, column = "action", action_id, "action_id"
        elif object_id and not model_authored:
            self._one(s.objects, agent_id, object_id)
            kind, identity, column = "object", object_id, "object_id"
        elif model_authored:
            raise BusinessError("evidence_required", "Model-authored memory requires real source evidence", status=422)
        else:
            kind, identity, column = "manual", self.audit(agent_id, "memory.authored", memory_id, {}), "manual_audit_id"
            quote = ""
        digest = source_fingerprint(kind, identity, quote)
        if (
            model_authored
            and self.connection.execute(
                select(s.tombstone_sources.c.id).where(
                    s.tombstone_sources.c.agent_id == agent_id, s.tombstone_sources.c.source_hash == digest
                )
            ).first()
        ):
            raise BusinessError("memory_forgotten", "This source was explicitly forgotten")
        self._record(
            s.source_anchors,
            {
                "memory_id": memory_id,
                "memory_revision_id": revision_id,
                "source_hash": digest,
                "quote": quote,
                column: identity,
                "source_start": start if document_revision_id else None,
                "source_end": start + len(quote) if document_revision_id else None,
            },
            agent_id,
        )

    def remember(
        self,
        agent_id: str,
        content: str,
        *,
        kind: str = "fact",
        message_id: str | None = None,
        quote: str = "",
        person_id: str | None = None,
        model_authored: bool = False,
        object_id: str | None = None,
        action_id: str | None = None,
        document_revision_id: str | None = None,
        source_run_id: str | None = None,
    ) -> dict:
        if (
            not content.strip()
            or len(content) > 20_000
            or kind not in {"fact", "profile", "resident", "journal", "note"}
        ):
            raise BusinessError("invalid_memory", "Memory must contain bounded text and a known kind", status=422)
        if person_id:
            self._live_person(agent_id, person_id)
        if kind in DOCUMENT_KINDS and not person_id:
            raise BusinessError("person_required", "Personal documents require a person", status=422)
        digest = content_fingerprint(content)
        if model_authored and (
            kind in DOCUMENT_KINDS
            or self.connection.execute(
                select(s.tombstones.c.id).where(
                    s.tombstones.c.agent_id == agent_id, s.tombstones.c.content_hash == digest
                )
            ).first()
            or self.connection.execute(
                select(s.tombstone_sources.c.id).where(
                    s.tombstone_sources.c.agent_id == agent_id, s.tombstone_sources.c.source_hash == digest
                )
            ).first()
        ):
            raise BusinessError(
                "memory_forgotten", "Model writes cannot restore forgotten content or personal documents"
            )
        entry = self._record(
            s.memory_entries,
            {"kind": kind, "content": content, "content_hash": digest, "person_id": person_id, "revision": 1},
            agent_id,
        )
        revision = self._record(
            s.memory_revisions, {"memory_id": entry["id"], "revision": 1, "content": content}, agent_id
        )
        self._evidence(
            agent_id,
            entry["id"],
            revision["id"],
            message_id=message_id,
            quote=quote,
            object_id=object_id,
            model_authored=model_authored,
            action_id=action_id,
            document_revision_id=document_revision_id,
            source_run_id=source_run_id,
        )
        self._touch_person(person_id)
        return entry

    def revise_memory(
        self,
        agent_id: str,
        memory_id: str,
        content: str,
        revision: int,
        *,
        object_id: str | None = None,
        quote: str = "",
    ) -> dict:
        row = self._one(s.memory_entries, agent_id, memory_id)
        if row["deleted_at"]:
            raise BusinessError("memory_deleted", "Memory was forgotten", status=410)
        if row["revision"] != revision:
            raise BusinessError("revision_conflict", "Memory changed; reload before saving")
        if row["person_id"]:
            self._live_person(agent_id, row["person_id"])
        if not content.strip() or len(content) > 20_000:
            raise BusinessError("invalid_memory", "Memory must contain bounded text", status=422)
        self.connection.execute(
            update(s.memory_entries)
            .where(s.memory_entries.c.id == memory_id)
            .values(content=content, content_hash=content_fingerprint(content), revision=revision + 1)
        )
        version = self._record(
            s.memory_revisions, {"memory_id": memory_id, "revision": revision + 1, "content": content}, agent_id
        )
        self._evidence(agent_id, memory_id, version["id"], object_id=object_id, quote=quote)
        self._touch_person(row["person_id"])
        self.audit(agent_id, "memory.revised", memory_id, {"revision": revision + 1})
        return self._one(s.memory_entries, agent_id, memory_id)

    def forget(self, agent_id: str, memory_id: str, revision: int | None = None) -> None:
        root = self._one(s.memory_entries, agent_id, memory_id)
        if root["deleted_at"]:
            return
        if revision is not None and root["revision"] != revision:
            raise BusinessError("revision_conflict", "Memory changed before it could be forgotten")
        pending, seen, invalidated = {memory_id}, set(), set()
        while pending:
            identity = pending.pop()
            if identity in seen:
                continue
            seen.add(identity)
            entry = self._one(s.memory_entries, agent_id, identity)
            if entry["deleted_at"]:
                continue
            sessions = self._memory_sessions(agent_id, identity) - invalidated
            invalidated.update(sessions)
            pending.update(self._invalidate_sessions(agent_id, sessions))
            self._forget_one(agent_id, identity)

    def erase_message(self, agent_id: str, message_id: str) -> None:
        _, derived = self.erase_message_record(agent_id, message_id)
        for memory_id in derived:
            self.forget(agent_id, memory_id)

    def _forget_one(self, agent_id: str, memory_id: str, revision: int | None = None) -> None:
        entry = self._one(s.memory_entries, agent_id, memory_id)
        if entry["deleted_at"]:
            return
        if revision is not None and entry["revision"] != revision:
            raise BusinessError("revision_conflict", "Memory changed before it could be forgotten")
        hashes = set(
            self.connection.execute(
                select(s.source_anchors.c.source_hash).where(
                    s.source_anchors.c.agent_id == agent_id, s.source_anchors.c.memory_id == memory_id
                )
            ).scalars()
        ) - {""}
        hashes.update(
            content_fingerprint(content)
            for content in self.connection.execute(
                select(s.memory_revisions.c.content).where(s.memory_revisions.c.memory_id == memory_id)
            ).scalars()
            if content
        )
        marker = self._record(
            s.tombstones,
            {"memory_id": memory_id, "reason": "owner_requested", "content_hash": entry["content_hash"]},
            agent_id,
        )
        for digest in hashes:
            self._record(s.tombstone_sources, {"tombstone_id": marker["id"], "source_hash": digest}, agent_id)
        self.connection.execute(
            update(s.memory_entries)
            .where(s.memory_entries.c.id == memory_id)
            .values(content="", deleted_at=self.clock.now(), revision=entry["revision"] + 1)
        )
        self.connection.execute(
            update(s.memory_revisions).where(s.memory_revisions.c.memory_id == memory_id).values(content="")
        )
        # Dropping evidence releases object references. Hash-only tombstones
        # prevent extraction of the same source without retaining its raw quote.
        self.connection.execute(delete(s.source_anchors).where(s.source_anchors.c.memory_id == memory_id))
        self._touch_person(entry["person_id"])
        self.audit(agent_id, "memory.forgotten", memory_id, {})

    def memory_history(self, agent_id: str, memory_id: str, after: int = 0, limit: int = 50) -> list[dict]:
        entry = self._one(s.memory_entries, agent_id, memory_id)
        rows = self.connection.execute(
            select(s.memory_revisions)
            .where(
                s.memory_revisions.c.agent_id == agent_id,
                s.memory_revisions.c.memory_id == memory_id,
                s.memory_revisions.c.revision > after,
            )
            .order_by(s.memory_revisions.c.revision)
            .limit(min(100, max(1, limit)))
        ).mappings()
        result = []
        for row in rows:
            sources = [
                dict(r)
                for r in self.connection.execute(
                    select(s.source_anchors).where(s.source_anchors.c.memory_revision_id == row["id"])
                ).mappings()
            ]
            result.append({**dict(row), "sources": sources, "erased": entry["deleted_at"] is not None})
        return result

    def search_memory(
        self, agent_id: str, query: str, limit: int = 20, *, include_documents: bool = False
    ) -> list[dict]:
        if not self.connection.execute(select(s.agents.c.id).where(s.agents.c.id == agent_id)).first():
            raise BusinessError("not_found", "Agent does not exist", status=404)
        if not query.strip():
            return []
        tokens = re.findall(r"[^\W_]+", query, flags=re.UNICODE)[:20]
        if tokens:
            expression = " OR ".join('"' + token.replace('"', '""') + '"' for token in tokens)
            fts = table("memory_fts", column("rowid"))
            matches = (
                self.connection.execute(
                    select(s.memory_entries)
                    .join(fts, literal_column("memory_entries.rowid") == fts.c.rowid)
                    .where(
                        text("memory_fts MATCH :expression"),
                        s.memory_entries.c.agent_id == agent_id,
                        s.memory_entries.c.deleted_at.is_(None),
                        s.memory_entries.c.kind.in_(["fact", "profile", "resident"])
                        if include_documents
                        else s.memory_entries.c.kind.not_in(DOCUMENT_KINDS),
                        ~memory_is_organized(),
                    )
                    .order_by(text("bm25(memory_fts)"))
                    .limit(min(100, max(1, limit))),
                    {"expression": expression},
                )
                .mappings()
                .all()
            )
            if matches:
                return [dict(row) for row in matches]
        statement = (
            select(s.memory_entries)
            .where(
                s.memory_entries.c.agent_id == agent_id,
                s.memory_entries.c.deleted_at.is_(None),
                s.memory_entries.c.kind.in_(["fact", "profile", "resident"])
                if include_documents
                else s.memory_entries.c.kind.not_in(DOCUMENT_KINDS),
                ~memory_is_organized(),
                s.memory_entries.c.content.contains(query, autoescape=True),
            )
            .limit(min(100, max(1, limit)))
        )
        return [dict(row) for row in self.connection.execute(statement).mappings()]
