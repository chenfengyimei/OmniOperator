"""Source documents, explicitly requested local indexes and registered retrieval dependencies."""

from __future__ import annotations

import asyncio
from collections.abc import Awaitable, Callable, Mapping
from typing import TypeVar, cast

from ..domain.contracts import JSON, ActionResult, BusinessError, InvocationContext, Outcome
from ..domain.documents import (
    TEXT_INDEX_VERSION,
    VECTOR_CANDIDATES,
    DocumentText,
    document_title,
    merge_results,
    normalized_vector,
)
from .local_vectors import embed_local, vector_status
from .objects import ObjectService
from .persona import PersonaGrowthInvalidator
from .ports import DatabasePort, LocalVectorizer, Record, UnitOfWork

T = TypeVar("T")


class DocumentService:
    def __init__(
        self,
        database: DatabasePort,
        objects: ObjectService,
        vectorizer: LocalVectorizer | None = None,
        *,
        persona: PersonaGrowthInvalidator | None = None,
    ) -> None:
        self._db, self._objects, self._vectorizer = database, objects, vectorizer
        self._persona = persona
        self._pending: set[asyncio.Task[object]] = set()
        self._slots = asyncio.Semaphore(2)
        self._closing = self._closed = False

    async def _drop_persona_cache(self, agent_id: str) -> None:
        """Derived growth text must not outlive the source it quotes."""
        if self._persona is not None:
            await self._persona.invalidate(agent_id)

    async def _execute(self, operation: Callable[[], Awaitable[T]]) -> T:
        if self._closing:
            raise BusinessError("documents_closing", "Source documents are closing", status=503)
        if len(self._pending) >= 8:
            raise BusinessError("documents_busy", "Source operation queue is full", retryable=True, status=503)

        async def perform() -> T:
            async with self._slots:
                return await operation()

        task = asyncio.create_task(perform(), name="source-document-operation")
        self._pending.add(task)

        def completed(future: asyncio.Task[object]) -> None:
            self._pending.discard(future)
            if not future.cancelled():
                future.exception()

        task.add_done_callback(completed)
        return await asyncio.shield(task)

    async def _source(self, agent_id: str, row: Record) -> DocumentText:
        _, payload = await self._objects.get(agent_id, str(row["object_id"]))
        source = DocumentText.parse(payload)
        if source.sha256 != row["sha256"] or source.size != row["bytes"]:
            raise BusinessError("object_integrity_failed", "Document source fingerprint changed", status=503)
        return source

    async def save(
        self, agent_id: str, title: str, content: str, document_id: str | None = None, revision: int = 0
    ) -> Record:
        title = document_title(title)
        source = DocumentText.from_text(content)

        async def perform() -> Record:
            await self._db.read(lambda u: u.settings(agent_id))
            obj = await self._objects.publish(agent_id, source.content.encode("utf-8"), "text/plain")
            return await self._db.write(
                lambda u: u.document_save(agent_id, title, str(obj["id"]), source, document_id, revision)
            )

        return await self._execute(perform)

    async def import_object(
        self, agent_id: str, title: str, object_id: str, document_id: str | None = None, revision: int = 0
    ) -> Record:
        title = document_title(title)

        async def perform() -> Record:
            _, payload = await self._objects.get(agent_id, object_id)
            source = DocumentText.parse(payload)
            return await self._db.write(
                lambda u: u.document_save(agent_id, title, object_id, source, document_id, revision)
            )

        return await self._execute(perform)

    async def get(self, agent_id: str, document_id: str, revision_id: str | None = None) -> Record:
        async def perform() -> Record:
            row = await self._db.read(lambda u: u.document_get(agent_id, document_id, revision_id))
            source = await self._source(agent_id, row)
            # A concurrent erasure must not publish a previously loaded source.
            await self._db.read(lambda u: u.document_get(agent_id, document_id, str(row["revision_id"])))
            return {**row, "content": source.content}

        return await self._execute(perform)

    async def page(self, agent_id: str, after: str = "", limit: int = 100) -> Record:
        rows = await self._db.read(lambda u: u.document_page(agent_id, after, limit))
        return {"items": list(rows), "next_after": rows[-1]["id"] if len(rows) == limit else None}

    async def history(self, agent_id: str, document_id: str, after: int = 0, limit: int = 50) -> Record:
        rows = await self._db.read(lambda u: u.document_history(agent_id, document_id, after, limit))
        return {"items": list(rows), "next_after": rows[-1]["revision"] if len(rows) == limit else None}

    async def erase(self, agent_id: str, document_id: str, revision: int) -> None:
        async def perform() -> None:
            # Fail closed before any ledger change, then again after the source transaction
            # commits: an owner rebuild racing the erase cannot leave old text in the cache.
            await self._drop_persona_cache(agent_id)
            job = await self._db.write(lambda u: u.document_erasure_begin(agent_id, document_id, revision))
            job_id = str(job["id"])
            if job["state"] == "done":
                await self._drop_persona_cache(agent_id)
                await self._db.write(lambda u: u.document_erasure_finish(agent_id, job_id))
                return

            def remove(u: UnitOfWork) -> None:
                derived = u.document_erase(agent_id, document_id, revision)
                for memory_id in cast(list[str], derived):
                    u.forget(agent_id, memory_id)

            try:
                await self._db.write(remove)
                await self._drop_persona_cache(agent_id)
            except BaseException as exc:
                code = exc.code if isinstance(exc, BusinessError) else "erasure_interrupted"
                await self._db.write(lambda u: u.document_erasure_finish(agent_id, job_id, code))
                raise
            await self._db.write(lambda u: u.document_erasure_finish(agent_id, job_id))

        await self._execute(perform)

    def vector_status(self) -> Record:
        return vector_status(self._vectorizer)

    async def _embed(
        self, texts: list[str], version: str, dimension: int, *, purpose: str = "document"
    ) -> list[list[float]]:
        return await embed_local(self._vectorizer, texts, version, dimension, purpose=purpose)

    async def rebuild(
        self,
        agent_id: str,
        document_id: str,
        kind: str,
        *,
        expected_revision_id: str | None = None,
        guard: Callable[[UnitOfWork], None] | None = None,
        on_build: Callable[[UnitOfWork, str], None] | None = None,
        on_finish: Callable[[UnitOfWork, Record], None] | None = None,
    ) -> Record:
        if kind not in {"full_text", "vector"}:
            raise BusinessError("invalid_index_kind", "Choose full_text or vector", status=422)
        status = self.vector_status()
        if kind == "vector" and not status["available"]:
            raise BusinessError(
                "vector_unavailable", "Install an explicitly configured local vectorizer first", status=503
            )
        version = str(status["version"]) if kind == "vector" else TEXT_INDEX_VERSION
        dimension = cast(int, status["dimension"]) if kind == "vector" else None

        async def perform() -> Record:
            def begin(u: UnitOfWork) -> Record:
                if guard:
                    guard(u)
                row = u.document_build_begin(
                    agent_id, document_id, kind, version, dimension, expected_revision_id=expected_revision_id
                )
                if on_build:
                    on_build(u, str(row["id"]))
                return row

            build = await self._db.write(begin)
            identity = str(build["id"])
            try:
                source = await self._source(agent_id, cast(Record, build["document"]))
                vectors: list[list[float]] | None = None
                if kind == "vector":
                    vectors = []
                    chunks = source.chunks()
                    for i in range(0, len(chunks), 8):
                        vectors.extend(
                            await self._embed(
                                [str(r["content"]) for r in chunks[i : i + 8]], version, cast(int, dimension)
                            )
                        )

                def publish(u: UnitOfWork) -> Record:
                    if guard:
                        guard(u)
                    result = u.document_build_finish(agent_id, identity, source, vectors)
                    if on_finish:
                        on_finish(u, result)
                    return result

                return await self._db.write(publish)
            except BaseException as exc:
                code = exc.code if isinstance(exc, BusinessError) else "index_interrupted"
                await self._db.write(lambda u: u.document_build_finish(agent_id, identity, error=code))
                raise

        return await self._execute(perform)

    async def index_status(self, agent_id: str, document_id: str, after: str = "", limit: int = 50) -> Record:
        rows = await self._db.read(lambda u: u.document_builds(agent_id, document_id, after, limit))
        return {
            "items": list(rows),
            "next_after": rows[-1]["id"] if len(rows) == limit else None,
            "vector": self.vector_status(),
            "text_index_version": TEXT_INDEX_VERSION,
            "full_text": await self._db.read(lambda u: u.document_index_health(agent_id, document_id)),
            "erasure_job": await self._db.read(lambda u: u.document_erasure_status(agent_id, document_id)),
        }

    async def search(
        self, agent_id: str, query: str, limit: int = 20, *, run_id: str | None = None, action_id: str | None = None
    ) -> Record:
        if not isinstance(cast(object, query), str) or not 1 <= len(query.strip()) <= 500 or not 1 <= limit <= 20:
            raise BusinessError(
                "invalid_document_query", "A query of 1 to 500 characters and at most 20 hits is required", status=422
            )

        async def perform() -> Record:
            await self._db.read(lambda u: u.settings(agent_id))
            text_rows = await self._db.read(lambda u: u.document_search(agent_id, query, limit))
            vector_rows, limited, reason = [], False, ""
            status = self.vector_status()
            if status["available"]:
                try:
                    version, dimension = str(status["version"]), cast(int, status["dimension"])
                    query_vector = (await self._embed([query], version, dimension, purpose="query"))[0]
                    candidates = await self._db.read(
                        lambda u: u.document_vector_candidates(agent_id, version, dimension)
                    )
                    limited = len(candidates) > VECTOR_CANDIDATES
                    for row in candidates[:VECTOR_CANDIDATES]:
                        vector = normalized_vector(row.pop("embedding"), dimension)
                        row["similarity"] = sum(a * b for a, b in zip(query_vector, vector, strict=True))
                    vector_rows = sorted(
                        candidates[:VECTOR_CANDIDATES], key=lambda r: (-cast(float, r["similarity"]), r["chunk_id"])
                    )[:limit]
                    if not candidates:
                        reason = "vector_index_missing_or_stale"
                except BusinessError:
                    reason = str(self.vector_status().get("reason") or "vector_unavailable")
            else:
                reason = str(status["reason"])
            rows = cast(
                list[Record],
                merge_results(
                    cast(list[dict[str, object]], text_rows), cast(list[dict[str, object]], vector_rows), limit
                ),
            )
            for revision_id in {str(r["document_revision_id"]) for r in rows}:
                metadata = await self._db.read(
                    lambda u: u.document_get(
                        agent_id,
                        str(next(r["document_id"] for r in rows if r["document_revision_id"] == revision_id)),
                        revision_id,
                    )
                )
                await self._source(agent_id, metadata)
            index_health = await self._db.read(lambda u: u.document_index_health(agent_id))

            def validate(u: UnitOfWork) -> None:
                u.document_validate_results(agent_id, rows)
                if run_id and action_id:
                    u.document_capture(
                        agent_id,
                        run_id,
                        action_id,
                        [
                            {
                                "document_revision_id": r["document_revision_id"],
                                "start": r["start"],
                                "end": r["end"],
                            }
                            for r in rows
                        ],
                    )

            await self._db.write(validate) if run_id else await self._db.read(validate)
            return cast(
                Record,
                {
                    "items": list(rows),
                    "mode": "hybrid" if vector_rows else "full_text",
                    "degraded_reason": reason,
                    "vector_candidates_limited": limited,
                    "unindexed_documents": index_health["unindexed_documents"],
                },
            )

        return await self._execute(perform)

    async def invoke(
        self, name: str, arguments: Mapping[str, JSON], *, operation_id: str, context: InvocationContext
    ) -> ActionResult:
        try:
            if name == "document_search":
                result = await self.search(
                    context.agent_id, str(arguments["query"]), 6, run_id=context.run_id, action_id=operation_id
                )
                rows = cast(list[dict[str, object]], result["items"])
                text = "\n".join(
                    f"{r['document_id']} revision_id={r['document_revision_id']} range={r['start']}:{r['end']}: {r['content']}"
                    for r in rows
                )
                return ActionResult(Outcome.OK, text or "No source documents matched", data=result)
            if name == "document_read":
                row = await self.get(
                    context.agent_id,
                    str(arguments["document_id"]),
                    str(arguments["revision_id"]) if arguments.get("revision_id") else None,
                )
                start, length = arguments.get("start", 0), arguments.get("length", 2000)
                if type(start) is not int or type(length) is not int or start < 0 or not 1 <= length <= 4000:
                    raise BusinessError("invalid_document_query", "A bounded character range is required", status=422)
                content = str(row["content"])[start : start + length]
                if not content:
                    raise BusinessError("invalid_document_query", "Range is outside the source", status=422)
                await self._db.write(
                    lambda u: u.document_capture(
                        context.agent_id,
                        context.run_id,
                        operation_id,
                        [{"document_revision_id": row["revision_id"], "start": start, "end": start + len(content)}],
                    )
                )
                return ActionResult(
                    Outcome.OK,
                    f"revision_id={row['revision_id']} range={start}:{start + len(content)}\n{content}",
                    data={
                        "document_id": row["id"],
                        "revision_id": row["revision_id"],
                        "start": start,
                        "end": start + len(content),
                        "content": content,
                    },
                )
        except BusinessError as exc:
            return ActionResult(Outcome.ERROR, exc.message, error_code=exc.code)
        return ActionResult(Outcome.ERROR, "Unknown source capability", error_code="tool_not_found")

    def health(self) -> Record:
        return {"closing": self._closing, "pending": len(self._pending), "vector": self.vector_status()}

    async def close(self, timeout_s: float = 30) -> bool:
        self._closing = True
        started = asyncio.get_running_loop().time()
        if self._pending:
            _, pending = await asyncio.wait(self._pending, timeout=timeout_s)
            if pending:
                return False
        if not self._closed and self._vectorizer is not None:
            remaining = max(0.0, timeout_s - (asyncio.get_running_loop().time() - started))
            try:
                await asyncio.wait_for(self._vectorizer.close(), remaining)
            except Exception:  # noqa: BLE001 -- shutdown reports component residue without private adapter text
                return False
        self._closed = True
        return True
