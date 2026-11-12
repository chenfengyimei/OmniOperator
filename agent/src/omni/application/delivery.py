"""Supervised, bounded outbound execution; never supplies a real transport by default."""

from __future__ import annotations

import asyncio
import logging
from dataclasses import dataclass
from typing import cast

from ..domain.configuration import reference
from ..domain.contracts import JSON, BusinessError, Outcome
from ..domain.delivery import SEND_SECONDS, DeliveryResult, OutboundMessage, bounded_text, result_value
from .ports import Clock, DatabasePort, DeliveryPort, Observer, Record, UnitOfWork

log = logging.getLogger("omni.v2.delivery")


@dataclass
class ActiveSend:
    agent_id: str
    delivery_id: str
    task: asyncio.Task[None]
    deadline: float
    interrupted: bool = False


class DeliveryService:
    def __init__(
        self, database: DatabasePort, clock: Clock, observer: Observer, ports: dict[str, DeliveryPort] | None = None
    ) -> None:
        self._db, self._clock, self._observer = database, clock, observer
        self._ports = dict(ports or {})
        if len(self._ports) > 8:
            raise ValueError("At most eight explicit transport bindings are allowed")
        for driver, port in self._ports.items():
            bounded_text(driver, 40, "transport driver")
            bounded_text(port.identity, 100, "transport identity")
        self._active: dict[str, ActiveSend] = {}
        self._port_closers: dict[int, asyncio.Task[bool]] = {}
        self._close_writes: dict[str, asyncio.Task[object]] = {}
        self._worker: asyncio.Task[None] | None = None
        self._wake = asyncio.Event()
        self._closing = self._closed = False
        self._error = ""

    def _binding(self, configuration: Record) -> tuple[DeliveryPort, str]:
        port = self._ports.get(cast(str, configuration["driver"]))
        if port is None:
            raise BusinessError(
                "delivery_adapter_unavailable", "No sending adapter is installed for this channel", status=503
            )
        return port, bounded_text(port.account_key(configuration), 200, "sender account identity")

    async def create(
        self,
        agent_id: str,
        message_id: str,
        config_id: str,
        target: str,
        key: str,
        parent_id: str | None = None,
        acknowledge_duplicate: bool = False,
    ) -> Record:
        if self._closing:
            raise BusinessError("delivery_closing", "Outbound admission is closed", status=503)
        prior = await self._db.read(
            lambda u: u.delivery_lookup(agent_id, key, message_id, config_id, target, parent_id)
        )
        if prior:
            return prior
        config = await self._db.read(lambda u: u.configuration_get(agent_id, config_id))
        if config["kind"] != "channel":
            raise BusinessError("invalid_delivery_channel", "Choose a channel configuration", status=422)
        port, account = self._binding(cast(Record, config["value"]))
        row = await self._db.write(
            lambda u: u.delivery_create(
                agent_id,
                message_id,
                config_id,
                target,
                key,
                port.identity,
                account,
                str(config["revision_id"]),
                parent_id,
                acknowledge_duplicate,
            )
        )
        self._wake.set()
        return row

    async def page(
        self, agent_id: str, after: int = 0, limit: int = 100, include_archived: bool = False
    ) -> list[Record]:
        return await self._db.read(lambda u: u.delivery_page(agent_id, after, limit, include_archived))

    async def get(self, agent_id: str, delivery_id: str) -> Record:
        return await self._db.read(lambda u: u.delivery_get(agent_id, delivery_id))

    async def attempts(self, agent_id: str, delivery_id: str, after: int = 0, limit: int = 50) -> list[Record]:
        return await self._db.read(lambda u: u.delivery_attempt_page(agent_id, delivery_id, after, limit))

    async def decide(self, agent_id: str, delivery_id: str, allowed: bool, revision: int, fingerprint: str) -> Record:
        if self._closing:
            raise BusinessError("delivery_closing", "Sending authority cannot be granted while draining", status=503)
        if allowed:
            row = await self.get(agent_id, delivery_id)
            config = await self._db.read(lambda u: u.delivery_configuration(agent_id, delivery_id))
            port, account = self._binding(config)
            if port.identity != row["adapter_id"] or account != row["account_key"]:
                raise BusinessError("delivery_adapter_changed", "The approved sender binding is no longer available")
        row = await self._db.write(lambda u: u.delivery_decide(agent_id, delivery_id, allowed, revision, fingerprint))
        self._wake.set()
        return row

    def cancel_active(self, delivery_ids: list[str]) -> None:
        for active in self._active.values():
            if active.delivery_id in delivery_ids and not active.interrupted:
                active.interrupted = True
                active.task.cancel()
        self._wake.set()

    async def cancel(self, agent_id: str, delivery_id: str) -> Record:
        row = await self._db.write(lambda u: u.delivery_interrupt(agent_id, delivery_id))
        self.cancel_active([delivery_id])
        return row

    async def review(
        self, agent_id: str, delivery_id: str, revision: int, fingerprint: str, decision: str, evidence: str
    ) -> Record:
        row = await self._db.write(
            lambda u: u.delivery_review(agent_id, delivery_id, revision, fingerprint, decision, evidence)
        )
        self._wake.set()
        return row

    async def archive(self, agent_id: str, delivery_id: str, revision: int) -> None:
        await self._db.write(lambda u: u.delivery_archive(agent_id, delivery_id, revision))

    async def credential_changed(self, name: str) -> None:
        try:
            if not name:
                raise ValueError("Empty credential reference")
            reference(name)
        except ValueError as exc:
            raise BusinessError("invalid_credential_reference", "Invalid credential reference", status=422) from exc
        ids = await self._db.write(lambda u: u.delivery_revoke_credential(name))
        self.cancel_active(ids)

    async def channels(self, agent_id: str, after: str = "", limit: int = 100) -> Record:
        def read(u: UnitOfWork) -> list[Record]:
            rows = u.configuration_page(agent_id, "channel", after, limit)
            return [u.configuration_get(agent_id, str(row["id"])) for row in rows]

        rows = await self._db.read(read)
        items: list[Record] = []
        for row in rows:
            available, account, code = bool(row["enabled"]), "", ""
            try:
                _, account = self._binding(cast(Record, row["value"]))
            except BusinessError as exc:
                available, code = False, exc.code
            value = cast(dict[str, object], row["value"])
            items.append(
                {
                    "id": row["id"],
                    "name": row["name"],
                    "available": available,
                    "account_key": account,
                    "driver": cast(JSON, value["driver"]),
                    "error_code": code,
                }
            )
        return {"items": cast(JSON, items), "next_after": rows[-1]["id"] if len(rows) == limit else None}

    async def capabilities(self, agent_id: str) -> Record:
        await self._db.read(lambda u: u.configuration_page(agent_id, "channel", limit=1))
        return {
            "items": [
                {"driver": driver, "adapter_id": port.identity, "installed": True}
                for driver, port in self._ports.items()
            ],
            "approval_required": True,
            "max_text_characters": 20000,
        }

    async def tick(self) -> None:
        if self._closing:
            return
        for attempt_id, active in tuple(self._active.items()):
            if active.interrupted:
                continue
            valid = await self._db.read(lambda u: u.delivery_active_check(active.agent_id, attempt_id))
            if not valid or self._clock.monotonic() >= active.deadline:
                await self._db.write(
                    lambda u: u.delivery_interrupt(
                        active.agent_id,
                        active.delivery_id,
                        "delivery_timeout" if valid else "delivery_authorization_revoked",
                    )
                )
                self.cancel_active([active.delivery_id])
        if self._active:
            await self._db.write(lambda u: u.delivery_renew())
        if len(self._active) >= 4:
            return
        rows = await self._db.read(lambda u: u.delivery_candidates())
        for row in rows:
            if self._closing or len(self._active) >= 4:
                break
            agent, key = str(row["agent_id"]), str(row["id"])
            try:
                config = await self._db.read(lambda u: u.delivery_configuration(agent, key))
                port, account = self._binding(config)
            except BusinessError as exc:
                code = exc.code
                await self._db.write(lambda u: u.delivery_block(agent, key, code))
                continue
            claim = await self._db.write(lambda u: u.delivery_claim(agent, key, port.identity, account))
            if claim is None:
                continue
            if self._closing:
                await self._db.write(lambda u: u.delivery_interrupt(agent, key, "instance_closing"))
                break
            attempt_id = str(claim["id"])
            task = asyncio.create_task(
                self._send(
                    agent,
                    key,
                    attempt_id,
                    str(claim["token"]),
                    port,
                    account,
                    str(claim["run_id"]),
                    str(claim["session_id"]),
                ),
                name="outbound-" + attempt_id,
            )
            self._active[attempt_id] = ActiveSend(agent, key, task, self._clock.monotonic() + SEND_SECONDS)
            task.add_done_callback(lambda future, identity=attempt_id: self._completed(identity, future))

    def _completed(self, identity: str, future: asyncio.Task[None]) -> None:
        self._active.pop(identity, None)
        if not future.cancelled() and future.exception():
            self._error = "delivery_cleanup_failed"
            log.error("Outbound cleanup failed", extra={"event": "delivery.cleanup_failed", "error_code": self._error})
        self._wake.set()

    async def _send(
        self,
        agent: str,
        delivery_id: str,
        attempt_id: str,
        token: str,
        port: DeliveryPort,
        account: str,
        run_id: str,
        session_id: str,
    ) -> None:
        child: asyncio.Task[DeliveryResult] | None = None
        payload = None
        with (
            self._observer.context(agent_id=agent, operation_id=delivery_id, run_id=run_id, session_id=session_id),
            self._observer.span("outbound.send") as measured,
        ):
            if measured:
                measured.result(Outcome.ERROR)
            try:
                payload = await self._db.write(
                    lambda u: u.delivery_start(agent, attempt_id, token, port.identity, account)
                )
                if payload is None:
                    return
                if measured:
                    measured.result(Outcome.UNKNOWN)
                child = asyncio.create_task(
                    port.send(
                        OutboundMessage(
                            delivery_id=str(payload["delivery_id"]),
                            attempt_id=str(payload["attempt_id"]),
                            token=str(payload["token"]),
                            fingerprint=str(payload["fingerprint"]),
                            account_key=str(payload["account_key"]),
                            target=str(payload["target"]),
                            text=str(payload["text"]),
                            configuration=cast(dict[str, object], payload["configuration"]),
                        )
                    ),
                    name="transport-" + attempt_id,
                )
                result = await asyncio.shield(child)
            except asyncio.CancelledError:
                await self._db.write(lambda u: u.delivery_interrupt(agent, delivery_id, "delivery_cancelled"))
                if child:
                    child.cancel()
                    while not child.done():
                        try:
                            await asyncio.shield(child)
                        except asyncio.CancelledError:
                            continue
                        except Exception:
                            log.exception(
                                "Transport ended during cancellation", extra={"event": "delivery.transport_cancelled"}
                            )
                            break
                if child and not child.cancelled() and child.exception() is None:
                    result = child.result()
                else:
                    row = await self.get(agent, delivery_id)
                    result = DeliveryResult("unknown", str(row["fingerprint"]), error_code="delivery_cancelled")
            except Exception:
                log.exception("Transport result unavailable", extra={"event": "delivery.transport_failed"})
                row = await self.get(agent, delivery_id)
                result = DeliveryResult("unknown", str(row["fingerprint"]), error_code="transport_exception")
            if not isinstance(cast(object, result), DeliveryResult):
                row = await self.get(agent, delivery_id)
                result = DeliveryResult("unknown", str(row["fingerprint"]), error_code="invalid_delivery_result")
            state = await self._db.write(
                lambda u: u.delivery_settle(agent, attempt_id, token, cast(Record, result_value(result)))
            )
            if measured:
                measured.result(
                    Outcome.OK
                    if state == "sent"
                    else Outcome.UNKNOWN
                    if state == "unknown"
                    else Outcome.CANCELLED
                    if state == "cancelled"
                    else Outcome.ERROR
                )
            log.info(
                "Outbound observation committed", extra={"event": "delivery.result", "error_code": result.error_code}
            )

    async def _loop(self) -> None:
        while not self._closing:
            try:
                await self.tick()
                if self._error != "delivery_cleanup_failed":
                    self._error = ""
            except asyncio.CancelledError:
                raise
            except Exception:
                self._error = "delivery_worker_failed"
                log.exception(
                    "Outbound worker iteration failed", extra={"event": "worker.failed", "error_code": self._error}
                )
            try:
                await asyncio.wait_for(self._wake.wait(), timeout=0.25)
            except TimeoutError:
                pass
            self._wake.clear()

    def start(self) -> None:
        if self._worker or self._closing:
            raise BusinessError("already_started", "Outbound worker cannot start twice")
        self._worker = asyncio.create_task(self._loop(), name="omni-delivery")

    def stop_accepting(self) -> None:
        self._closing = True
        self._wake.set()

    def health(self) -> Record:
        return {
            "ready": bool(self._worker and not self._worker.done() and not self._closing and not self._error),
            "closing": self._closing,
            "active": len(self._active),
            "error_code": self._error,
            "installed_transports": len(self._ports),
            "stalled": sum(active.interrupted for active in self._active.values()),
        }

    async def close(self, timeout_s: float = 30) -> bool:
        if self._closed:
            return True
        self.stop_accepting()
        deadline = asyncio.get_running_loop().time() + max(0, timeout_s)
        if self._worker:
            _, pending = await asyncio.wait(
                (self._worker,), timeout=max(0, deadline - asyncio.get_running_loop().time())
            )
            if pending:
                return False
        recover = self._close_writes.get("recover")
        if recover is None:
            recover = asyncio.create_task(
                self._db.write(lambda u: u.delivery_recover(shutting_down=True)), name="delivery-close-recover"
            )
            self._close_writes["recover"] = recover
        _, pending = await asyncio.wait((recover,), timeout=max(0, deadline - asyncio.get_running_loop().time()))
        if pending:
            return False
        recover.result()
        self.cancel_active([a.delivery_id for a in self._active.values()])
        if self._active:
            _, pending = await asyncio.wait(
                [a.task for a in self._active.values()], timeout=max(0, deadline - asyncio.get_running_loop().time())
            )
            if pending:
                return False
        for identity, port in {id(p): p for p in self._ports.values()}.items():
            previous = self._port_closers.get(identity)
            if previous is None or (
                previous.done()
                and (previous.cancelled() or previous.exception() is not None or previous.result() is not True)
            ):
                previous = asyncio.create_task(
                    port.close(max(0, deadline - asyncio.get_running_loop().time())),
                    name="transport-close-" + str(identity),
                )
                self._port_closers[identity] = previous
            _, pending = await asyncio.wait((previous,), timeout=max(0, deadline - asyncio.get_running_loop().time()))
            if pending:
                return False
            if previous.cancelled() or previous.exception() is not None or previous.result() is not True:
                return False
        release = self._close_writes.get("release")
        if release is None:
            release = asyncio.create_task(
                self._db.write(lambda u: u.delivery_release_boot()), name="delivery-close-release"
            )
            self._close_writes["release"] = release
        _, pending = await asyncio.wait((release,), timeout=max(0, deadline - asyncio.get_running_loop().time()))
        if pending:
            return False
        release.result()
        self._closed = True
        return True
