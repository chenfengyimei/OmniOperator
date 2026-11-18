"""Reusable media_ui fixtures, extracted without collecting test cases."""

import socket
import threading
import time
from pathlib import Path

import pytest
import uvicorn
from playwright.sync_api import expect

from omni.composition.http import http_app
from omni_test_support.v2.media import BV, CHANNEL, FakeGateway, FakeProcessor, NoModel, Secrets


class ServerInfo(tuple):
    def __new__(cls, url, token, app):
        instance = super().__new__(cls, (url, token))
        instance.app = app
        return instance


@pytest.fixture
def media_server(tmp_path):
    gateway, processor = FakeGateway(), FakeProcessor()
    app = http_app(
        tmp_path,
        model=NoModel(),
        bilibili_backend=gateway,
        media_processor=processor,
        credential_backend=Secrets(),
    )
    listener = socket.socket()
    listener.bind(("127.0.0.1", 0))
    listener.listen(128)
    port = listener.getsockname()[1]
    server = uvicorn.Server(
        uvicorn.Config(
            app, loop="omni.adapters.platform.runtime.event_loop:create_event_loop", log_config=None, access_log=False
        )
    )

    def run_server():
        server.run(sockets=[listener])

    thread = threading.Thread(target=run_server, daemon=True)
    thread.start()
    try:
        deadline = time.monotonic() + 15
        while not server.started and thread.is_alive() and time.monotonic() < deadline:
            time.sleep(0.02)
        assert server.started, "V2 media test server failed to start"
        yield ServerInfo(f"http://127.0.0.1:{port}", (tmp_path / "run/owner.token").read_text(encoding="utf-8"), app)
    finally:
        server.should_exit = True
        thread.join(15)
        listener.close()
        assert not thread.is_alive(), "V2 media server leaked its runtime"


def wait_job_done(page, base, headers, job_id):
    deadline = time.monotonic() + 15
    while time.monotonic() < deadline:
        row = page.request.get(f"{base}/media/jobs/{job_id}", headers=headers).json()
        if row["state"] not in ("queued", "running"):
            return row
        page.get_by_role("button", name="刷新作业", exact=True).click()
        time.sleep(0.1)
    raise AssertionError(row)


def wait_job_id(page, base, headers, source_id, operation):
    """Poll server truth until the submitted job appears; status text is shared between submits."""
    deadline = time.monotonic() + 10
    while time.monotonic() < deadline:
        items = page.request.get(base + f"/media/sources/{source_id}/jobs", headers=headers).json()["items"]
        found = [row["id"] for row in items if row["operation"] == operation]
        if found:
            return found[0]
        time.sleep(0.1)
    raise AssertionError(f"{operation} job never appeared")


__all__ = [
    "BV",
    "CHANNEL",
    "FakeGateway",
    "FakeProcessor",
    "NoModel",
    "Path",
    "Secrets",
    "ServerInfo",
    "expect",
    "http_app",
    "media_server",
    "pytest",
    "socket",
    "threading",
    "time",
    "uvicorn",
    "wait_job_done",
    "wait_job_id",
]
