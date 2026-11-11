"""Serialize audited owner credential changes; shield accepted mutations through caller cancellation."""

from __future__ import annotations

import asyncio
import logging
from collections.abc import Awaitable, Callable
from typing import TypeVar, cast

from ..domain.contracts import BusinessError
from ..domain.credentials import validate_reference, validate_secret
from .ports import AsyncSecretPort, DatabasePort, Ids, Observer, Record, UnitOfWork

log = logging.getLogger("omni.v2.credentials")
T = TypeVar("T")


class CredentialService:
    def __init__(
        self,
        database: DatabasePort,
        backend: AsyncSecretPort,
        ids: Ids,
        interrupt: Callable[[list[str]], None],
        observer: Observer,
    ) -> None:
        self._db, self._backend, self._ids, self._interrupt = database, backend, ids, interrupt
        self._observer = observer
        self._lock = asyncio.Lock()
        self._pending: set[asyncio.Task[object]] = set()
        self._closing = False

    async def _change(self, reference: str, value: str | None) -> None:
        async with self._lock:
            identity = self._ids.new()
            with self._observer.context(operation_id=identity):
                try:
                    await self._apply(reference, value, identity)
                except BaseException:
                    log.warning("Credential change did not confirm success", extra={"event": "credential.unconfirmed"})
                    raise
                log.info("Credential change recorded", extra={"event": "credential.applied"})

    async def _apply(self, reference: str, value: str | None, identity: str) -> None:
        self._backend.invalidate(reference)

        def prepare(u: UnitOfWork) -> tuple[Record, list[str]]:
            operation = u.credential_begin(reference, "delete" if value is None else "set", identity)
            return operation, u.delivery_revoke_credential(reference)

        operation, deliveries = await self._db.write(prepare)
        self._interrupt(deliveries)
        token = str(operation["execution_token"])
        dispatched = False
        try:
            await self._db.write(lambda u: u.credential_dispatch(identity, token))
            dispatched = True
            if value is None:
                await self._backend.delete(reference)
            else:
                await self._backend.set(reference, value)
        except BaseException as exc:
            known = (
                not dispatched
                or isinstance(exc, BusinessError)
                and exc.code
                in {
                    "credential_busy",
                    "credential_closing",
                    "invalid_credential_reference",
                    "invalid_credential_value",
                    "redaction_capacity",
                    "credential_not_dispatched",
                }
            )
            error_code = exc.code if isinstance(exc, BusinessError) else "credential_operation_interrupted"
            await self._db.write(
                lambda u: u.credential_settle(identity, token, "not_applied" if known else "unknown", error_code)
            )
            raise
        try:
            await self._db.write(lambda u: u.credential_settle(identity, token, "applied"))
        except BaseException as exc:
            raise BusinessError(
                "credential_change_unknown",
                "Credential may have changed; verify its instance audit before repeating",
            ) from exc

    async def change(self, reference: str, value: str | None) -> None:
        validate_reference(reference)
        if value is not None:
            validate_secret(value)
        await self._owned(lambda: self._change(reference, value))

    async def _owned(self, operation: Callable[[], Awaitable[T]]) -> T:
        if self._closing:
            raise BusinessError("credential_closing", "Credential mutations are draining", status=503)
        if len(self._pending) >= 4:
            raise BusinessError("credential_busy", "Credential mutation queue is full", status=503, retryable=True)

        async def perform():
            return await operation()

        task = asyncio.create_task(perform(), name="omni-credential-operation")
        self._pending.add(task)

        def done(future: asyncio.Task[T]) -> None:
            self._pending.discard(cast(asyncio.Task[object], future))
            if not future.cancelled():
                future.exception()

        task.add_done_callback(done)
        return await asyncio.shield(task)

    async def page(self, after: str = "", limit: int = 100) -> Record:
        rows = await self._db.read(lambda u: u.credential_page(after, limit))
        return cast(Record, {"items": list(rows), "next_after": rows[-1]["id"] if rows else after})

    async def get(self, identity: str) -> Record:
        return await self._db.read(lambda u: u.credential_get(identity))

    async def register(self, reference: str) -> Record:
        validate_reference(reference)
        return await self._owned(lambda: self._db.write(lambda u: u.credential_register(reference)))

    async def history(self, identity: str, after: str = "", limit: int = 50, *, reviews: bool = False) -> Record:
        rows = await self._db.read(
            lambda u: (
                u.credential_review_history(identity, after, limit)
                if reviews
                else u.credential_history(identity, after, limit)
            )
        )
        return {"items": list(rows), "next_after": rows[-1]["id"] if rows else after}

    async def review(
        self, identity: str, revision: int, epoch: int, fingerprint: str, policy: str, decision: str, evidence: str
    ) -> Record:
        review_id = self._ids.new()

        async def perform():
            health = self._backend.health()
            actual_work = health.get("pending") or health.get("resident") or health.get("cleanup_pending")

            def commit(u: UnitOfWork) -> tuple[Record, list[str]]:
                # The existing persistent changing/revision rules retain their
                # precise errors. The extra guard closes only the gap where an
                # unknown result still owns native work after ledger settlement.
                if actual_work and u.credential_get(identity)["state"] != "changing":
                    raise BusinessError(
                        "credential_review_busy",
                        "Wait for actual credential work and process reclamation before review",
                        status=409,
                    )
                row = u.credential_review(
                    identity, revision, epoch, fingerprint, policy, decision, evidence, review_id=review_id
                )
                return row, u.delivery_revoke_credential(str(row["reference"])) if row["reference"] else []

            with self._observer.context(operation_id=review_id):
                row, deliveries = await self._db.write(commit)
                self._interrupt(deliveries)
                if row["reference"]:
                    self._backend.invalidate(str(row["reference"]))
                log.info("Credential review recorded", extra={"event": "credential.reviewed"})
                return row

        return await self._owned(perform)

    async def audit(self, cursor: str = "", limit: int = 100) -> Record:
        page = await self._db.read(lambda u: u.history_page("instance_audit", cursor=cursor, limit=limit))
        return {**page, "unfinished_intent_requires_state_lookup": True}

    def health(self) -> Record:
        return {"closing": self._closing, "pending_changes": len(self._pending), "backend": self._backend.health()}

    def stop_accepting(self) -> None:
        self._closing = True

    async def close(self, timeout_s: float = 30) -> bool:
        self.stop_accepting()
        if self._pending:
            _, pending = await asyncio.wait(self._pending, timeout=max(0, timeout_s))
            return not pending
        return True
