"""Bounded authenticated history notifications over SSE; no event bodies, replay queue or database ownership."""

from __future__ import annotations

import asyncio
import json
import logging
import re
import time
from collections.abc import AsyncIterator, Awaitable, Callable
from typing import cast

from starlette.responses import StreamingResponse
from starlette.types import Receive, Scope, Send

from ..application.ports import Record
from ..domain.contracts import JSON, BusinessError
from ..domain.history import MAX_CURSOR_LENGTH

CAPACITY = 8
PAGE_SIZE = 50
LIFETIME_S = 30.0
POLL_S = 0.5
HEARTBEAT_S = 5.0
MAX_FRAME_BYTES = 8192
log = logging.getLogger("omni.v2.http-events")


def continuation(cursor: str, last_event_id: str) -> str:
    """Header and query resume the exact same scoped history cursor, never an integer offset."""
    if (
        any(len(value) > MAX_CURSOR_LENGTH for value in (cursor, last_event_id))
        or cursor
        and last_event_id
        and cursor != last_event_id
        or any(value and re.fullmatch(r"[A-Za-z0-9_-]+", value) is None for value in (cursor, last_event_id))
    ):
        raise BusinessError("invalid_history_cursor", "Use one intact history cursor to resume events", status=422)
    return cursor or last_event_id


def frame(kind: str, data: Record, cursor: str = "") -> bytes:
    # Notification frames carry only bounded invalidations. Content stays behind
    # the current, authenticated use-case queries, including deletion barriers.
    value = (
        (f"id: {cursor}\n" if cursor else "")
        + f"event: {kind}\n"
        + "data: "
        + json.dumps(data, ensure_ascii=True, separators=(",", ":"), allow_nan=False)
        + "\n\n"
    ).encode("ascii")
    if len(value) > MAX_FRAME_BYTES:
        raise BusinessError("event_stream_frame_limit", "Event notification exceeds its bound", status=503)
    return value


def notification(page: Record) -> Record:
    items = cast(list[Record], page["items"])
    run_ids: list[JSON] = list(sorted({str(item["run_id"]) for item in items}))
    return {
        "count": len(items),
        "run_ids": run_ids,
        "has_more": page["has_more"],
        "history_trimmed": page["history_trimmed"],
    }


class EventResponse(StreamingResponse):
    def __init__(self, body: AsyncIterator[bytes], release: Callable[[], None]) -> None:
        super().__init__(
            body,
            media_type="text/event-stream",
            headers={"Cache-Control": "no-store", "X-Accel-Buffering": "no"},
        )
        self._release = release

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        try:
            # A stalled send must not retain a stream slot indefinitely. The
            # ASGI task owns cancellation; no detached polling task survives it.
            async with asyncio.timeout(LIFETIME_S + HEARTBEAT_S):
                await super().__call__(scope, receive, send)
        finally:
            self._release()


class EventStreams:
    def __init__(
        self,
        *,
        clock: Callable[[], float] = time.monotonic,
        wait: Callable[[float], Awaitable[None]] = asyncio.sleep,
    ) -> None:
        self._clock, self._wait = clock, wait
        self._leases: set[object] = set()

    @property
    def active(self) -> int:
        return len(self._leases)

    async def open(
        self,
        history: Callable[[str], Awaitable[Record]],
        authorized: Callable[[], Awaitable[bool]],
        closing: Callable[[], bool],
        disconnected: Callable[[], Awaitable[bool]],
        cursor: str,
    ) -> EventResponse:
        if self.active >= CAPACITY:
            raise BusinessError("event_stream_busy", "Too many open event streams", status=429, retryable=True)
        lease = object()
        self._leases.add(lease)

        def release() -> None:
            self._leases.discard(lease)

        try:
            if not await authorized():
                raise BusinessError("unauthenticated", "A valid owner token is required", status=401)
            if closing():
                raise BusinessError("not_ready", "Instance is shutting down", status=503)
            # Validate Agent/run ownership and cursor generation before HTTP 200.
            initial = await history(cursor)
        except BaseException:
            release()
            raise

        async def body() -> AsyncIterator[bytes]:
            started = heartbeat = self._clock()
            page = initial
            position = cursor
            try:
                while self._clock() - started < LIFETIME_S:
                    if await disconnected():
                        return
                    if not await authorized():
                        yield frame("unauthenticated", {"code": "unauthenticated"})
                        return
                    if closing():
                        yield frame("closing", {"code": "not_ready"})
                        return
                    next_cursor = str(page["next_cursor"])
                    if page["items"] or next_cursor != position:
                        yield frame("history", notification(page), next_cursor)
                        heartbeat = self._clock()
                    position = next_cursor
                    if not page["has_more"]:
                        if self._clock() - heartbeat >= HEARTBEAT_S:
                            yield b": keepalive\n\n"
                            heartbeat = self._clock()
                        await self._wait(POLL_S)
                    # Every iteration yields to cancellation, including catch-up
                    # pages. The DB port retains ownership of any in-flight read.
                    await self._wait(0)
                    if await disconnected():
                        return
                    if not await authorized() or closing():
                        continue
                    try:
                        page = await history(position)
                    except BusinessError as exc:
                        if exc.code == "history_cursor_expired":
                            yield frame("reset", {"code": "history_cursor_expired"})
                            return
                        yield frame("unavailable", {"code": "event_stream_unavailable"})
                        return
                yield frame("end", {"code": "event_stream_renew"})
            except Exception:  # noqa: BLE001 -- headers are sent; terminate with a bounded, content-free notification
                log.error(
                    "Event stream failed",
                    extra={"event": "http.events_failed", "error_code": "event_stream_unavailable"},
                )
                yield frame("unavailable", {"code": "event_stream_unavailable"})
            finally:
                release()

        return EventResponse(body(), release)
