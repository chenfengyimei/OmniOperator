"""Derived memory vectors and source-bound related-fact decisions; never suppresses or rewrites facts."""

from __future__ import annotations

from typing import TYPE_CHECKING, cast

from sqlalchemy import and_, select, update

from ...application.ports import Record
from ...domain.contracts import BusinessError
from ...domain.documents import normalized_vector, vector_identity
from ...domain.memory_organization import parse_decision, parse_request, request_fingerprint
from ...domain.memory_semantics import CANDIDATES, MAX_BYTES, POLICY, THRESHOLD, digest, paging
from . import schema as s
from .memory_organization_predicates import memory_is_organized
from .memory_organization_repository import CAPTURE_IDENTITY, MemoryOrganizationRepositories

if TYPE_CHECKING:
    from .repository import Repositories


class MemorySemanticRepositories(MemoryOrganizationRepositories):
    def memory_index_audit(self, agent_id: str, version: str, count: int, ready: int) -> None:
        self.audit(agent_id, "memory.index.prepared", agent_id, {"version": version, "count": count, "ready": ready})

    def memory_semantic_capture(self, agent_id: str, memory_id: str, revision: int) -> Record:
        row, _ = self._organization_capture(
            agent_id, memory_id, revision, MAX_BYTES, kinds=("fact", "profile", "resident")
        )
        return row

    def memory_index_page(self, agent_id: str, after: str, limit: int) -> list[Record]:
        paging(after, limit)
        self._organization_agent(agent_id)
        return [
            cast(Record, dict(r))
            for r in self.connection.execute(
                select(s.memory_entries.c.id, s.memory_entries.c.revision)
                .where(
                    s.memory_entries.c.agent_id == agent_id,
                    s.memory_entries.c.id > after,
                    s.memory_entries.c.deleted_at.is_(None),
                    s.memory_entries.c.kind.in_(["fact", "profile", "resident"]),
                    ~memory_is_organized(),
                )
                .order_by(s.memory_entries.c.id)
                .limit(limit)
            ).mappings()
        ]

    def memory_embedding_begin(self, agent_id: str, source: Record, version: str, dimension: int) -> Record:
        vector_identity(version, dimension)
        current = self.memory_semantic_capture(agent_id, cast(str, source["memory_id"]), cast(int, source["revision"]))
        if current != source:
            raise BusinessError("memory_recall_stale", "Memory or its sources changed")
        old = (
            self.connection.execute(
                select(s.memory_embeddings).where(
                    s.memory_embeddings.c.agent_id == agent_id,
                    s.memory_embeddings.c.memory_revision_id == source["memory_revision_id"],
                    s.memory_embeddings.c.version == version,
                    s.memory_embeddings.c.state.in_(["building", "ready"]),
                )
            )
            .mappings()
            .first()
        )
        if old:
            return {**cast(Record, dict(old)), "owned": False}
        return {
            **self._record(
                s.memory_embeddings,
                {
                    **{
                        k: source[k]
                        for k in ("memory_id", "memory_revision_id", "revision", "raw_sha256", "evidence_sha256")
                    },
                    "version": version,
                    "dimension": dimension,
                    "boot_id": self.boot_id,
                    "state": "building",
                    "embedding": None,
                    "finished_at": None,
                    "error_code": "",
                },
                agent_id,
            ),
            "owned": True,
        }

    def memory_embedding_finish(
        self, agent_id: str, identity: str, embedding: list[float] | None = None, error: str = ""
    ) -> Record:
        row = self._one(s.memory_embeddings, agent_id, identity)
        if row["state"] != "building":
            return {k: v for k, v in row.items() if k != "embedding"}
        if row["boot_id"] != self.boot_id:
            raise BusinessError("memory_index_owner_changed", "Index belongs to a different runtime")
        state = "failed" if error else "ready"
        if not error:
            current = self.memory_semantic_capture(agent_id, cast(str, row["memory_id"]), cast(int, row["revision"]))
            if any(current[k] != row[k] for k in ("memory_revision_id", "raw_sha256", "evidence_sha256")):
                state, error = "invalidated", "memory_recall_stale"
            else:
                embedding = normalized_vector(embedding, cast(int, row["dimension"]))
        self.connection.execute(
            update(s.memory_embeddings)
            .where(s.memory_embeddings.c.id == identity)
            .values(
                state=state,
                embedding=embedding if state == "ready" else None,
                finished_at=self.clock.now(),
                error_code=error,
            )
        )
        return {**{k: v for k, v in row.items() if k != "embedding"}, "state": state, "error_code": error}

    def memory_semantic_recover(self) -> None:
        self.connection.execute(
            update(s.memory_embeddings)
            .where(s.memory_embeddings.c.state == "building", s.memory_embeddings.c.boot_id != self.boot_id)
            .values(state="failed", embedding=None, finished_at=self.clock.now(), error_code="index_interrupted")
        )

    def memory_vector_candidates(self, agent_id: str, version: str, dimension: int) -> list[Record]:
        self._organization_agent(agent_id)
        return [
            cast(Record, dict(r))
            for r in self.connection.execute(
                select(
                    s.memory_entries.c.id,
                    s.memory_entries.c.revision,
                    s.memory_embeddings.c.embedding,
                    s.memory_embeddings.c.raw_sha256,
                    s.memory_embeddings.c.evidence_sha256,
                )
                .join(
                    s.memory_embeddings,
                    and_(
                        s.memory_embeddings.c.memory_id == s.memory_entries.c.id,
                        s.memory_embeddings.c.agent_id == s.memory_entries.c.agent_id,
                        s.memory_embeddings.c.revision == s.memory_entries.c.revision,
                    ),
                )
                .where(
                    s.memory_entries.c.agent_id == agent_id,
                    s.memory_entries.c.deleted_at.is_(None),
                    ~memory_is_organized(),
                    s.memory_embeddings.c.state == "ready",
                    s.memory_embeddings.c.version == version,
                    s.memory_embeddings.c.dimension == dimension,
                )
                .order_by(s.memory_entries.c.id)
                .limit(CANDIDATES + 1)
            ).mappings()
        ]

    def memory_recall_validate(self, agent_id: str, rows: list[Record], run_id: str | None = None) -> list[Record]:
        result, used = cast(list[Record], []), 0
        for row in rows:
            current = self.memory_semantic_capture(agent_id, cast(str, row["id"]), cast(int, row["revision"]))
            if ("content" in row and row["content"] != current["content"]) or any(
                k in row and row[k] != current[k] for k in ("raw_sha256", "evidence_sha256")
            ):
                raise BusinessError("memory_recall_stale", "Recall source changed before publication")
            if self.connection.execute(
                select(s.memory_entries.c.id).where(
                    s.memory_entries.c.id == cast(str, row["id"]), memory_is_organized()
                )
            ).first():
                raise BusinessError("memory_recall_stale", "An approved duplicate left recall")
            used += len(cast(str, current["content"]).encode())
            if used > MAX_BYTES:
                raise BusinessError("memory_recall_budget", "Recall exceeds the source byte budget", status=422)
            result.append(
                {
                    **{k: v for k, v in row.items() if k not in {"embedding", "raw_sha256", "evidence_sha256"}},
                    "content": current["content"],
                    "person_id": current["person_id"],
                    "session_id": current["session_id"],
                    "provenance": "anchored" if current["source_count"] else "unverified",
                }
            )
        if run_id:
            unit = cast("Repositories", self)
            unit.assert_run_authorized(agent_id, run_id)
            unit.capture_tool_memories(agent_id, run_id, [cast(str, r["id"]) for r in result])
        return result

    def memory_semantic_replay(self, agent_id: str, request: Record) -> Record | None:
        normalized = parse_request(request)
        self._organization_agent(agent_id)
        row = (
            self.connection.execute(
                select(s.memory_semantic_batches).where(
                    s.memory_semantic_batches.c.agent_id == agent_id,
                    s.memory_semantic_batches.c.request_key == normalized["key"],
                )
            )
            .mappings()
            .first()
        )
        if row and row["request_sha256"] != request_fingerprint(request):
            raise BusinessError("idempotency_conflict", "This key identifies another semantic request")
        return cast(Record, dict(row)) if row else None

    def _semantic_sources(self, agent_id: str, batch_id: str) -> list[Record]:
        return [
            cast(Record, dict(r))
            for r in self.connection.execute(
                select(s.memory_semantic_sources)
                .where(
                    s.memory_semantic_sources.c.agent_id == agent_id, s.memory_semantic_sources.c.batch_id == batch_id
                )
                .order_by(s.memory_semantic_sources.c.position)
                .limit(33)
            ).mappings()
        ]

    def memory_semantic_propose(self, agent_id: str, request: Record, version: str, dimension: int) -> Record:
        prior = self.memory_semantic_replay(agent_id, request)
        if prior:
            return self.memory_semantic_get(agent_id, cast(str, prior["id"]), version)
        vector_identity(version, dimension)
        normalized = parse_request(request)
        captures, vectors, remaining = cast(list[Record], []), cast(list[list[float]], []), MAX_BYTES
        for source in cast(list[Record], normalized["sources"]):
            current, used = self._organization_capture(
                agent_id, cast(str, source["memory_id"]), cast(int, source["revision"]), remaining
            )
            remaining -= used
            row = (
                self.connection.execute(
                    select(s.memory_embeddings).where(
                        s.memory_embeddings.c.agent_id == agent_id,
                        s.memory_embeddings.c.memory_revision_id == current["memory_revision_id"],
                        s.memory_embeddings.c.version == version,
                        s.memory_embeddings.c.dimension == dimension,
                        s.memory_embeddings.c.state == "ready",
                    )
                )
                .mappings()
                .first()
            )
            if not row or any(row[k] != current[k] for k in ("raw_sha256", "evidence_sha256")):
                raise BusinessError(
                    "memory_index_missing", "Build current memory vectors before proposing relationships", status=422
                )
            captures.append(current)
            vectors.append(normalized_vector(row["embedding"], dimension))
        pairs: list[tuple[int, int, int]] = []
        for left, a in enumerate(captures):
            for right in range(left + 1, len(captures)):
                b = captures[right]
                if (
                    a["person_id"] != b["person_id"]
                    or a["session_id"] != b["session_id"]
                    or a["content"] == b["content"]
                ):
                    continue
                similarity = min(
                    1_000_000, round(sum(x * y for x, y in zip(vectors[left], vectors[right], strict=True)) * 1_000_000)
                )
                if similarity >= THRESHOLD:
                    pairs.append((similarity, left, right))
        pairs = sorted(pairs, key=lambda p: (-p[0], p[1], p[2]))[:31]
        batch = self._record(
            s.memory_semantic_batches,
            {
                "request_key": cast(str, normalized["key"]),
                "request_sha256": request_fingerprint(request),
                "policy": POLICY,
                "history_epoch": self._organization_epoch(),
                "version": version,
                "dimension": dimension,
                "source_count": len(captures),
                "candidate_count": len(pairs),
            },
            agent_id,
        )
        sources = [
            self._record(
                s.memory_semantic_sources,
                {"batch_id": batch["id"], "position": i, **{k: row[k] for k in CAPTURE_IDENTITY}},
                agent_id,
            )
            for i, row in enumerate(captures)
        ]
        for similarity, left, right in pairs:
            value: Record = {
                "batch_id": batch["id"],
                "left_source_id": sources[left]["id"],
                "right_source_id": sources[right]["id"],
                "similarity_micro": similarity,
            }
            self._record(
                s.memory_semantic_links,
                {
                    **value,
                    "fingerprint": digest(
                        cast(
                            dict[str, object],
                            {**value, "manifest": sources, "history_epoch": batch["history_epoch"], "version": version},
                        )
                    ),
                    "state": "pending",
                    "revision": 1,
                    "review_audit_id": None,
                    "undo_audit_id": None,
                    "updated_at": self.clock.now(),
                },
                agent_id,
            )
        self.audit(
            agent_id,
            "memory.semantic.prepared",
            cast(str, batch["id"]),
            {"policy": POLICY, "source_count": len(captures), "candidate_count": len(pairs), "model_calls": 0},
        )
        return self.memory_semantic_get(agent_id, cast(str, batch["id"]), version)

    def memory_semantic_get(self, agent_id: str, identity: str, version: str = "") -> Record:
        batch = self._one(s.memory_semantic_batches, agent_id, identity)
        sources, current = self._semantic_sources(agent_id, identity), cast(list[Record], [])
        valid = (
            batch["history_epoch"] == self._organization_epoch()
            and batch["version"] == version
            and len(sources) == batch["source_count"]
        )
        if valid:
            try:
                remaining = MAX_BYTES
                for source in sources:
                    row, used = self._organization_capture(
                        agent_id, cast(str, source["memory_id"]), cast(int, source["revision"]), remaining
                    )
                    remaining -= used
                    if any(row[k] != source[k] for k in CAPTURE_IDENTITY):
                        raise self._organization_stale()
                    current.append(row)
            except BusinessError:
                valid = False
        links = [
            cast(Record, dict(r))
            for r in self.connection.execute(
                select(s.memory_semantic_links)
                .where(s.memory_semantic_links.c.batch_id == identity, s.memory_semantic_links.c.agent_id == agent_id)
                .order_by(s.memory_semantic_links.c.id)
                .limit(32)
            ).mappings()
        ]
        return {
            **{k: v for k, v in batch.items() if k not in {"request_key", "request_sha256"}},
            "valid": valid,
            "sources": [{**r, **({"content": current[i]["content"]} if valid else {})} for i, r in enumerate(sources)],
            "links": [{**r, "valid": valid and r["state"] != "invalidated"} for r in links],
        }

    def memory_semantic_page(self, agent_id: str, after: str, limit: int) -> list[Record]:
        paging(after, limit)
        self._organization_agent(agent_id)
        return [
            cast(Record, dict(r))
            for r in self.connection.execute(
                select(
                    s.memory_semantic_batches.c.id,
                    s.memory_semantic_batches.c.created_at,
                    s.memory_semantic_batches.c.source_count,
                    s.memory_semantic_batches.c.candidate_count,
                    s.memory_semantic_batches.c.version,
                )
                .where(s.memory_semantic_batches.c.agent_id == agent_id, s.memory_semantic_batches.c.id > after)
                .order_by(s.memory_semantic_batches.c.id)
                .limit(limit)
            ).mappings()
        ]

    def memory_semantic_decide(self, agent_id: str, identity: str, request: Record, version: str) -> Record:
        decision = parse_decision(request)
        row = self._one(s.memory_semantic_links, agent_id, identity)
        report = self.memory_semantic_get(agent_id, cast(str, row["batch_id"]), version)
        if row["revision"] != decision["revision"]:
            raise BusinessError("revision_conflict", "Semantic candidate changed")
        if (
            not report["valid"]
            or row["fingerprint"] != decision["fingerprint"]
            or report["history_epoch"] != decision["epoch"]
        ):
            raise BusinessError("memory_semantic_stale", "Sources, local model or recovery identity changed")
        undo = decision["decision"] == "undo"
        if row["state"] != ("accepted" if undo else "pending"):
            raise BusinessError("memory_semantic_state", "Candidate cannot accept this transition")
        target = "revoked" if undo else "accepted" if decision["decision"] == "apply" else "rejected"
        audit = self.audit(
            agent_id,
            "memory.semantic." + target,
            identity,
            {
                "batch_id": row["batch_id"],
                "fingerprint": row["fingerprint"],
                "effect": "related_only_no_suppression",
            },
        )
        self.connection.execute(
            update(s.memory_semantic_links)
            .where(s.memory_semantic_links.c.id == identity)
            .values(
                state=target,
                revision=cast(int, row["revision"]) + 1,
                updated_at=self.clock.now(),
                **({"undo_audit_id": audit} if undo else {"review_audit_id": audit}),
            )
        )
        return self.memory_semantic_get(agent_id, cast(str, row["batch_id"]), version)
