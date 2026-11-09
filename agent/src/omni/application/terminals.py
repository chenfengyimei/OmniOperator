"""Bounded owner terminals with durable input receipts, single-writer leases and explicit output gaps."""

from __future__ import annotations

import asyncio
import contextvars
from collections import deque
from collections.abc import Awaitable, Callable
from dataclasses import dataclass, field
from typing import TypeVar, cast

from ..domain.contracts import JSON, BusinessError
from ..domain.terminals import identity as uuid_value
from ..domain.terminals import parse_close, parse_input, parse_open, parse_resize, payload_fingerprint
from .ports import Clock, DatabasePort, Ids, Record, TerminalBackendPort, TerminalProcessPort

T = TypeVar("T")
MAX_SESSIONS, OUTPUT_BYTES, LEASE_SECONDS = 4, 524288, 15


@dataclass
class LiveTerminal:
    agent_id: str
    identity: str
    process: TerminalProcessPort
    stream_id: str
    chunks: deque[tuple[int, str, int]] = field(default_factory=deque)
    sequence: int = 0
    retained: int = 0
    dropped: int = 0
    connection: str = ""
    expires: float = 0
    pump: asyncio.Task | None = None
    closer: asyncio.Task[bool] | None = None
    lock: asyncio.Lock = field(default_factory=asyncio.Lock)


class TerminalService:
    def __init__(self, database: DatabasePort, backend: TerminalBackendPort, clock: Clock, ids: Ids) -> None:
        self._db, self._backend, self._clock, self._ids = database, backend, clock, ids
        self._live: dict[str, LiveTerminal] = {}
        self._finished: dict[str, LiveTerminal] = {}
        self._pending: set[asyncio.Task] = set()
        self._shutdown_closers: dict[str, asyncio.Task[bool]] = {}
        self._failed_pumps: dict[str, LiveTerminal] = {}
        self._opening = asyncio.Lock()
        self._closing = False

    async def _execute(self, operation: Callable[[], Awaitable[T]]) -> T:
        if self._closing:
            raise BusinessError("terminal_draining", "Terminal admission is closed", status=503)
        if len(self._pending) >= 32:
            raise BusinessError("terminal_busy", "Terminal operations are bounded", status=503, retryable=True)

        async def perform() -> T:
            return await operation()

        task = asyncio.create_task(perform(), name="terminal-owner-operation")
        self._pending.add(task)

        def complete(future):
            self._pending.discard(future)
            if not future.cancelled():
                future.exception()

        task.add_done_callback(complete)
        # Losing an HTTP waiter cannot abandon a native call or discard its receipt.
        return await asyncio.shield(task)

    async def options(self, agent_id: str) -> Record:
        settings = await self._db.read(lambda u: u.settings(agent_id))
        value = cast(Record, settings["value"])
        return {
            **self._backend.capabilities(),
            "workspace": value["workspace"],
            "limits": {
                "max_sessions": MAX_SESSIONS,
                "input_bytes": 16384,
                "output_bytes": OUTPUT_BYTES,
                "cols_min": 20,
                "cols_max": 500,
                "rows_min": 5,
                "rows_max": 200,
            },
            "control": "owner_only",
            "model_raw_input": False,
        }

    async def page(self, agent_id: str, after: int = 0, limit: int = 20) -> Record:
        rows = await self._db.read(lambda u: u.terminal_page(agent_id, after, limit))
        items: list[JSON] = list(rows)
        return {"items": items, "next_after": rows[-1]["sequence"] if len(rows) == limit else None}

    async def get(self, agent_id: str, identity: str) -> Record:
        row = await self._db.read(lambda u: u.terminal_get(agent_id, identity))
        entry = self._live.get(identity) or self._finished.get(identity)
        if entry is not None and entry.agent_id == agent_id:
            return {**row, "runtime": entry.process.snapshot()}
        return row

    async def receipt(self, agent_id: str, identity: str, operation: str) -> Record:
        return await self._db.read(lambda u: u.terminal_operation_get(agent_id, identity, operation))

    async def lookup(self, agent_id: str, operation: str) -> Record:
        return await self._db.read(lambda u: u.terminal_operation_lookup(agent_id, operation))

    def review_quiescent(self) -> bool:
        native = self._backend.health()
        return not (
            self._closing
            or self._live
            or self._failed_pumps
            or self._pending
            or native.get("resident")
            or native.get("pending")
            or native.get("cleanup_pending")
        )

    async def while_quiescent(self, operation: Callable[[], Awaitable[Record]]) -> Record:
        # Native starts and review admission share the same lock. A process
        # cannot start between checking actual ownership and committing a review.
        async with self._opening:
            if not self.review_quiescent():
                raise BusinessError(
                    "terminal_review_busy",
                    "Finish actual terminal work and reclamation before recording a review",
                    status=409,
                )
            return await operation()

    async def open(self, agent_id: str, request: Record) -> Record:
        checked = parse_open(request)

        async def perform() -> Record:
            async with self._opening:
                if self._closing or self._failed_pumps:
                    raise BusinessError(
                        "terminal_state_pending",
                        "Terminal state must settle before admitting another process",
                        status=503,
                    )
                prior = await self._db.read(lambda u: u.terminal_open_replay(agent_id, checked))
                if prior is not None:
                    return prior
                if len(self._live) >= MAX_SESSIONS:
                    raise BusinessError(
                        "terminal_capacity", "Four terminals including unreclaimed processes are allowed"
                    )
                settings = await self._db.read(lambda u: u.settings(agent_id))
                cwd = str(cast(Record, settings["value"])["workspace"])
                prepared = await self._backend.prepare(checked["shell"], cwd)
                prepared["configured_workspace"] = cwd
                if self._closing:
                    raise BusinessError("terminal_draining", "Terminal admission is closed", status=503)
                session = await self._db.write(
                    lambda u: u.terminal_open_begin(agent_id, checked, prepared, cast(int, settings["policy_revision"]))
                )
                if session.get("replayed"):
                    return session
                identity, operation = str(session["id"]), checked["operation_id"]
                entry = None
                try:

                    async def authorize():
                        if self._closing:
                            raise BusinessError(
                                "terminal_draining", "Terminal execution was withdrawn while starting", status=503
                            )
                        await self._db.read(lambda u: u.terminal_assert_start(agent_id, identity))

                    process = await self._backend.open({**prepared, **session}, before_dispatch=authorize)
                    entry = LiveTerminal(agent_id, identity, process, self._ids.new())
                    # Publish actual ownership before any cancellable database wait.
                    self._live[identity] = entry
                    native = process.snapshot()

                    def started(u):
                        row = u.terminal_mark_running(
                            agent_id, identity, cast(int, native["pid"]), cast(float, native["created"])
                        )
                        receipt = u.terminal_operation_settle(agent_id, identity, operation, "accepted")
                        return {**row, "operation": receipt, "replayed": False}

                    row = await self._db.write(started)
                    entry.pump = asyncio.create_task(
                        self._drain(entry), name="terminal-output-" + identity, context=contextvars.Context()
                    )
                    entry.pump.add_done_callback(lambda task, owned=entry: self._pump_completed(owned, task))
                    return row
                except BaseException as exc:  # noqa: BLE001 -- every interrupted native start retains an unknown receipt
                    reclaimed = await self._close_live(entry, 2) if entry else False
                    code = exc.code if isinstance(exc, BusinessError) else "terminal_start_unknown"

                    def failed(u):
                        u.terminal_operation_settle(agent_id, identity, operation, "unknown", code)
                        return u.terminal_mark_closed(agent_id, identity, reclaimed, None, code)

                    await self._db.write(failed)
                    if isinstance(exc, asyncio.CancelledError):
                        raise
                    raise BusinessError(
                        "terminal_start_unknown", "Terminal start requires receipt and cleanup verification"
                    ) from None

        return await self._execute(perform)

    def _entry(self, agent_id: str, identity: str) -> LiveTerminal:
        entry = self._live.get(identity) or self._finished.get(identity)
        if entry is None or entry.agent_id != agent_id:
            raise BusinessError(
                "terminal_not_attached",
                "No managed terminal belongs to this boot; inspect its recorded state",
                status=409,
            )
        return entry

    def _writer(self, entry: LiveTerminal, connection: str) -> None:
        uuid_value(connection)
        if not entry.connection or entry.connection != connection or self._clock.monotonic() >= entry.expires:
            raise BusinessError(
                "terminal_connection_expired", "Reconnect explicitly before sending new input", status=409
            )

    async def attach(self, agent_id: str, identity: str) -> Record:
        async def perform() -> Record:
            row = await self.get(agent_id, identity)
            if row["state"] != "exited":
                await self._db.read(lambda u: u.terminal_assert_control(agent_id, identity))
            entry = self._entry(agent_id, identity)
            native = entry.process.snapshot()
            if row["state"] != "exited" and (not native.get("alive") or native.get("cleanup_pending")):
                raise BusinessError("terminal_cleanup_pending", "The native terminal is not ready for owner input")
            if entry.connection and self._clock.monotonic() < entry.expires:
                raise BusinessError("terminal_writer_exists", "This terminal already has a live owner connection")
            entry.connection, entry.expires = self._ids.new(), self._clock.monotonic() + LEASE_SECONDS
            return {
                "session_id": identity,
                "connection_id": entry.connection,
                "stream_id": entry.stream_id,
                "lease_seconds": LEASE_SECONDS,
                "boot_id": row["boot_id"],
            }

        return await self._execute(perform)

    async def detach(self, agent_id: str, identity: str, connection: str) -> Record:
        await self.get(agent_id, identity)
        uuid_value(connection)
        entry = self._live.get(identity) or self._finished.get(identity)
        if entry is not None and entry.agent_id == agent_id and entry.connection == connection:
            entry.connection, entry.expires = "", 0
        return {"detached": True}

    async def output(self, agent_id: str, identity: str, connection: str, stream_id: str, after: int = 0) -> Record:
        if type(after) is not int or not 0 <= after <= 9223372036854775807:
            raise BusinessError("terminal_invalid_cursor", "Use a nonnegative output sequence", status=422)
        row = await self.get(agent_id, identity)
        entry = self._entry(agent_id, identity)
        self._writer(entry, connection)
        if uuid_value(stream_id) != entry.stream_id or after > entry.sequence:
            raise BusinessError(
                "terminal_stream_expired", "Output stream changed; reconnect and replay retained output", status=410
            )
        entry.expires = self._clock.monotonic() + LEASE_SECONDS
        earliest = entry.chunks[0][0] if entry.chunks else entry.sequence + 1
        rows: list[Record] = [{"seq": seq, "data": data} for seq, data, _ in entry.chunks if seq > after][:16]
        chunks: list[JSON] = list(rows)
        next_after = cast(int, rows[-1]["seq"]) if rows else after
        dropped = cast(int, entry.process.snapshot().get("output_dropped", 0))
        gap = after < earliest - 1 or dropped != entry.dropped
        entry.dropped = dropped
        return {
            "stream_id": entry.stream_id,
            "earliest": earliest,
            "high_water": entry.sequence,
            "chunks": chunks,
            "next_after": next_after,
            "has_more": next_after < entry.sequence,
            "gap": gap,
            "state": row["state"],
            "cleanup_pending": row["cleanup_pending"],
        }

    async def _drain(self, entry: LiveTerminal) -> None:
        code = ""
        try:
            while True:
                data = await entry.process.read()
                if not data:
                    break
                for start in range(0, len(data), 4096):
                    text = data[start : start + 4096]
                    size = len(text.encode("utf-8", errors="replace"))
                    entry.sequence += 1
                    entry.chunks.append((entry.sequence, text, size))
                    entry.retained += size
                    while (entry.retained > OUTPUT_BYTES or len(entry.chunks) > 256) and entry.chunks:
                        entry.retained -= entry.chunks.popleft()[2]
        except Exception:  # noqa: BLE001 -- native output faults become a persistent cleanup obligation
            code = "terminal_output_lost"
        finally:
            await self._close_and_record(entry, 2, code)

    def _pump_completed(self, entry: LiveTerminal, task: asyncio.Task) -> None:
        if task.cancelled() or task.exception() is not None:
            self._failed_pumps[entry.identity] = entry

    async def _close_and_record(self, entry: LiveTerminal, timeout_s: float, code: str = "") -> bool:
        reclaimed = await self._close_live(entry, timeout_s)
        native = entry.process.snapshot()
        await self._db.write(
            lambda u: u.terminal_mark_closed(
                entry.agent_id, entry.identity, reclaimed, cast(int | None, native.get("exit_code")), code
            )
        )
        self._failed_pumps.pop(entry.identity, None)
        return reclaimed

    async def _change(self, agent_id: str, identity: str, request: Record, kind: str) -> Record:
        checked = parse_input(request) if kind == "input" else parse_resize(request)

        async def perform() -> Record:
            sha = payload_fingerprint(kind, checked)
            await self.get(agent_id, identity)
            try:
                old = await self._db.read(lambda u: u.terminal_operation_lookup(agent_id, checked["operation_id"]))
            except BusinessError as exc:
                if exc.status != 404:
                    raise
                old = None
            if old:
                op = cast(Record, old["operation"])
                if op["session_id"] != identity or op["payload_sha256"] != sha or op["kind"] != kind:
                    raise BusinessError("terminal_operation_conflict", "Operation identity already has another intent")
                return op
            entry = self._entry(agent_id, identity)
            async with entry.lock:
                self._writer(entry, checked["connection_id"])
                native = entry.process.snapshot()
                if not native.get("alive") or native.get("cleanup_pending"):
                    raise BusinessError("terminal_cleanup_pending", "The native terminal requires cleanup")
                await self._db.read(lambda u: u.terminal_assert_control(agent_id, identity))
                size = len(checked["data"].encode("utf-8")) if kind == "input" else 0
                receipt = await self._db.write(
                    lambda u: u.terminal_operation_begin(agent_id, identity, checked["operation_id"], kind, sha, size)
                )
                if receipt.get("replayed"):
                    return receipt
                dispatched = False
                try:
                    # Recheck the lease after queued database work, immediately before native dispatch.
                    self._writer(entry, checked["connection_id"])
                    await self._db.read(lambda u: u.terminal_assert_control(agent_id, identity))
                    self._writer(entry, checked["connection_id"])
                    dispatched = True
                    if kind == "input":
                        accepted = await entry.process.write(checked["data"], checked["operation_id"])
                        if accepted != size:
                            raise BusinessError("terminal_partial_input", "PTY accepted an incomplete input frame")
                    else:
                        await entry.process.resize(checked["cols"], checked["rows"], checked["operation_id"])
                except Exception:  # noqa: BLE001 -- uncertainty after native dispatch cannot be downgraded to failure
                    entry.connection, entry.expires = "", 0
                    return await self._db.write(
                        lambda u: u.terminal_operation_settle(
                            agent_id,
                            identity,
                            checked["operation_id"],
                            "unknown" if dispatched else "not_sent",
                            "terminal_dispatch_unknown" if dispatched else "terminal_authority_changed",
                        )
                    )

                def settled(u):
                    if kind == "resize":
                        u.terminal_mark_resized(agent_id, identity, checked["cols"], checked["rows"])
                    return u.terminal_operation_settle(agent_id, identity, checked["operation_id"], "accepted")

                return await self._db.write(settled)

        return await self._execute(perform)

    async def input(self, agent_id: str, identity: str, request: Record) -> Record:
        return await self._change(agent_id, identity, request, "input")

    async def resize(self, agent_id: str, identity: str, request: Record) -> Record:
        return await self._change(agent_id, identity, request, "resize")

    async def end(self, agent_id: str, identity: str, request: Record) -> Record:
        checked = parse_close(request)

        async def perform() -> Record:
            receipt = await self._db.write(
                lambda u: u.terminal_operation_begin(
                    agent_id,
                    identity,
                    checked["operation_id"],
                    "close",
                    payload_fingerprint("close", checked),
                    0,
                    checked["revision"],
                )
            )
            if receipt.get("replayed"):
                return {"session": await self.get(agent_id, identity), "operation": receipt}
            entry = self._live.get(identity)
            row = await self.get(agent_id, identity)
            reclaimed = bool(row["state"] == "exited" and not row["cleanup_pending"])
            if entry is not None and entry.agent_id == agent_id:
                entry.connection, entry.expires = "", 0
                reclaimed = await self._close_live(entry, 5)

            def settled(u):
                row = u.terminal_mark_closed(
                    agent_id, identity, reclaimed, None, "" if reclaimed else "terminal_cleanup_pending"
                )
                op = u.terminal_operation_settle(
                    agent_id,
                    identity,
                    checked["operation_id"],
                    "accepted" if reclaimed else "unknown",
                    "" if reclaimed else "terminal_cleanup_pending",
                )
                return {"session": row, "operation": op}

            return await self._db.write(settled)

        return await self._execute(perform)

    async def _close_live(self, entry: LiveTerminal | None, timeout_s: float) -> bool:
        if entry is None:
            return False
        if entry.closer is None or (
            entry.closer.done()
            and (entry.closer.cancelled() or entry.closer.exception() is not None or not entry.closer.result())
        ):
            entry.closer = asyncio.create_task(
                entry.process.close(timeout_s), name="terminal-reclaim-" + entry.identity, context=contextvars.Context()
            )
            entry.closer.add_done_callback(lambda task: task.exception() if not task.cancelled() else None)
        done, _ = await asyncio.wait((entry.closer,), timeout=max(0, timeout_s))
        complete = bool(
            done and not entry.closer.cancelled() and entry.closer.exception() is None and entry.closer.result()
        )
        if complete:
            self._live.pop(entry.identity, None)
            if entry.identity not in self._finished and len(self._finished) >= 8:
                self._finished.pop(next(iter(self._finished)))
            self._finished[entry.identity] = entry
        return complete

    def health(self) -> Record:
        backend = self._backend.health()
        return {
            "closing": self._closing,
            "active": len(self._live),
            "pending": len(self._pending),
            "cleanup_pending": max(
                cast(int, backend["cleanup_pending"]),
                sum(bool(item.process.snapshot().get("cleanup_pending")) for item in self._live.values()),
            ),
            "max_sessions": MAX_SESSIONS,
            "max_pending": 32,
            "state_pending": len(self._failed_pumps),
            "backend": backend,
        }

    def stop_accepting(self) -> None:
        self._closing = True
        for entry in self._live.values():
            entry.connection, entry.expires = "", 0

    async def close(self, timeout_s: float = 30) -> bool:
        self.stop_accepting()
        deadline = self._clock.monotonic() + max(0, timeout_s)
        entries = list({**self._live, **self._failed_pumps}.values())
        for entry in entries:
            old = self._shutdown_closers.get(entry.identity)
            if old is None or old.done():
                self._shutdown_closers[entry.identity] = asyncio.create_task(
                    self._close_and_record(entry, max(0, deadline - self._clock.monotonic())),
                    context=contextvars.Context(),
                )
        tasks = list(self._shutdown_closers.values())
        if tasks:
            await asyncio.wait(tasks, timeout=max(0, deadline - self._clock.monotonic()))
        pending = [task for task in self._pending if not task.done()] + [
            entry.pump for entry in entries if entry.pump and not entry.pump.done()
        ]
        if pending:
            await asyncio.wait(pending, timeout=max(0, deadline - self._clock.monotonic()))
        return (
            not self._live
            and not self._failed_pumps
            and not any(not task.done() or task.cancelled() or task.exception() is not None for task in pending + tasks)
        )
