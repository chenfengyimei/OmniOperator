"""Own bounded durable maintenance executions; never runs exclusive offline cleanup or remote model/device actions."""

from __future__ import annotations

import asyncio
import logging
from typing import cast

from ..domain.contracts import BusinessError
from ..domain.maintenance import MAX_ACTIVE, MAX_PENDING
from .documents import DocumentService
from .memory_organization import MemoryOrganizationService
from .ports import Clock, DatabasePort, Observer, Record, UnitOfWork

log = logging.getLogger("omni.v2.maintenance")


class MaintenanceService:
    def __init__(
        self,
        database: DatabasePort,
        documents: DocumentService,
        clock: Clock,
        observer: Observer,
        memory_organization: MemoryOrganizationService | None = None,
    ) -> None:
        self._db, self._documents, self._clock, self._observer = database, documents, clock, observer
        self._active: dict[str, tuple[Record, asyncio.Task]] = {}
        self._worker: asyncio.Task | None = None
        self._wake = asyncio.Event()
        self._closing = self._closed = False
        self._error = ""
        self._settling: set[str] = set()
        self._memory_organization = memory_organization

    def notify(self) -> None:
        self._wake.set()

    @staticmethod
    def _public(row: Record) -> Record:
        value = {key: item for key, item in row.items() if key not in {"request_key", "request_sha256", "attempt"}}
        attempt = cast(Record | None, row.get("attempt"))
        value["attempt"] = (
            {
                key: attempt[key]
                for key in ("id", "attempt_no", "state", "started_at", "ended_at", "error_code", "build_id")
            }
            if attempt
            else None
        )
        return value

    async def start(self) -> None:
        if self._closing:
            raise BusinessError("maintenance_closing", "Maintenance is draining", status=503)
        if self._worker is not None:
            return
        await self._db.write(lambda u: u.maintenance_recover())
        self._worker = asyncio.create_task(self._loop(), name="omni-maintenance-worker")

    async def submit(self, agent_id: str, request: Record) -> Record:
        if self._closing:
            raise BusinessError("maintenance_closing", "Maintenance is draining", status=503)
        result = await self._db.write(lambda u: u.maintenance_enqueue(agent_id, request))
        self._wake.set()
        return self._public(result)

    async def get(self, agent_id: str, job_id: str) -> Record:
        return self._public(await self._db.read(lambda u: u.maintenance_get(agent_id, job_id)))

    async def page(self, agent_id: str, after: int = 0, limit: int = 50) -> Record:
        rows = await self._db.read(lambda u: u.maintenance_page(agent_id, after, limit))
        return {
            "items": [self._public(row) for row in rows],
            "next_after": rows[-1]["sequence"] if len(rows) == limit else None,
        }

    async def cancel(self, agent_id: str, job_id: str, revision: int) -> Record:
        result = await self._db.write(lambda u: u.maintenance_cancel(agent_id, job_id, revision))
        self._wake.set()
        return self._public(result)

    @staticmethod
    def _guard(unit: UnitOfWork, claim: Record) -> None:
        attempt = cast(Record, claim["attempt"])
        unit.maintenance_guard(
            str(claim["agent_id"]),
            str(claim["id"]),
            str(attempt["id"]),
            str(attempt["token"]),
            cast(int, attempt["fence"]),
        )

    async def _perform(self, claim: Record) -> None:
        agent, identity = str(claim["agent_id"]), str(claim["id"])
        try:
            with (
                self._observer.context(agent_id=agent, operation_id=identity),
                self._observer.span("maintenance." + str(claim["kind"])),
            ):
                await self._db.write(lambda u: u.maintenance_start(claim))
                if claim["kind"] == "document_index_rebuild":

                    def finished(unit: UnitOfWork, build: Record) -> None:
                        if build["state"] != "ready":
                            raise BusinessError("maintenance_source_changed", "Captured document is no longer current")
                        unit.maintenance_finish(claim, str(build["id"]))

                    def bound(unit: UnitOfWork, build_id: str) -> None:
                        unit.maintenance_bind_build(claim, build_id)

                    await self._documents.rebuild(
                        agent,
                        str(claim["document_id"]),
                        "full_text",
                        expected_revision_id=str(claim["revision_id"]),
                        guard=lambda u: self._guard(u, claim),
                        on_build=bound,
                        on_finish=finished,
                    )
                else:

                    def erase(unit):
                        self._guard(unit, claim)
                        derived = unit.document_erase(
                            agent, str(claim["document_id"]), cast(int, claim["source_revision"])
                        )
                        for memory_id in derived:
                            unit.forget(agent, memory_id)
                        unit.document_erasure_finish(agent, str(claim["erasure_job_id"]))
                        return unit.maintenance_finish(claim)

                    await self._db.write(erase)
        except (Exception, asyncio.CancelledError) as exc:
            code = (
                exc.code
                if isinstance(exc, BusinessError)
                else "maintenance_interrupted"
                if isinstance(exc, asyncio.CancelledError)
                else "maintenance_failed"
            )
            # No caller cancels the actual operation. Publication and success are one
            # transaction; a late error cannot downgrade an already committed result.
            self._settling.add(identity)
            reported = False
            while True:
                try:
                    await self._db.write(lambda u: u.maintenance_fail(claim, code))
                    break
                except Exception as settlement:  # noqa: BLE001 -- retain owned settlement on infrastructure failure
                    if isinstance(settlement, BusinessError) and settlement.code == "maintenance_claim_lost":
                        # A different persisted owner cannot be overwritten by this task.
                        break
                    self._error = "maintenance_settlement_pending"
                    if not reported:
                        log.error(
                            "Maintenance settlement needs recovery",
                            extra={
                                "event": "maintenance.settlement_pending",
                                "operation_id": identity,
                                "agent_id": agent,
                                "error_code": self._error,
                            },
                        )
                        reported = True
                    await asyncio.sleep(1)
            self._settling.discard(identity)
            if not self._settling and self._error == "maintenance_settlement_pending":
                self._error = ""
            if isinstance(exc, asyncio.CancelledError):
                raise

    async def tick(self) -> int:
        if self._closing:
            return 0
        for claim, task in list(self._active.values()):
            if not task.done():
                await self._db.write(lambda u, claim=claim: u.maintenance_heartbeat(claim))
        available = MAX_ACTIVE - len(self._active)
        if available <= 0:
            return 0
        processed = 0
        if self._memory_organization is not None:
            for _ in range(available):
                if not await self._memory_organization.process_one():
                    break
                processed += 1
        claims = await self._db.write(lambda u: u.maintenance_claim(available))
        for claim in claims:
            identity = str(claim["id"])
            task = asyncio.create_task(self._perform(claim), name="maintenance-" + identity)
            self._active[identity] = (claim, task)

            def complete(future, identity=identity):
                self._active.pop(identity, None)
                if not future.cancelled():
                    future.exception()
                self._wake.set()

            task.add_done_callback(complete)
        return len(claims) + processed

    async def _loop(self) -> None:
        while not self._closing:
            try:
                await self.tick()
                if self._error != "maintenance_settlement_pending":
                    self._error = ""
            except BusinessError as exc:
                self._error = exc.code
                log.warning(
                    "Maintenance admission paused", extra={"event": "maintenance.paused", "error_code": exc.code}
                )
            except Exception:  # noqa: BLE001 -- fixed error health without leaking adapter exception text
                self._error = "maintenance_worker_failed"
                log.error(
                    "Maintenance worker stopped",
                    extra={"event": "maintenance.worker_failed", "error_code": self._error},
                )
                return
            self._wake.clear()
            try:
                await asyncio.wait_for(self._wake.wait(), timeout=1)
            except TimeoutError:
                pass

    def health(self) -> Record:
        return {
            "ready": bool(self._worker and not self._worker.done() and not self._closing and not self._error),
            "closing": self._closing,
            "active": len(self._active),
            "max_active": MAX_ACTIVE,
            "max_pending": MAX_PENDING,
            "settlement_pending": len(self._settling),
            "error_code": self._error,
        }

    def stop_accepting(self) -> None:
        self._closing = True
        self._wake.set()

    async def close(self, timeout_s: float = 30) -> bool:
        self.stop_accepting()
        tasks = [task for _, task in self._active.values()]
        if self._worker:
            tasks.append(self._worker)
        if tasks:
            _, pending = await asyncio.wait(tasks, timeout=max(0, timeout_s))
            if pending:
                return False
        self._closed = True
        return True
