"""History notifications resume durably, revoke promptly and release actual bounded HTTP ownership."""

import asyncio
import json
import socket
import threading
import time
import uuid
from contextlib import contextmanager

import httpx
import pytest
import uvicorn
from fastapi.testclient import TestClient
from starlette.requests import ClientDisconnect

from omni.adapters.http_events import CAPACITY, LIFETIME_S, EventStreams, continuation
from omni.composition.http import http_app
from omni.domain.contracts import BusinessError, ModelReply

RUN = str(uuid.uuid4())


def page(position, *, more=False, run=RUN):
    return {
        "items": [{"run_id": run, "data": {"private": "not-a-stream-payload"}}] if position else [],
        "next_cursor": f"cursor_{position}",
        "has_more": more,
        "history_trimmed": False,
    }


class Fixture:
    def __init__(self):
        self.now = 0
        self.authorized = True
        self.closing = self.disconnected = False
        self.reads = []
        self.sleeps = []
        self.next_page = page(1)
        self.streams = EventStreams(clock=lambda: self.now, wait=self.wait)

    async def wait(self, seconds):
        self.sleeps.append(seconds)
        self.now += seconds
        await asyncio.sleep(0)

    async def auth(self):
        return self.authorized

    async def gone(self):
        return self.disconnected

    async def history(self, cursor):
        self.reads.append(cursor)
        if isinstance(self.next_page, BaseException):
            raise self.next_page
        return self.next_page if cursor != self.next_page["next_cursor"] else page(0) | {"next_cursor": cursor}

    async def open(self, cursor=""):
        return await self.streams.open(self.history, self.auth, lambda: self.closing, self.gone, cursor)


def test_resume_accepts_one_exact_history_cursor_and_rejects_ambiguous_headers():
    assert continuation("", "cursor_1") == "cursor_1"
    assert continuation("cursor_1", "cursor_1") == "cursor_1"
    for supplied in [("cursor_1", "cursor_2"), ("a\nb", ""), ("", "x" * 1025), ("", "bad=cursor")]:
        with pytest.raises(BusinessError) as failure:
            continuation(*supplied)
        assert failure.value.code == "invalid_history_cursor" and failure.value.status == 422


def test_finite_stream_emits_only_invalidation_metadata_and_heartbeats_without_replaying_empty_pages():
    async def scenario():
        fixture = Fixture()
        response = await fixture.open()
        assert fixture.streams.active == 1
        frames = [value async for value in response.body_iterator]
        history = [value for value in frames if b"event: history" in value]
        assert len(history) == 1 and b"id: cursor_1\n" in history[0]
        assert json.loads(history[0].split(b"data: ")[1]) == {
            "count": 1,
            "run_ids": [RUN],
            "has_more": False,
            "history_trimmed": False,
        }
        assert b"not-a-stream-payload" not in b"".join(frames)
        assert b": keepalive\n\n" in frames and b"event: end" in frames[-1]
        assert fixture.now == LIFETIME_S and fixture.streams.active == 0
        assert len(fixture.reads) <= LIFETIME_S / 0.5 + 1
        assert response.headers["cache-control"] == "no-store"
        assert response.headers["content-type"].startswith("text/event-stream")

    asyncio.run(scenario())


def test_catchup_preserves_each_page_cursor_and_yields_before_next_database_read():
    async def scenario():
        fixture = Fixture()
        fixture.next_page = page(1, more=True)
        response = await fixture.open()
        first = await anext(response.body_iterator)
        fixture.next_page = page(2, more=True)
        second = await anext(response.body_iterator)
        fixture.next_page = page(3)
        third = await anext(response.body_iterator)
        assert [
            b"id: cursor_" + str(i).encode() + b"\n" in value for i, value in enumerate([first, second, third], 1)
        ] == [True] * 3
        assert fixture.reads == ["", "cursor_1", "cursor_2"] and fixture.sleeps == [0, 0]
        await response.body_iterator.aclose()
        assert fixture.streams.active == 0

    asyncio.run(scenario())


@pytest.mark.parametrize(
    "changed,event", [("authorized", "unauthenticated"), ("closing", "closing"), ("disconnected", None)]
)
def test_revoke_shutdown_or_disconnect_between_pages_cannot_publish_another_notification(changed, event):
    async def scenario():
        fixture = Fixture()
        response = await fixture.open()
        assert b"event: history" in await anext(response.body_iterator)
        setattr(fixture, changed, changed != "authorized")
        frames = [value async for value in response.body_iterator]
        assert frames == [] if event is None else len(frames) == 1 and f"event: {event}".encode() in frames[0]
        assert fixture.reads == [""] and fixture.streams.active == 0

    asyncio.run(scenario())


def test_authentication_is_rechecked_after_initial_database_read_before_any_payload_is_published():
    async def scenario():
        fixture = Fixture()

        async def history(cursor):
            fixture.authorized = False
            return page(1)

        response = await fixture.streams.open(history, fixture.auth, lambda: False, fixture.gone, "")
        frames = [value async for value in response.body_iterator]
        assert len(frames) == 1 and b"event: unauthenticated" in frames[0]
        assert b"cursor_1" not in frames[0] and fixture.streams.active == 0

    asyncio.run(scenario())


def test_trim_during_stream_requires_explicit_reset_and_redacts_unrelated_business_errors():
    async def scenario(code, expected):
        fixture = Fixture()
        response = await fixture.open()
        await anext(response.body_iterator)
        fixture.next_page = BusinessError(code, "private-error-text", status=410)
        frames = [value async for value in response.body_iterator]
        assert len(frames) == 1 and f"event: {expected}".encode() in frames[0]
        assert b"private-error-text" not in frames[0] and b"id:" not in frames[0]
        assert fixture.streams.active == 0

    for code, event in [("history_cursor_expired", "reset"), ("not_found", "unavailable")]:
        asyncio.run(scenario(code, event))


def test_capacity_and_failure_before_body_start_release_the_real_response_lease():
    async def scenario():
        fixture = Fixture()
        responses = [await fixture.open() for _ in range(CAPACITY)]
        with pytest.raises(BusinessError) as failure:
            await fixture.open()
        assert failure.value.status == 429 and failure.value.retryable

        async def disconnected_send(message):
            raise OSError("closed client")

        async def receive():
            return {"type": "http.disconnect"}

        for response in responses:
            with pytest.raises(ClientDisconnect):
                await response({"type": "http", "asgi": {"spec_version": "2.4"}}, receive, disconnected_send)
        assert fixture.streams.active == 0
        fixture.next_page = BusinessError("history_scope_mismatch", "wrong Agent", status=422)
        with pytest.raises(BusinessError):
            await fixture.open()
        assert fixture.streams.active == 0

    asyncio.run(scenario())


def test_unexpected_source_error_terminates_with_content_free_metadata_and_releases_ownership(caplog):
    async def scenario():
        fixture = Fixture()
        response = await fixture.open()
        await anext(response.body_iterator)
        fixture.next_page = RuntimeError("private-source-error-canary")
        frames = [value async for value in response.body_iterator]
        assert len(frames) == 1 and b"event: unavailable" in frames[0]
        assert b"private-source-error-canary" not in frames[0] and fixture.streams.active == 0

    asyncio.run(scenario())
    assert "Event stream failed" in caplog.text and "private-source-error-canary" not in caplog.text


def test_cancelling_an_inflight_history_read_releases_the_stream_without_background_polling():
    async def scenario():
        fixture = Fixture()
        entered = asyncio.Event()

        async def blocked(cursor):
            if not cursor:
                return page(1)
            entered.set()
            await asyncio.Event().wait()

        response = await fixture.streams.open(blocked, fixture.auth, lambda: False, fixture.gone, "")
        await anext(response.body_iterator)
        task = asyncio.create_task(anext(response.body_iterator))
        await entered.wait()
        assert fixture.streams.active == 1
        task.cancel()
        with pytest.raises(asyncio.CancelledError):
            await task
        assert fixture.streams.active == 0

    asyncio.run(scenario())


class Model:
    async def complete(self, messages, tools, settings):
        return ModelReply("fixture reply", cost_micro=0)


def test_http_stream_uses_normal_auth_and_rejects_foreign_cursors_before_opening(tmp_path):
    with TestClient(http_app(tmp_path, model=Model())) as client:
        route = f"/api/v1/agents/{uuid.uuid4()}/events/stream"
        token = (tmp_path / "run/owner.token").read_text(encoding="utf-8")
        assert client.get(route, params={"token": token}).status_code == 401
        client.headers["Authorization"] = "Bearer " + token
        agent = client.get("/api/v1/agents").json()["items"][0]["id"]
        other = client.portal.call(client.app.state.runtime.application._db.write, lambda u: u.create_agent("Other"))[
            "id"
        ]
        base = f"/api/v1/agents/{agent}"
        turn = client.post(base + "/turns", json={"key": "stream", "message": "fixture"}).json()
        cursor = client.get(base + f"/runs/{turn['run_id']}/events").json()["next_cursor"]
        assert client.get(base + "/events/stream", headers={"Last-Event-ID": cursor}).status_code == 422
        assert client.get(base + "/events/stream", params={"after": 1}).status_code == 422
        assert client.get(base + "/events/stream", headers={"Last-Event-ID": "x" * 1025}).status_code == 422
        assert client.get(f"/api/v1/agents/{other}/events/stream", params={"run_id": turn["run_id"]}).status_code == 404
        assert client.app.state.event_streams.active == 0


@contextmanager
def server(home):
    app = http_app(home, model=Model())
    listener = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    listener.bind(("127.0.0.1", 0))
    port = listener.getsockname()[1]
    native = uvicorn.Server(uvicorn.Config(app, log_level="error", ws="none", lifespan="on"))
    thread = threading.Thread(target=lambda: native.run(sockets=[listener]), name="fixture-v2-event-stream")
    thread.start()
    try:
        deadline = time.monotonic() + 20
        while not native.started and time.monotonic() < deadline:
            assert thread.is_alive()
            time.sleep(0.01)
        assert native.started
        yield app, f"http://127.0.0.1:{port}"
    finally:
        native.should_exit = True
        thread.join(30)
        listener.close()
        assert not thread.is_alive()


def read_frame(lines):
    result = {}
    for line in lines:
        if not line and result:
            return result
        if ": " in line and not line.startswith(":"):
            field, value = line.split(": ", 1)
            result[field] = json.loads(value) if field == "data" else value
    raise AssertionError("stream ended before a complete frame")


def test_real_http_disconnect_resume_and_token_rotation_keep_one_durable_event_source(tmp_path):
    with server(tmp_path) as (app, url), httpx.Client(base_url=url, timeout=10) as client:
        token = (tmp_path / "run/owner.token").read_text(encoding="utf-8")
        client.headers["Authorization"] = "Bearer " + token
        agent = client.get("/api/v1/agents").json()["items"][0]["id"]
        base = f"/api/v1/agents/{agent}"
        first = client.post(base + "/turns", json={"key": "one", "message": "first fixture"}).json()
        with client.stream("GET", base + "/events/stream") as response:
            assert response.status_code == 200
            before = read_frame(response.iter_lines())
            assert before["event"] == "history" and first["run_id"] in before["data"]["run_ids"]
        second = client.post(base + "/turns", json={"key": "two", "message": "second fixture"}).json()
        with client.stream("GET", base + "/events/stream", headers={"Last-Event-ID": before["id"]}) as response:
            lines = response.iter_lines()
            after = read_frame(lines)
            assert after["event"] == "history" and second["run_id"] in after["data"]["run_ids"]
            assert after["id"] != before["id"]
            assert client.post("/api/v1/owner-token/rotate").status_code == 200
            revoked = read_frame(lines)
            assert revoked == {"event": "unauthenticated", "data": {"code": "unauthenticated"}}
        deadline = time.monotonic() + 5
        while app.state.event_streams.active and time.monotonic() < deadline:
            time.sleep(0.01)
        assert app.state.event_streams.active == 0
