"""Source revision, bounded search and derived index transactions; never reads files."""

from __future__ import annotations

import re

from sqlalchemy import delete, func, select, text, update

from ...domain.contracts import BusinessError
from ...domain.documents import TEXT_INDEX_VERSION, VECTOR_CANDIDATES, DocumentText, document_title, normalized_vector
from . import schema as s
from .retention_repository import RetentionRepositories


class DocumentRepositories(RetentionRepositories):
    def document_get(self, agent_id: str, document_id: str, revision_id: str | None = None) -> dict:
        doc = self._one(s.source_documents, agent_id, document_id)
        if doc["deleted_at"]:
            raise BusinessError("document_erased", "Source document was erased", status=410)
        statement = select(s.document_revisions).where(
            s.document_revisions.c.document_id == document_id, s.document_revisions.c.agent_id == agent_id
        )
        statement = (
            statement.where(s.document_revisions.c.id == revision_id)
            if revision_id
            else statement.where(s.document_revisions.c.revision == doc["revision"])
        )
        row = self.connection.execute(statement).mappings().first()
        if row is None:
            raise BusinessError("not_found", "Document revision does not exist", status=404)
        if row["redacted_at"]:
            raise BusinessError("document_erased", "Source revision was erased", status=410)
        return {**dict(row), "revision_id": row["id"], "id": document_id, "head_revision": doc["revision"]}

    def document_page(self, agent_id: str, after: str = "", limit: int = 100) -> list[dict]:
        if not self.connection.execute(select(s.agents.c.id).where(s.agents.c.id == agent_id)).first():
            raise BusinessError("not_found", "Agent does not exist", status=404)
        return [
            dict(row)
            for row in self.connection.execute(
                select(s.source_documents)
                .where(
                    s.source_documents.c.agent_id == agent_id,
                    s.source_documents.c.deleted_at.is_(None),
                    s.source_documents.c.id > after,
                )
                .order_by(s.source_documents.c.id)
                .limit(min(200, max(1, limit)))
            ).mappings()
        ]

    def _document_chunks(self, agent_id: str, revision_id: str, content: DocumentText, document_id: str) -> None:
        for chunk in content.chunks():
            self._record(s.document_chunks, {"revision_id": revision_id, "document_id": document_id, **chunk}, agent_id)

    def document_save(
        self,
        agent_id: str,
        title: str,
        object_id: str,
        content: DocumentText,
        document_id: str | None = None,
        revision: int = 0,
    ) -> dict:
        title = document_title(title)
        if type(revision) is not int or revision < 0 or (document_id is None and revision != 0):
            raise BusinessError("invalid_document", "An exact document revision is required", status=422)
        obj = self._one(s.objects, agent_id, object_id)
        if obj["sha256"] != content.sha256 or obj["bytes"] != content.size:
            raise BusinessError("object_integrity_failed", "Source bytes do not match the published object", status=503)
        if document_id and revision == 0:
            prior = self.connection.execute(
                select(s.source_documents.c.id).where(
                    s.source_documents.c.id == document_id, s.source_documents.c.agent_id == agent_id
                )
            ).first()
            if prior:
                first = (
                    self.connection.execute(
                        select(s.document_revisions).where(
                            s.document_revisions.c.document_id == document_id,
                            s.document_revisions.c.revision == 1,
                            s.document_revisions.c.agent_id == agent_id,
                        )
                    )
                    .mappings()
                    .one()
                )
                if first["sha256"] != content.sha256 or first["title"] != title:
                    raise BusinessError(
                        "idempotency_conflict", "Document creation identity belongs to different content"
                    )
                return self.document_get(agent_id, document_id, first["id"])
        if document_id and revision > 0:
            current = self.document_get(agent_id, document_id)
            if current["head_revision"] != revision:
                raise BusinessError("revision_conflict", "Document changed; retain your draft and reload")
            self.connection.execute(
                update(s.source_documents)
                .where(s.source_documents.c.id == document_id)
                .values(title=title, revision=revision + 1)
            )
        else:
            values = {"title": title, "revision": 1}
            if document_id:
                values["id"] = document_id
            document_id = self._record(s.source_documents, values, agent_id)["id"]
        assert isinstance(document_id, str)
        audit = self.audit(
            agent_id, "document.saved", document_id, {"revision": revision + 1, "sha256": content.sha256}
        )
        row = self._record(
            s.document_revisions,
            {
                "document_id": document_id,
                "revision": revision + 1,
                "title": title,
                "object_id": object_id,
                "sha256": content.sha256,
                "bytes": content.size,
                "chars": len(content.content),
                "authored_audit_id": audit,
                "text_index_version": TEXT_INDEX_VERSION,
            },
            agent_id,
        )
        self._document_chunks(agent_id, row["id"], content, document_id)
        self._record(
            s.document_index_builds,
            {
                "revision_id": row["id"],
                "kind": "full_text",
                "version": TEXT_INDEX_VERSION,
                "state": "ready",
                "error_code": "",
                "completed_at": self.clock.now(),
            },
            agent_id,
        )
        return self.document_get(agent_id, document_id)

    def document_history(self, agent_id: str, document_id: str, after: int = 0, limit: int = 50) -> list[dict]:
        self._one(s.source_documents, agent_id, document_id)
        return [
            dict(row)
            for row in self.connection.execute(
                select(s.document_revisions)
                .where(
                    s.document_revisions.c.agent_id == agent_id,
                    s.document_revisions.c.document_id == document_id,
                    s.document_revisions.c.revision > after,
                )
                .order_by(s.document_revisions.c.revision)
                .limit(min(100, max(1, limit)))
            ).mappings()
        ]

    def document_text(self, agent_id: str, revision_id: str) -> str:
        version = self._one(s.document_revisions, agent_id, revision_id)
        self.document_get(agent_id, version["document_id"], revision_id)
        rows = list(
            self.connection.execute(
                select(s.document_chunks)
                .where(s.document_chunks.c.agent_id == agent_id, s.document_chunks.c.revision_id == revision_id)
                .order_by(s.document_chunks.c.position)
                .limit(101)
            ).mappings()
        )
        position = 0
        for i, row in enumerate(rows):
            if row["position"] != i or row["start"] != position or row["end"] != row["start"] + len(row["content"]):
                raise BusinessError("document_index_invalid", "Source chunks need a verified rebuild", status=503)
            position = row["end"]
        content = DocumentText.from_text("".join(row["content"] for row in rows))
        if (
            content.sha256 != version["sha256"]
            or content.size != version["bytes"]
            or len(content.content) != version["chars"]
        ):
            raise BusinessError(
                "document_index_invalid", "Source chunks do not match the immutable revision", status=503
            )
        return content.content

    def _document_search_statement(self):
        return (
            select(
                s.document_chunks.c.id.label("chunk_id"),
                s.document_chunks.c.document_id,
                s.document_chunks.c.revision_id.label("document_revision_id"),
                s.document_chunks.c.start,
                s.document_chunks.c.end,
                s.document_chunks.c.content,
                s.document_revisions.c.revision,
                s.document_revisions.c.sha256,
                s.document_revisions.c.title,
            )
            .join(s.document_revisions, s.document_revisions.c.id == s.document_chunks.c.revision_id)
            .join(s.source_documents, s.source_documents.c.id == s.document_chunks.c.document_id)
        )

    def document_search(self, agent_id: str, query: str, limit: int = 20) -> list[dict]:
        if not query.strip():
            return []
        tokens = re.findall(r"[^\W_]+", query, flags=re.UNICODE)[:20]
        if tokens:
            expression = " OR ".join('"' + token + '"' for token in tokens)
            rows = list(
                self.connection.execute(
                    text(
                        "SELECT c.id AS chunk_id,c.document_id,c.revision_id AS document_revision_id,c.start,c.end,c.content,r.revision,r.sha256,r.title FROM document_chunks c JOIN document_fts ON c.rowid=document_fts.rowid JOIN document_revisions r ON r.id=c.revision_id JOIN source_documents d ON d.id=c.document_id WHERE document_fts MATCH :expression AND c.agent_id=:agent AND d.deleted_at IS NULL AND r.redacted_at IS NULL AND r.revision=d.revision AND r.text_index_version=:version ORDER BY bm25(document_fts),c.id LIMIT :limit"
                    ),
                    {"expression": expression, "agent": agent_id, "version": TEXT_INDEX_VERSION, "limit": limit},
                ).mappings()
            )
            if rows:
                return [dict(row) for row in rows]
        statement = (
            self._document_search_statement()
            .where(
                s.document_chunks.c.agent_id == agent_id,
                s.source_documents.c.deleted_at.is_(None),
                s.document_revisions.c.redacted_at.is_(None),
                s.document_revisions.c.revision == s.source_documents.c.revision,
                s.document_revisions.c.text_index_version == TEXT_INDEX_VERSION,
                s.document_chunks.c.content.contains(query, autoescape=True),
            )
            .order_by(s.document_chunks.c.id)
            .limit(limit)
        )
        return [dict(row) for row in self.connection.execute(statement).mappings()]

    def document_capture(self, agent_id: str, run_id: str, action_id: str, sources: list[dict]) -> None:
        self.assert_context(agent_id, run_id)
        action = self._one(s.actions, agent_id, action_id)
        if action["run_id"] != run_id or action["state"] != "executing" or action["writes"]:
            raise BusinessError("invalid_document_read", "Source retrieval must belong to the active read action")
        snapshot = self.connection.execute(
            select(s.context_snapshots.c.id)
            .where(s.context_snapshots.c.agent_id == agent_id, s.context_snapshots.c.run_id == run_id)
            .order_by(s.context_snapshots.c.ordinal.desc())
            .limit(1)
        ).scalar_one()
        for source in sources:
            revision_id = source["document_revision_id"]
            content = self.document_text(agent_id, revision_id)
            start, end = source["start"], source["end"]
            if type(start) is not int or type(end) is not int or not 0 <= start < end <= len(content):
                raise BusinessError(
                    "invalid_document_query", "Captured range must identify live source text", status=422
                )
            exists = self.connection.execute(
                select(s.context_documents.c.id).where(
                    s.context_documents.c.action_id == action_id,
                    s.context_documents.c.document_revision_id == revision_id,
                    s.context_documents.c.source_start == start,
                    s.context_documents.c.source_end == end,
                )
            ).first()
            if not exists:
                self._record(
                    s.context_documents,
                    {
                        "snapshot_id": snapshot,
                        "action_id": action_id,
                        "document_revision_id": revision_id,
                        "source_start": start,
                        "source_end": end,
                    },
                    agent_id,
                )

    def document_validate_results(self, agent_id: str, rows: list[dict]) -> None:
        for revision_id in {r["document_revision_id"] for r in rows}:
            content = self.document_text(agent_id, revision_id)
            if any(
                content[r["start"] : r["end"]] != r["content"] for r in rows if r["document_revision_id"] == revision_id
            ):
                raise BusinessError("document_index_invalid", "Search result changed; rebuild the index", status=503)

    def document_erasure_status(self, agent_id: str, document_id: str) -> dict | None:
        self._one(s.source_documents, agent_id, document_id)
        row = (
            self.connection.execute(
                select(s.document_erasure_jobs)
                .where(
                    s.document_erasure_jobs.c.agent_id == agent_id, s.document_erasure_jobs.c.document_id == document_id
                )
                .order_by(s.document_erasure_jobs.c.revision.desc())
                .limit(1)
            )
            .mappings()
            .first()
        )
        return dict(row) if row else None

    def document_erasure_begin(self, agent_id: str, document_id: str, revision: int) -> dict:
        doc = self._one(s.source_documents, agent_id, document_id)
        if type(revision) is not int or revision != doc["revision"]:
            raise BusinessError("revision_conflict", "Document changed before erasure")
        job = self.document_erasure_status(agent_id, document_id)
        if job and job["revision"] == revision:
            if job["state"] != "done":
                self.connection.execute(
                    update(s.document_erasure_jobs)
                    .where(s.document_erasure_jobs.c.id == job["id"])
                    .values(state="pending", attempt=job["attempt"] + 1, error_code="", updated_at=self.clock.now())
                )
            return self._one(s.document_erasure_jobs, agent_id, job["id"])
        row = self._record(
            s.document_erasure_jobs,
            {
                "document_id": document_id,
                "revision": revision,
                "state": "done" if doc["deleted_at"] else "pending",
                "attempt": 1,
                "error_code": "",
                "updated_at": self.clock.now(),
            },
            agent_id,
        )
        self.audit(
            agent_id, "document.erasure_requested", row["id"], {"document_id": document_id, "revision": revision}
        )
        return row

    def document_erasure_finish(self, agent_id: str, job_id: str, error: str = "") -> None:
        job = self._one(s.document_erasure_jobs, agent_id, job_id)
        if error:
            # A cancelled waiter can observe an exception after its real commit.
            # The already committed outcome remains the business truth.
            if job["state"] == "done":
                return
            self.connection.execute(
                update(s.document_erasure_jobs)
                .where(s.document_erasure_jobs.c.id == job_id)
                .values(state="failed", error_code=error, updated_at=self.clock.now())
            )
            self.audit(agent_id, "document.erasure_failed", job_id, {"error_code": error})
            return
        if not self._one(s.source_documents, agent_id, job["document_id"])["deleted_at"]:
            raise BusinessError("erasure_not_applied", "Source erasure must commit before requests can complete")
        resolved = self.connection.execute(
            update(s.document_erasure_jobs)
            .where(
                s.document_erasure_jobs.c.agent_id == agent_id,
                s.document_erasure_jobs.c.document_id == job["document_id"],
                s.document_erasure_jobs.c.revision <= job["revision"],
                s.document_erasure_jobs.c.state != "done",
            )
            .values(state="done", error_code="", updated_at=self.clock.now())
        ).rowcount
        if resolved:
            self.audit(
                agent_id,
                "document.erasure_resolved",
                job_id,
                {"resolved_requests": resolved, "revision": job["revision"]},
            )

    def document_erase(self, agent_id: str, document_id: str, revision: int) -> set[str]:
        doc = self._one(s.source_documents, agent_id, document_id)
        if doc["deleted_at"]:
            return set()
        if doc["revision"] != revision:
            raise BusinessError("revision_conflict", "Document changed before erasure")
        versions = select(s.document_revisions.c.id).where(
            s.document_revisions.c.document_id == document_id, s.document_revisions.c.agent_id == agent_id
        )
        derived = set(
            self.connection.execute(
                select(s.source_anchors.c.memory_id).where(
                    s.source_anchors.c.document_revision_id.in_(versions), s.source_anchors.c.agent_id == agent_id
                )
            ).scalars()
        )
        reads = select(s.context_documents.c.action_id).where(
            s.context_documents.c.document_revision_id.in_(versions), s.context_documents.c.agent_id == agent_id
        )
        sessions = set(
            self.connection.execute(
                select(s.runs.c.session_id)
                .join(s.actions, s.actions.c.run_id == s.runs.c.id)
                .where(s.actions.c.id.in_(reads))
            ).scalars()
        )
        derived.update(self._invalidate_sessions(agent_id, sessions))
        self.connection.execute(
            update(s.source_documents)
            .where(s.source_documents.c.id == document_id)
            .values(deleted_at=self.clock.now(), title="Erased document")
        )
        self.connection.execute(
            update(s.document_revisions)
            .where(s.document_revisions.c.id.in_(versions))
            .values(
                object_id=None,
                redacted_at=self.clock.now(),
                active_vector_build_id=None,
                title="Erased document",
                text_index_version="",
            )
        )
        builds = select(s.document_index_builds.c.id).where(s.document_index_builds.c.revision_id.in_(versions))
        self.connection.execute(delete(s.document_vectors).where(s.document_vectors.c.build_id.in_(builds)))
        self.connection.execute(delete(s.document_chunks).where(s.document_chunks.c.revision_id.in_(versions)))
        self.connection.execute(
            update(s.document_index_builds)
            .where(s.document_index_builds.c.revision_id.in_(versions))
            .values(state="invalidated", error_code="source_erased", completed_at=self.clock.now())
        )
        self.connection.execute(
            update(s.actions)
            .where(s.actions.c.id.in_(reads), s.actions.c.state == "ok")
            .values(
                result={
                    "status": "ok",
                    "text": "Source document erased",
                    "effect": "none",
                    "error_code": "",
                    "data": {"erased": True},
                    "artifacts": [],
                    "retryable": False,
                }
            )
        )
        self.audit(
            agent_id,
            "document.erased",
            document_id,
            {
                "revisions": self.connection.execute(
                    select(func.count())
                    .select_from(s.document_revisions)
                    .where(s.document_revisions.c.document_id == document_id)
                ).scalar_one()
            },
        )
        return derived

    def document_build_begin(
        self,
        agent_id: str,
        document_id: str,
        kind: str,
        version: str,
        dimension: int | None,
        *,
        expected_revision_id: str | None = None,
    ) -> dict:
        row = self.document_get(agent_id, document_id)
        if expected_revision_id is not None and row["revision_id"] != expected_revision_id:
            raise BusinessError("revision_conflict", "Document changed after the maintenance request")
        if self.connection.execute(
            select(s.document_index_builds.c.id).where(
                s.document_index_builds.c.revision_id == row["revision_id"],
                s.document_index_builds.c.state == "building",
            )
        ).first():
            raise BusinessError("document_index_busy", "A rebuild is already active", retryable=True, status=503)
        build = self._record(
            s.document_index_builds,
            {
                "revision_id": row["revision_id"],
                "kind": kind,
                "version": version,
                "dimension": dimension,
                "state": "building",
                "error_code": "",
            },
            agent_id,
        )
        self.audit(agent_id, "document.index_started", build["id"], {"kind": kind, "version": version})
        return {**build, "document": row}

    def document_build_finish(
        self,
        agent_id: str,
        build_id: str,
        content: DocumentText | None = None,
        vectors: list[list[float]] | None = None,
        error: str = "",
    ) -> dict:
        build = self._one(s.document_index_builds, agent_id, build_id)
        if build["state"] != "building":
            return build
        version = self._one(s.document_revisions, agent_id, build["revision_id"])
        doc = self._one(s.source_documents, agent_id, version["document_id"])
        stale = doc["deleted_at"] or version["redacted_at"] or version["revision"] != doc["revision"]
        state = "invalidated" if stale else "failed" if error else "ready"
        if state == "ready" and build["kind"] == "full_text":
            if content is None or content.sha256 != version["sha256"]:
                raise BusinessError("document_index_invalid", "Rebuild source fingerprint changed", status=503)
            old = select(s.document_index_builds.c.id).where(
                s.document_index_builds.c.revision_id == version["id"], s.document_index_builds.c.kind == "vector"
            )
            self.connection.execute(
                update(s.document_revisions)
                .where(s.document_revisions.c.id == version["id"])
                .values(active_vector_build_id=None, text_index_version=TEXT_INDEX_VERSION)
            )
            self.connection.execute(delete(s.document_vectors).where(s.document_vectors.c.build_id.in_(old)))
            self.connection.execute(
                update(s.document_index_builds)
                .where(s.document_index_builds.c.id.in_(old))
                .values(state="invalidated", error_code="text_index_rebuilt")
            )
            self.connection.execute(delete(s.document_chunks).where(s.document_chunks.c.revision_id == version["id"]))
            self._document_chunks(agent_id, version["id"], content, version["document_id"])
        elif state == "ready":
            chunks = list(
                self.connection.execute(
                    select(s.document_chunks.c.id)
                    .where(s.document_chunks.c.revision_id == version["id"])
                    .order_by(s.document_chunks.c.position)
                ).scalars()
            )
            self.document_text(agent_id, version["id"])
            if vectors is None or len(vectors) != len(chunks):
                raise BusinessError("invalid_vector", "Vector count does not match the captured source chunks")
            for chunk, embedding in zip(chunks, vectors, strict=True):
                self.connection.execute(
                    s.document_vectors.insert().values(
                        build_id=build_id,
                        chunk_id=chunk,
                        agent_id=agent_id,
                        embedding=normalized_vector(embedding, build["dimension"]),
                    )
                )
        self.connection.execute(
            update(s.document_index_builds)
            .where(s.document_index_builds.c.id == build_id)
            .values(state=state, error_code="source_changed" if stale else error, completed_at=self.clock.now())
        )
        if state == "ready" and build["kind"] == "vector":
            self.connection.execute(
                update(s.document_revisions)
                .where(s.document_revisions.c.id == version["id"])
                .values(active_vector_build_id=build_id)
            )
        self.audit(agent_id, "document.index_" + state, build_id, {"kind": build["kind"]})
        return self._one(s.document_index_builds, agent_id, build_id)

    def document_builds(self, agent_id: str, document_id: str, after: str = "", limit: int = 50) -> list[dict]:
        self._one(s.source_documents, agent_id, document_id)
        versions = select(s.document_revisions.c.id).where(s.document_revisions.c.document_id == document_id)
        return [
            dict(row)
            for row in self.connection.execute(
                select(s.document_index_builds)
                .where(
                    s.document_index_builds.c.agent_id == agent_id,
                    s.document_index_builds.c.revision_id.in_(versions),
                    s.document_index_builds.c.id > after,
                )
                .order_by(s.document_index_builds.c.id)
                .limit(min(100, max(1, limit)))
            ).mappings()
        ]

    def document_vector_candidates(self, agent_id: str, version: str, dimension: int) -> list[dict]:
        statement = (
            self._document_search_statement()
            .add_columns(s.document_vectors.c.embedding)
            .join(s.document_vectors, s.document_vectors.c.chunk_id == s.document_chunks.c.id)
            .join(s.document_index_builds, s.document_index_builds.c.id == s.document_vectors.c.build_id)
            .where(
                s.document_chunks.c.agent_id == agent_id,
                s.source_documents.c.deleted_at.is_(None),
                s.document_revisions.c.redacted_at.is_(None),
                s.document_revisions.c.text_index_version == TEXT_INDEX_VERSION,
                s.document_revisions.c.revision == s.source_documents.c.revision,
                s.document_revisions.c.active_vector_build_id == s.document_index_builds.c.id,
                s.document_index_builds.c.state == "ready",
                s.document_index_builds.c.version == version,
                s.document_index_builds.c.dimension == dimension,
            )
            .order_by(s.document_chunks.c.id)
            .limit(VECTOR_CANDIDATES + 1)
        )
        return [dict(row) for row in self.connection.execute(statement).mappings()]

    def document_index_health(self, agent_id: str, document_id: str | None = None) -> dict:
        count = (
            select(func.count())
            .select_from(s.document_chunks)
            .where(s.document_chunks.c.revision_id == s.document_revisions.c.id)
            .scalar_subquery()
        )
        statement = (
            select(
                s.document_revisions.c.id,
                s.document_revisions.c.text_index_version,
                count.label("chunks"),
                s.document_revisions.c.chars,
            )
            .join(s.source_documents, s.source_documents.c.id == s.document_revisions.c.document_id)
            .where(
                s.document_revisions.c.agent_id == agent_id,
                s.source_documents.c.deleted_at.is_(None),
                s.document_revisions.c.revision == s.source_documents.c.revision,
            )
        )
        if document_id:
            self._one(s.source_documents, agent_id, document_id)
            statement = statement.where(s.source_documents.c.id == document_id)
        invalid = statement.subquery()
        missing = self.connection.execute(
            select(func.count())
            .select_from(invalid)
            .where(
                (invalid.c.text_index_version != TEXT_INDEX_VERSION)
                | (invalid.c.chunks != (invalid.c.chars + 999) // 1000)
            )
        ).scalar_one()
        return {"unindexed_documents": missing, "text_index_version": TEXT_INDEX_VERSION}
