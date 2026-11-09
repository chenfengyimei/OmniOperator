"""Controlled fake wire: account verification, literal text, late/unknown receipts and close ownership."""

import asyncio
import json
from contextlib import asynccontextmanager

import pytest

from omni.adapters.onebot import OneBotTransport
from omni.domain.contracts import BusinessError
from omni.domain.delivery import OutboundMessage


class Secrets:
    async def get(self, reference):
        return "TOKEN_CANARY"


class Wire:
    def __init__(self, *, account=456, result="sent", authorize=None):
        self.account, self.result = account, result
        self.frames, self.options, self.pending = [], [], asyncio.Queue()
        self.closed = False
        self.authorize = authorize

    @asynccontextmanager
    async def connect(self, endpoint, **options):
        self.options.append(options)
        try:
            yield self
        finally:
            self.closed = True

    async def send(self, frame):
        request = json.loads(frame)
        self.frames.append(request)
        if request["action"] == "get_login_info":
            data = {"user_id": self.account}
        else:
            if self.result == "disconnect":
                raise OSError("TOKEN_CANARY and chat body must never be logged")
            data = {"message_id": -7 if self.result == "sent" else True}
        await self.pending.put(json.dumps({"status": "ok", "retcode": 0, "echo": request["echo"], "data": data}))

    async def recv(self):
        return await self.pending.get()


def outbound():
    return OutboundMessage(
        "delivery",
        "attempt",
        "token",
        "a" * 64,
        "456",
        "private:123",
        "[CQ:at,qq=all] literal text",
        {"driver": "qq", "endpoint": "ws://127.0.0.1:3001", "account": "456", "credential_ref": "fixture/token"},
    )


@pytest.mark.parametrize("result,expected", [("sent", "sent"), ("disconnect", "unknown"), ("bad_receipt", "unknown")])
def test_wire_sends_exactly_once_and_never_replays_uncertain_writes(result, expected):
    async def scenario():
        wire = Wire(result=result)
        checks = []

        async def authorize(message):
            checks.append(message.token)
            return "same-generation"

        adapter = OneBotTransport(Secrets(), authorize, connector=wire.connect)
        response = await adapter.send(outbound())
        assert response.status == expected and len(checks) == 2
        assert [r["action"] for r in wire.frames] == ["get_login_info", "send_private_msg"]
        assert wire.frames[1]["params"]["message"] == [{"type": "text", "data": {"text": outbound().text}}]
        assert wire.options[0]["additional_headers"]["Authorization"] == "Bearer TOKEN_CANARY"
        assert wire.closed and await adapter.close(1)

    asyncio.run(scenario())


@pytest.mark.parametrize("failure", ["account", "revoked", "rotation"])
def test_pre_dispatch_failure_cannot_send(failure):
    async def scenario():
        wire, count = Wire(account=999 if failure == "account" else 456), 0

        async def authorize(message):
            nonlocal count
            count += 1
            if failure == "revoked" and count == 2:
                raise BusinessError("credential_revoked", "owner revoked")
            return str(count) if failure == "rotation" else "stable"

        adapter = OneBotTransport(Secrets(), authorize, connector=wire.connect)
        response = await adapter.send(outbound())
        assert response.status == "not_sent" and not response.retryable
        assert all(r["action"] == "get_login_info" for r in wire.frames)
        assert await adapter.close(1)

    asyncio.run(scenario())


def test_listener_close_tracks_actual_cleanup_and_hides_wire_logs(caplog):
    async def scenario():
        wire, connected = Wire(), asyncio.Event()

        async def authorize(message):
            return "stable"

        async def receive(raw):
            raise AssertionError("no fixture inbound event")

        adapter = OneBotTransport(Secrets(), authorize, connector=wire.connect)
        listener = asyncio.create_task(adapter.listen(outbound().configuration, receive, connected.set))
        await asyncio.wait_for(connected.wait(), 2)
        assert not await adapter.close(0)
        listener.cancel()
        await asyncio.gather(listener, return_exceptions=True)
        assert await adapter.close(1)
        assert "TOKEN_CANARY" not in caplog.text and outbound().text not in caplog.text

    asyncio.run(scenario())


def test_locked_websocket_client_against_local_fake_onebot():
    async def scenario():
        from websockets.asyncio.server import serve

        frames = []

        async def handler(ws):
            assert ws.request.headers["Authorization"] == "Bearer TOKEN_CANARY"
            async for raw in ws:
                request = json.loads(raw)
                frames.append(request)
                data = {"user_id": 456} if request["action"] == "get_login_info" else {"message_id": 0}
                await ws.send(json.dumps({"status": "ok", "retcode": 0, "echo": request["echo"], "data": data}))

        async def authorize(message):
            return "stable"

        async with serve(handler, "127.0.0.1", 0) as server:
            value = outbound()
            value = OutboundMessage(
                value.delivery_id,
                value.attempt_id,
                value.token,
                value.fingerprint,
                value.account_key,
                value.target,
                value.text,
                {**value.configuration, "endpoint": f"ws://127.0.0.1:{server.sockets[0].getsockname()[1]}"},
            )
            adapter = OneBotTransport(Secrets(), authorize)
            result = await adapter.send(value)
            assert result.status == "sent" and result.receipt_id == "0"
            assert len(frames) == 2 and await adapter.close(1)

    asyncio.run(scenario())
