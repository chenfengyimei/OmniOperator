"""Own bounded QQ listeners and owner conversation grants; turns use the shared V2 kernel."""

from __future__ import annotations

import asyncio
import logging
from collections.abc import Awaitable, Callable, Mapping
from typing import Protocol, cast

from ..domain.contracts import BusinessError
from ..domain.qq import connection_value, parse_event
from .ports import DatabasePort, DeliveryPort, Record

log = logging.getLogger("omni.v2.qq")


class QQListenerPort(Protocol):
    async def listen(
        self,
        configuration: dict[str, object],
        receive: Callable[[object], Awaitable[None]],
        connected: Callable[[], None],
    ) -> None: ...


class QQTransportPort(QQListenerPort, DeliveryPort, Protocol):
    pass


class QQService:
    def __init__(self, db: DatabasePort, transport: QQListenerPort, notify: Callable[[], None]) -> None:
        self._db, self._transport, self._notify = db, transport, notify
        self._worker: asyncio.Task[None] | None = None
        self._listeners: dict[str, tuple[str, asyncio.Task[None]]] = {}
        self._states: dict[str, dict[str, object]] = {}
        self._closing = False
        self._last_error = ""

    async def page(self, agent_id: str, after: str = "", limit: int = 50) -> Record:
        rows = await self._db.read(lambda u: u.qq_binding_page(agent_id, after, limit))
        return {"items": list(rows), "next_after": rows[-1]["id"] if len(rows) == limit else None}

    async def save(
        self, agent_id: str, config_id: str, value: Record, binding_id: str = "", revision: int = 0
    ) -> Record:
        if self._closing:
            raise BusinessError("qq_closing", "QQ service is draining", status=503)
        return await self._db.write(lambda u: u.qq_binding_put(agent_id, config_id, value, binding_id, revision))

    async def receive(
        self,
        agent_id: str,
        config_id: str,
        revision_id: str,
        raw: Mapping[str, object],
        *,
        transport_revision: str = "",
    ) -> Record | None:
        if self._closing:
            return None
        config = cast(
            dict[str, object],
            await self._db.read(lambda u: u.qq_connection_authorized(agent_id, config_id, revision_id)),
        )
        if transport_revision and config["transport_revision"] != transport_revision:
            raise BusinessError("qq_credential_changed", "QQ connection must renew its credential authority")
        value = cast(dict[str, object], config["value"])
        event = parse_event(cast(dict[str, object], raw), cast(str, value["account"]))
        if event is None:
            return None
        result = await self._db.write(
            lambda u: u.qq_accept(agent_id, config_id, revision_id, event, cast(str, config["transport_revision"]))
        )
        self._notify()
        return result

    def health(self) -> dict[str, object]:
        return {
            "worker_alive": bool(self._worker and not self._worker.done()),
            "closing": self._closing,
            "listeners": len(self._listeners),
            "capacity": 4,
            "last_error": self._last_error,
        }

    async def status(self, agent_id: str) -> Record:
        # Never expose another Agent's account, endpoint, credential reference or provider text.
        await self._db.read(lambda u: u.qq_binding_page(agent_id, limit=1))
        cooldowns = await self._db.read(lambda u: u.qq_cooldowns(agent_id))
        return cast(
            Record,
            {
                "health": self.health(),
                "cooldowns": list(cooldowns),
                "connections": [dict(state) for state in self._states.values() if state["agent_id"] == agent_id],
            },
        )

    def start(self) -> None:
        if self._worker is None:
            self._worker = asyncio.create_task(self._loop(), name="qq-supervisor")

    async def _listen(self, config: Mapping[str, object]) -> None:
        key = cast(str, config["id"])
        agent = cast(str, config["agent_id"])
        self._states[key] = {"config_id": key, "agent_id": agent, "state": "connecting", "error_code": ""}

        async def receive(raw: object) -> None:
            try:
                await self.receive(
                    agent,
                    key,
                    cast(str, config["revision_id"]),
                    cast(dict[str, object], raw),
                    transport_revision=cast(str, config["transport_revision"]),
                )
            except BusinessError as exc:
                self._states[key]["last_admission_code"] = exc.code
                # Explicit grants/cooldowns are ordinary denials, not disconnects.
                if exc.code not in {
                    "qq_conversation_denied",
                    "qq_sender_unbound",
                    "qq_cooldown",
                    "qq_message_too_large",
                    "queue_full",
                    "idempotency_conflict",
                    "invalid_qq_event",
                    "invalid_qq_identity",
                }:
                    raise

        try:
            # listen() verifies the provider login before invoking receive().
            def connected() -> None:
                self._states[key].update(state="connected", error_code="")

            await self._transport.listen(cast(dict[str, object], config["value"]), receive, connected)
        except asyncio.CancelledError:
            self._states[key]["state"] = "stopped"
            raise
        except Exception:  # noqa: BLE001 -- provider messages may contain credentials or chat text
            self._states[key].update(state="disconnected", error_code="qq_connection_unavailable")
            log.warning(
                "QQ connection unavailable",
                extra={"event": "qq.disconnected", "agent_id": agent, "error_code": "qq_connection_unavailable"},
            )

    async def _loop(self) -> None:
        retry_at: dict[str, float] = {}
        failures: dict[str, int] = {}
        while not self._closing:
            try:
                desired = cast(list[dict[str, object]], await self._db.read(lambda u: u.qq_connections()))
                if len(desired) > 4:
                    self._last_error = "qq_connection_limit"
                    desired = []
                else:
                    self._last_error = ""
                accounts: dict[str, int] = {}
                valid: list[dict[str, object]] = []
                for config in desired:
                    try:
                        if config.get("connection_error"):
                            raise BusinessError("qq_authority_unavailable", "QQ connection is not authorized")
                        value = connection_value(cast(dict[str, object], config["value"]))
                        account = cast(str, value["account"])
                        accounts[account] = accounts.get(account, 0) + 1
                        valid.append(config)
                    except BusinessError:
                        self._last_error = "qq_configuration_invalid"
                        self._states[cast(str, config["id"])] = {
                            "agent_id": config["agent_id"],
                            "config_id": config["id"],
                            "state": "blocked",
                            "error_code": "qq_authority_unavailable",
                        }
                wanted = {
                    cast(str, c["id"]): c
                    for c in valid
                    if accounts[cast(str, cast(dict[str, object], c["value"])["account"])] == 1
                }
                if len(wanted) != len(valid):
                    self._last_error = "qq_account_ambiguous"
                now = asyncio.get_running_loop().time()
                for key, (revision, task) in list(self._listeners.items()):
                    if key not in wanted or wanted[key]["transport_revision"] != revision or task.done():
                        if not task.cancelling():
                            task.cancel()
                        await asyncio.shield(asyncio.gather(task, return_exceptions=True))
                        del self._listeners[key]
                        if task.done() and not task.cancelled():
                            failures[key] = min(6, failures.get(key, 0) + 1)
                            retry_at[key] = now + 2 ** failures[key]
                for key, config in wanted.items():
                    if key not in self._listeners and now >= retry_at.get(key, 0):
                        self._listeners[key] = (
                            cast(str, config["transport_revision"]),
                            asyncio.create_task(self._listen(config), name="qq-listener-" + key),
                        )
                active_keys = {cast(str, c["id"]) for c in desired} | set(self._listeners)
                self._states = {k: v for k, v in self._states.items() if k in active_keys}
                retry_at = {k: v for k, v in retry_at.items() if k in active_keys}
                failures = {k: v for k, v in failures.items() if k in active_keys}
            except asyncio.CancelledError:
                raise
            except Exception:  # noqa: BLE001 -- optional channel failure must remain visible without leaking payloads
                self._last_error = "qq_supervisor_unavailable"
            await asyncio.sleep(1)

    def stop_accepting(self) -> None:
        self._closing = True

    async def close(self, timeout_s: float = 30) -> bool:
        self.stop_accepting()
        tasks: list[asyncio.Task[None]] = [t for _, t in self._listeners.values()] + (
            [self._worker] if self._worker else []
        )
        for task in tasks:
            if not task.cancelling():
                task.cancel()
        if tasks:
            _, pending = await asyncio.wait(tasks, timeout=max(0, timeout_s))
            if pending:
                return False
            await asyncio.gather(*tasks, return_exceptions=True)
        self._listeners.clear()
        return True
