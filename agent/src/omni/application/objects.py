"""Object publication and access use cases; no filesystem paths or ORM objects."""

from __future__ import annotations

import asyncio
import datetime as dt
from collections.abc import Awaitable, Callable
from typing import TypeVar, cast

from ..domain.contracts import JSON, BusinessError
from ..domain.desktop import CapturedFrame
from ..domain.objects import MAX_OBJECT_BYTES, Blob, agent_identity, media_type
from ..domain.omniweave_artifacts import ARTIFACT_MEDIA_TYPE
from .ports import Clock, DatabasePort, ObjectFilesPort, Record, UnitOfWork

T = TypeVar("T")


class ObjectService:
    def __init__(self, database: DatabasePort, files: ObjectFilesPort, clock: Clock) -> None:
        self._db, self._files, self._clock = database, files, clock
        self._pending: set[asyncio.Task[object]] = set()
        self._slots = asyncio.Semaphore(2)
        self._closing = False

    async def _execute(self, operation: Callable[[], Awaitable[T]]) -> T:
        if self._closing:
            raise BusinessError("object_store_closing", "Object storage is closing", status=503)
        if len(self._pending) >= 16:
            raise BusinessError("object_store_busy", "Object queue is full", retryable=True, status=503)

        async def perform():
            async with self._slots:
                try:
                    return await operation()
                except OSError as exc:
                    raise BusinessError("object_io_failed", "Object storage I/O failed", status=503) from exc

        task = asyncio.create_task(perform(), name="object-operation")
        self._pending.add(task)

        def completed(future: asyncio.Task[T]) -> None:
            self._pending.discard(cast(asyncio.Task[object], future))
            if not future.cancelled():
                future.exception()

        task.add_done_callback(completed)
        # Cancellation cannot cut between durable file publication and database
        # registration. The slot remains charged until real I/O and commit end.
        return await asyncio.shield(task)

    async def publish(self, agent_id: str, content: bytes, mime: str, *, pinned: bool = False) -> Record:
        agent_identity(agent_id)
        media_type(mime)
        if mime == ARTIFACT_MEDIA_TYPE:
            raise BusinessError(
                "invalid_media_type", "This media type belongs to scoped OmniWeave artifacts", status=422
            )
        if len(content) > MAX_OBJECT_BYTES:
            raise BusinessError("object_too_large", "Object exceeds the 16 MiB limit", status=413)

        async def perform():
            await self._db.read(lambda u: u.settings(agent_id))
            blob = await self._files.put(agent_id, content)
            expires = (dt.datetime.fromisoformat(self._clock.now()) + dt.timedelta(days=1)).isoformat(
                timespec="microseconds"
            )
            return await self._db.write(lambda u: u.object_register(blob, mime, expires, pinned))

        return await self._execute(perform)

    @staticmethod
    def _blob(row: Record) -> Blob:
        if not isinstance(row["agent_id"], str) or not isinstance(row["sha256"], str) or type(row["bytes"]) is not int:
            raise BusinessError("object_metadata_invalid", "Stored object metadata is invalid", status=503)
        return Blob(row["agent_id"], row["sha256"], row["bytes"])

    async def publish_capture(
        self,
        agent_id: str,
        session_id: str,
        epoch: int,
        operation_id: str,
        frame: CapturedFrame,
        *,
        before_publish: Callable[[], Awaitable[None]],
    ) -> Record:
        agent_identity(agent_id)
        frame.validate()
        return await self.publish_linked(
            agent_id,
            frame.content,
            before_publish=before_publish,
            attach=lambda u, blob, expires: u.capture_attach(blob, session_id, epoch, operation_id, frame, expires),
        )

    async def publish_linked(
        self,
        agent_id: str,
        content: bytes,
        *,
        before_publish: Callable[[], Awaitable[None]],
        attach: Callable[[UnitOfWork, Blob, str], Record],
    ) -> Record:
        agent_identity(agent_id)
        if len(content) > MAX_OBJECT_BYTES:
            raise BusinessError("object_too_large", "Object exceeds the 16 MiB limit", status=413)

        async def perform() -> Record:
            await before_publish()
            blob = await self._files.put(agent_id, content)
            await before_publish()
            expires = (dt.datetime.fromisoformat(self._clock.now()) + dt.timedelta(days=1)).isoformat(
                timespec="microseconds"
            )
            # Register file, scoped FK reference and publication audit in one transaction.
            return await self._db.write(lambda u: attach(u, blob, expires))

        return await self._execute(perform)

    async def read_linked(self, authorize: Callable[[UnitOfWork], Record]) -> tuple[Record, bytes]:
        async def perform() -> tuple[Record, bytes]:
            row = await self._db.read(authorize)
            content = await self._files.read(self._blob(row))
            current = await self._db.read(authorize)
            if self._blob(current) != self._blob(row):
                raise BusinessError("object_scope_conflict", "Object reference changed during the read", status=409)
            return current, content

        return await self._execute(perform)

    async def captures(self, agent_id: str, session_id: str, after: str = "", limit: int = 50) -> Record:
        rows = await self._db.read(lambda u: u.capture_page(agent_id, session_id, after, limit))
        items: list[JSON] = list(rows)
        return cast(Record, {"items": items, "next_after": rows[-1]["id"] if len(rows) == limit else None})

    async def release_capture(self, agent_id: str, session_id: str, capture_id: str) -> None:
        await self._db.write(lambda u: u.capture_remove(agent_id, session_id, capture_id))

    async def get(self, agent_id: str, object_id: str) -> tuple[Record, bytes]:
        async def perform():
            row = await self._db.read(lambda u: u.object_get(agent_id, object_id))
            return row, await self._files.read(self._blob(row))

        return await self._execute(perform)

    async def describe(self, agent_id: str, object_id: str) -> Record:
        return await self._execute(lambda: self._db.read(lambda u: u.object_get(agent_id, object_id)))

    async def pin(self, agent_id: str, object_id: str, pinned: bool, revision: int) -> Record:
        async def perform():
            row = await self._db.read(lambda u: u.object_get(agent_id, object_id))
            await self._files.read(self._blob(row))
            return await self._db.write(lambda u: u.object_pin(agent_id, object_id, pinned, revision))

        return await self._execute(perform)

    async def page(self, agent_id: str, *, after: str = "", limit: int = 100) -> Record:
        async def perform():
            rows = await self._db.read(lambda u: u.object_page(agent_id, after, limit))
            return cast(Record, {"items": rows, "next_after": rows[-1]["id"] if len(rows) == limit else None})

        return await self._execute(perform)

    async def close(self, timeout_s: float = 30) -> bool:
        self._closing = True
        if self._pending:
            _, pending = await asyncio.wait(self._pending, timeout=timeout_s)
            if pending:
                return False
        await self._files.close()
        return True
