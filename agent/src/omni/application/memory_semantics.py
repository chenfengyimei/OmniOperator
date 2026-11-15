"""Owned local memory indexing, hybrid recall and explicit related-fact reviews; no automatic merge or deletion."""

from __future__ import annotations

import asyncio
from collections.abc import Awaitable, Callable
from typing import TypedDict, TypeVar, cast

from ..domain.contracts import BusinessError
from ..domain.documents import normalized_vector
from ..domain.memory_semantics import CANDIDATES, MAX_BYTES, merge, paging, query_value
from .local_vectors import embed_local, vector_status
from .ports import DatabasePort, LocalVectorizer, Record, UnitOfWork

T = TypeVar("T")


class RecallReport(TypedDict):
    items: list[Record]
    mode: str
    degraded_reason: str
    vector_candidates_limited: bool


class MemorySemanticService:
    def __init__(self, database: DatabasePort, vectorizer: LocalVectorizer | None = None) -> None:
        self._db, self._vectorizer = database, vectorizer
        self._pending: set[asyncio.Task[object]] = set()
        self._slots = asyncio.Semaphore(2)
        self._closing = False

    async def _execute(self, operation: Callable[[], Awaitable[T]]) -> T:
        if self._closing or len(self._pending) >= 8:
            raise BusinessError(
                "memory_semantics_busy", "Memory semantic work is closed or full", status=503, retryable=True
            )

        async def perform() -> T:
            async with self._slots:
                return await operation()

        task = asyncio.create_task(perform(), name="memory-semantic-operation")
        self._pending.add(task)

        def completed(future: asyncio.Task[object]) -> None:
            self._pending.discard(future)
            if not future.cancelled():
                future.exception()

        task.add_done_callback(completed)
        return await asyncio.shield(task)

    def status(self) -> Record:
        return vector_status(self._vectorizer)

    async def index(self, agent_id: str, after: str = "", limit: int = 32) -> Record:
        paging(after, limit)
        status = self.status()
        if not status["available"]:
            raise BusinessError(
                "vector_unavailable", "Configure a local vector model before indexing memories", status=503
            )
        version, dimension = str(status["version"]), cast(int, status["dimension"])

        async def perform() -> Record:
            def prepare(u: UnitOfWork):
                page = u.memory_index_page(agent_id, after, limit)
                sources = [u.memory_semantic_capture(agent_id, str(r["id"]), cast(int, r["revision"])) for r in page]
                if sum(len(str(r["content"]).encode()) for r in sources) > MAX_BYTES:
                    raise BusinessError("memory_recall_budget", "Use a smaller indexing page", status=422)
                return page, [(u.memory_embedding_begin(agent_id, r, version, dimension), r) for r in sources]

            page, targets = await self._db.write(prepare)
            owned = [(row, source) for row, source in targets if row["owned"]]
            results = [
                {k: v for k, v in row.items() if k not in {"embedding", "owned"}}
                for row, _ in targets
                if not row["owned"]
            ]
            try:
                for offset in range(0, len(owned), 8):
                    batch = owned[offset : offset + 8]
                    embeddings = await embed_local(
                        self._vectorizer, [str(r[1]["content"]) for r in batch], version, dimension
                    )
                    for (row, _), vector in zip(batch, embeddings, strict=True):
                        results.append(
                            await self._db.write(
                                lambda u, r=row, v=vector: u.memory_embedding_finish(agent_id, str(r["id"]), v)
                            )
                        )
                await self._db.write(
                    lambda u: u.memory_index_audit(
                        agent_id, version, len(results), sum(r["state"] == "ready" for r in results)
                    )
                )
            except BaseException as exc:
                error = (
                    "vector_unavailable"
                    if isinstance(exc, BusinessError) and exc.code == "vector_unavailable"
                    else "index_interrupted"
                )
                for row, _ in owned:
                    await self._db.write(
                        lambda u, r=row: u.memory_embedding_finish(agent_id, str(r["id"]), error=error)
                    )
                raise
            return cast(
                Record,
                {
                    "items": results,
                    "next_after": page[-1]["id"] if len(page) == limit else None,
                    "vector": self.status(),
                },
            )

        return await self._execute(perform)

    async def search(self, agent_id: str, query: str, limit: int = 20, *, run_id: str | None = None) -> RecallReport:
        query_value(query, limit)

        async def perform() -> RecallReport:
            text = await self._db.read(lambda u: u.search_memory(agent_id, query, limit, include_documents=True))
            vectors, limited, reason = [], False, ""
            status = self.status()
            if status["available"]:
                try:
                    version, dimension = str(status["version"]), cast(int, status["dimension"])
                    query_vector = (await embed_local(self._vectorizer, [query], version, dimension, purpose="query"))[
                        0
                    ]
                    candidates = await self._db.read(lambda u: u.memory_vector_candidates(agent_id, version, dimension))
                    limited = len(candidates) > CANDIDATES
                    for row in candidates[:CANDIDATES]:
                        vector = normalized_vector(row.pop("embedding"), dimension)
                        row["similarity"] = sum(a * b for a, b in zip(query_vector, vector, strict=True))
                    vectors = sorted(
                        candidates[:CANDIDATES], key=lambda r: (-cast(float, r["similarity"]), str(r["id"]))
                    )[:limit]
                    if not vectors:
                        reason = "vector_index_missing_or_stale"
                except BusinessError:
                    reason = str(self.status().get("reason") or "vector_unavailable")
            else:
                reason = str(status["reason"])
            current = self.status()
            if vectors and (not current["available"] or current.get("version") != status.get("version")):
                vectors, reason = [], "local_vector_identity_changed"
            rows = cast(list[Record], merge(text, vectors, limit))

            def validate(u: UnitOfWork):
                return u.memory_recall_validate(agent_id, rows, run_id)

            result = await self._db.write(validate) if run_id else await self._db.read(validate)
            return {
                "items": result,
                "mode": "hybrid" if vectors else "full_text",
                "degraded_reason": reason,
                "vector_candidates_limited": limited,
            }

        return await self._execute(perform)

    async def propose(self, agent_id: str, request: Record) -> Record:
        async def perform() -> Record:
            prior = await self._db.read(lambda u: u.memory_semantic_replay(agent_id, request))
            if prior:
                return await self.get(agent_id, str(prior["id"]))
            status = self.status()
            if not status["available"]:
                raise BusinessError(
                    "vector_unavailable", "Configure local memory vectors before proposing relationships", status=503
                )
            return await self._db.write(
                lambda u: u.memory_semantic_propose(
                    agent_id, request, str(status["version"]), cast(int, status["dimension"])
                )
            )

        return await self._execute(perform)

    async def get(self, agent_id: str, identity: str) -> Record:
        status = self.status()
        return await self._db.read(
            lambda u: u.memory_semantic_get(agent_id, identity, str(status["version"]) if status["available"] else "")
        )

    async def page(self, agent_id: str, after: str = "", limit: int = 20) -> Record:
        rows = await self._db.read(lambda u: u.memory_semantic_page(agent_id, after, limit))
        return cast(
            Record,
            {"items": rows, "next_after": rows[-1]["id"] if len(rows) == limit else None, "vector": self.status()},
        )

    async def decide(self, agent_id: str, identity: str, request: Record) -> Record:
        async def perform() -> Record:
            status = self.status()
            return await self._db.write(
                lambda u: u.memory_semantic_decide(
                    agent_id, identity, request, str(status["version"]) if status["available"] else ""
                )
            )

        return await self._execute(perform)

    def health(self) -> Record:
        return {
            "pending": len(self._pending),
            "closing": self._closing,
            "vector": self.status(),
            "model_calls_enabled": False,
        }

    def stop_accepting(self) -> None:
        self._closing = True

    async def close(self, timeout_s: float = 30) -> bool:
        self.stop_accepting()
        if self._pending:
            _, pending = await asyncio.wait(self._pending, timeout=max(0, timeout_s))
            return not pending
        return True
