"""One bounded scheduler; durable identities and admission are committed together."""

from __future__ import annotations

import asyncio
import datetime as dt
import logging
from collections.abc import Awaitable, Callable, Mapping
from typing import cast

from ..domain.contracts import BusinessError
from ..domain.scheduling import due_selection, instant, next_due, schedule_value, timestamp
from .ports import Clock, DatabasePort, Record, Timezones, UnitOfWork

log = logging.getLogger("omni.v2.scheduling")


class SchedulingService:
    def __init__(
        self,
        database: DatabasePort,
        clock: Clock,
        zones: Timezones,
        notify: Callable[[], None],
        cancel: Callable[[str, str], Awaitable[None]],
    ) -> None:
        self._db, self._clock, self._zones = database, clock, zones
        self._notify, self._cancel = notify, cancel
        self._worker: asyncio.Task[None] | None = None
        self._wake = asyncio.Event()
        self._closing = False
        self._error = ""
        self._cursor = ""
        self._ticks = self._enqueued = 0
        self._last_tick: str | None = None

    async def save(
        self,
        agent_id: str,
        name: str,
        enabled: bool,
        value: dict[str, object],
        schedule_id: str | None = None,
        revision: int = 0,
    ) -> Record:
        if self._closing:
            raise BusinessError("scheduler_closing", "Scheduling is draining", status=503)
        value = schedule_value(value)
        zone = self._zones.resolve(str(value["timezone"]))
        now = self._clock.now()
        old = await self.get(agent_id, schedule_id) if schedule_id else None
        if value["kind"] == "interval" and value["start_at"] is None:
            value["start_at"] = timestamp(instant(now) + dt.timedelta(seconds=cast(float, value["interval_seconds"])))
        previous = cast(dict[str, object], old["progress"]) if old else {}
        boundary = max(now, str(previous.get("last_scheduled_at") or now))
        due = next_due(value, boundary, zone)
        if enabled and value["kind"] == "once" and due is None:
            raise BusinessError("schedule_in_past", "An enabled one-time schedule must be in the future", status=422)

        def persist(u: UnitOfWork) -> Record:
            if schedule_id:
                current = u.schedule_get(agent_id, schedule_id)
                progress = cast(dict[str, object], current["progress"])
                last = progress.get("last_scheduled_at")
                if last and str(last) > boundary:
                    raise BusinessError("revision_conflict", "Schedule advanced while editing; load it again")
            return u.schedule_save(agent_id, name, enabled, value, self._zones.version, due, schedule_id, revision)

        result = await self._db.write(persist)
        self._wake.set()
        return result

    async def get(self, agent_id: str, schedule_id: str) -> Record:
        return await self._db.read(lambda u: u.schedule_get(agent_id, schedule_id))

    async def page(
        self, agent_id: str, after: str = "", limit: int = 100, include_archived: bool = False
    ) -> list[Record]:
        return await self._db.read(lambda u: u.schedule_page(agent_id, after, limit, include_archived))

    async def history(self, agent_id: str, schedule_id: str, after: int = 0, limit: int = 50) -> list[Record]:
        return await self._db.read(lambda u: u.schedule_history(agent_id, schedule_id, after, limit))

    async def occurrences(self, agent_id: str, schedule_id: str, after: int = 0, limit: int = 50) -> list[Record]:
        return await self._db.read(lambda u: u.schedule_occurrences(agent_id, schedule_id, after, limit))

    async def archive(self, agent_id: str, schedule_id: str, revision: int) -> None:
        await self._db.write(lambda u: u.schedule_archive(agent_id, schedule_id, revision))
        self._wake.set()

    async def review(self, agent_id: str, schedule_id: str, occurrence_id: str, fingerprint: str) -> None:
        await self._db.write(lambda u: u.schedule_review(agent_id, schedule_id, occurrence_id, fingerprint))
        self._wake.set()

    async def cancel(self, agent_id: str, schedule_id: str, occurrence_id: str) -> None:
        run_id = await self._db.write(lambda u: u.schedule_cancel(agent_id, schedule_id, occurrence_id))
        if run_id:
            await self._cancel(agent_id, str(run_id))
        self._wake.set()

    async def tick(self) -> int:
        if self._closing:
            return 0
        rows = await self._db.read(lambda u: u.schedule_candidates(self._cursor))
        self._cursor = str(rows[-1]["id"]) if len(rows) == 16 else ""
        count = 0
        for row in rows:
            if self._closing:
                break
            agent, key = str(row["agent_id"]), str(row["id"])
            try:
                selection: dict[str, object] | None = None
                progress = cast(dict[str, object], row["progress"])
                definition = cast(dict[str, object], row["value"])
                next_at = progress.get("next_at")
                if next_at and str(next_at) <= self._clock.now():
                    selection = due_selection(
                        definition,
                        str(next_at),
                        self._clock.now(),
                        self._zones.resolve(str(definition["timezone"])),
                    )

                def admit(u: UnitOfWork) -> Record | None:
                    occurrence = u.schedule_materialize(row, selection, self._zones.version)
                    if occurrence is None:
                        return None

                    def accept(definition: Mapping[str, object], token: str) -> Record:
                        session = definition.get("session_id")
                        return u.accept_turn(
                            agent,
                            None if session is None else str(session),
                            token,
                            str(definition["prompt"]),
                            {},
                        )

                    return u.schedule_enqueue(occurrence, accept)

                receipt = await self._db.write(admit)
                if receipt:
                    count += 1
                    self._notify()
            except BusinessError as exc:
                if exc.status >= 500:
                    raise
                await self._db.write(lambda u: u.schedule_error(agent, key, exc.code))
                log.warning(
                    "Schedule is blocked",
                    extra={"event": "schedule.blocked", "agent_id": agent, "operation_id": key, "error_code": exc.code},
                )
        self._ticks += 1
        self._enqueued += count
        self._last_tick = self._clock.now()
        return count

    async def _loop(self) -> None:
        while not self._closing:
            try:
                await self.tick()
                self._error = ""
            except asyncio.CancelledError:
                raise
            except Exception:
                self._error = "scheduler_failed"
                log.exception("Scheduler iteration failed", extra={"event": "worker.failed", "error_code": self._error})
            try:
                await asyncio.wait_for(self._wake.wait(), timeout=0.5)
            except TimeoutError:
                pass
            self._wake.clear()

    def notify(self) -> None:
        self._wake.set()

    def start(self) -> None:
        if self._worker or self._closing:
            raise BusinessError("already_started", "Scheduler cannot start twice")
        self._worker = asyncio.create_task(self._loop(), name="omni-scheduler")

    def health(self) -> Record:
        return {
            "ready": bool(self._worker and not self._worker.done() and not self._closing and not self._error),
            "closing": self._closing,
            "error_code": self._error,
            "ticks": self._ticks,
            "enqueued": self._enqueued,
            "last_tick_at": self._last_tick,
            "batch_limit": 16,
            "tzdb_version": self._zones.version,
        }

    def stop_accepting(self) -> None:
        self._closing = True
        self._wake.set()

    async def close(self, timeout_s: float = 5) -> bool:
        self.stop_accepting()
        if self._worker:
            done, pending = await asyncio.wait((self._worker,), timeout=max(0, timeout_s))
            if pending:
                return False
            for task in done:
                if not task.cancelled():
                    task.exception()
        return True
