"""V2 OneBot v11 text transport; no V1 runtime, database access or automatic write replay."""

from __future__ import annotations

import asyncio
import json
import uuid
from collections.abc import AsyncIterator, Awaitable, Callable
from contextlib import asynccontextmanager
from urllib.parse import urlsplit

from ..application.ports import AsyncSecretPort
from ..domain.contracts import BusinessError
from ..domain.delivery import DeliveryResult, OutboundMessage
from ..domain.qq import ADAPTER, connection_value, qq_id


class OneBotTransport:
    identity = ADAPTER

    def __init__(
        self,
        credentials: AsyncSecretPort,
        authorize: Callable[[OutboundMessage], Awaitable[str]],
        *,
        connector: Callable | None = None,
    ) -> None:
        self._credentials, self._connector = credentials, connector
        self._authorize = authorize
        self._connections: set = set()
        self._closing = False
        self._slots = asyncio.Semaphore(8)
        self._owners: set[asyncio.Task] = set()

    def account_key(self, configuration: dict) -> str:
        return connection_value(configuration)["account"]

    @asynccontextmanager
    async def _connect(self, configuration: dict) -> AsyncIterator:
        owner = asyncio.current_task()
        assert owner is not None
        self._owners.add(owner)
        try:
            async with self._open(configuration) as ws:
                yield ws
        finally:
            self._owners.discard(owner)

    @asynccontextmanager
    async def _open(self, configuration: dict) -> AsyncIterator:
        value = connection_value(configuration)
        if self._closing:
            raise BusinessError("qq_closing", "QQ transport is draining")
        token = await self._credentials.get(value["credential_ref"]) if value["credential_ref"] else None
        if value["credential_ref"] and not token:
            raise BusinessError("qq_credential_unavailable", "QQ credential is unavailable")
        if token and any(ord(ch) < 32 or ord(ch) == 127 for ch in token):
            raise BusinessError("qq_credential_invalid", "QQ access token cannot contain control characters")
        connector = self._connector
        if connector is None:
            from websockets.asyncio.client import connect

            connector = connect
        async with self._slots:
            if self._closing:
                raise BusinessError("qq_closing", "QQ transport is draining")
            async with connector(
                value["endpoint"],
                additional_headers={"Authorization": "Bearer " + token} if token else {},
                proxy=None if urlsplit(value["endpoint"]).hostname in {"127.0.0.1", "localhost", "::1"} else True,
                open_timeout=5,
                close_timeout=2,
                max_size=65536,
                max_queue=16,
                ping_interval=20,
                ping_timeout=10,
                # The library's debug frames include tokens and message bodies.
                logger=_quiet_logger(),
            ) as ws:
                self._connections.add(ws)
                try:
                    async with asyncio.timeout(5):
                        result = await self._rpc(ws, "get_login_info", {})
                    if not _success(result) or qq_id(result.get("data", {}).get("user_id")) != value["account"]:
                        raise BusinessError("qq_account_mismatch", "OneBot login identity does not match configuration")
                    yield ws
                finally:
                    self._connections.discard(ws)

    @staticmethod
    async def _rpc(ws, action: str, params: dict) -> dict:
        echo = str(uuid.uuid4())
        await ws.send(json.dumps({"action": action, "params": params, "echo": echo}, ensure_ascii=False))
        while True:
            response = _frame(await ws.recv())
            if response.get("echo") == echo:
                return response

    async def listen(
        self, configuration: dict, receive: Callable[[dict], Awaitable[None]], connected: Callable[[], None]
    ) -> None:
        async with self._connect(configuration) as ws:
            connected()
            while not self._closing:
                event = _frame(await ws.recv())
                if event.get("post_type") == "message":
                    await receive(event)

    async def send(self, message: OutboundMessage) -> DeliveryResult:
        started = False
        try:
            value = connection_value(message.configuration)
            if message.account_key != value["account"]:
                raise BusinessError("qq_account_mismatch", "Captured QQ sender identity changed")
            scene, separator, peer = message.target.partition(":")
            if separator != ":" or scene not in {"private", "group"}:
                raise BusinessError("qq_target_invalid", "QQ target requires private:id or group:id")
            peer = qq_id(peer)
            async with asyncio.timeout(15):
                generation = await self._authorize(message)
                async with self._connect(value) as ws:
                    if await self._authorize(message) != generation:
                        raise BusinessError("qq_credential_changed", "QQ credential authority changed while connecting")
                    # The adapter never retries once the write might have reached the provider.
                    started = True
                    response = await self._rpc(
                        ws,
                        "send_" + scene + "_msg",
                        {
                            "user_id" if scene == "private" else "group_id": int(peer),
                            "message": [{"type": "text", "data": {"text": message.text}}],
                        },
                    )
                    identity = (
                        response.get("data", {}).get("message_id") if isinstance(response.get("data"), dict) else None
                    )
                    if _success(response) and type(identity) is int and -(2**31) <= identity < 2**31:
                        return DeliveryResult("sent", message.fingerprint, receipt_id=str(identity))
                    # OneBot errors do not prove that an external side effect never occurred.
                    return DeliveryResult("unknown", message.fingerprint, error_code="qq_receipt_unverified")
        except asyncio.CancelledError:
            raise
        except Exception:  # noqa: BLE001 -- sanitize provider exceptions; never log frames or token-bearing URLs
            return DeliveryResult(
                "unknown" if started else "not_sent",
                message.fingerprint,
                error_code="qq_send_unknown" if started else "qq_not_dispatched",
            )

    async def close(self, timeout_s: float = 30) -> bool:
        self._closing = True
        # Listener/send owners close their own sockets; do not acknowledge while a request still owns one.
        if self._owners:
            await asyncio.wait(tuple(self._owners), timeout=max(0, timeout_s))
        return not self._owners and not self._connections


def _frame(raw: object) -> dict:
    if not isinstance(raw, str) or len(raw.encode("utf-8")) > 65536:
        raise BusinessError("qq_protocol_invalid", "OneBot requires bounded UTF-8 JSON frames")
    try:
        value = json.loads(raw)
    except (ValueError, RecursionError) as exc:
        raise BusinessError("qq_protocol_invalid", "OneBot returned invalid JSON") from exc
    if not isinstance(value, dict):
        raise BusinessError("qq_protocol_invalid", "OneBot frame must be an object")
    return value


def _success(value: dict) -> bool:
    return (
        value.get("status") == "ok"
        and type(value.get("retcode")) is int
        and value["retcode"] == 0
        and isinstance(value.get("data"), dict)
    )


def _quiet_logger():
    import logging

    logger = logging.getLogger("omni.v2.onebot.wire")
    logger.handlers = [logging.NullHandler()]
    logger.propagate = False
    logger.setLevel(logging.CRITICAL + 1)
    return logger
