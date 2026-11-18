"""Reusable event_stream fixtures, extracted without collecting test cases."""

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


class Model:
    async def complete(self, messages, tools, settings):
        return ModelReply("fixture reply", cost_micro=0)


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


__all__ = [
    "BusinessError",
    "CAPACITY",
    "ClientDisconnect",
    "EventStreams",
    "Fixture",
    "LIFETIME_S",
    "Model",
    "ModelReply",
    "RUN",
    "TestClient",
    "asyncio",
    "contextmanager",
    "continuation",
    "http_app",
    "httpx",
    "json",
    "page",
    "pytest",
    "read_frame",
    "server",
    "socket",
    "threading",
    "time",
    "uuid",
    "uvicorn",
]
