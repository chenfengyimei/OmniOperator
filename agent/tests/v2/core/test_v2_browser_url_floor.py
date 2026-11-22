"""Browser URL floor: link-local/cloud-metadata requests are refused or aborted, loopback stays usable."""

from __future__ import annotations

import asyncio
import http.server
import threading
from pathlib import Path

import pytest

from omni.adapters.workers.main import Worker, _blocked_host, _blocked_url
from omni.domain.contracts import Effect, Outcome

OK_PAGE = """<!doctype html><html><head><title>floor fixture</title></head><body><div>fixture-ok</div></body></html>"""
IMG_PAGE = """<!doctype html><html><head>
<script src="http://169.254.169.254/blocked.js"></script></head><body><div>fixture-img</div></body></html>"""
ALLOWED_IMG_PAGE = """<!doctype html><html><body><div>fixture-allowed-img</div>
<img src="/pixel" alt="allowed"></body></html>"""


class _Handler(http.server.BaseHTTPRequestHandler):
    hits: dict[str, int] = {}

    def do_GET(self):
        _Handler.hits[self.path] = _Handler.hits.get(self.path, 0) + 1
        if self.path == "/redirect":
            self.send_response(302)
            self.send_header("Location", "http://169.254.169.254/latest/meta-data/")
            self.end_headers()
            return
        if self.path == "/pixel":
            payload, content_type = b"pixel", "image/gif"
        elif self.path == "/img":
            payload, content_type = IMG_PAGE.encode("utf-8"), "text/html; charset=utf-8"
        elif self.path == "/allowed-img":
            payload, content_type = ALLOWED_IMG_PAGE.encode("utf-8"), "text/html; charset=utf-8"
        else:
            payload, content_type = OK_PAGE.encode("utf-8"), "text/html; charset=utf-8"
        self.send_response(200)
        self.send_header("Content-Type", content_type)
        self.send_header("Content-Length", str(len(payload)))
        self.end_headers()
        self.wfile.write(payload)

    def log_message(self, *args):
        pass


@pytest.fixture(scope="module")
def base_url():
    _Handler.hits = {}
    server = http.server.ThreadingHTTPServer(("127.0.0.1", 0), _Handler)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    yield f"http://127.0.0.1:{server.server_address[1]}"
    server.shutdown()
    server.server_close()


def _context(tmp_path: Path) -> dict[str, str]:
    return {"workspace": str(tmp_path), "agent_id": "agent-floor"}


@pytest.mark.parametrize(
    "url",
    [
        "http://169.254.169.254/latest/meta-data/",
        "http://[fe80::1]/",
        "http://metadata.google.internal/",
        "http://100.100.100.200/",
        "http://[fd00:ec2::254]/",
    ],
)
def test_request_floor_blocks_metadata_and_link_local(url):
    assert _blocked_url(url)


def test_request_floor_ignores_non_http_schemes():
    # The route guard leaves data/blob/about alone; browser_navigate still rejects non-HTTP(S)
    # before dispatch, so this floor is not the scheme gate.
    assert not _blocked_url("file:///etc/passwd")
    assert not _blocked_url("data:text/html,<b>x</b>")


def test_request_floor_keeps_loopback_private_and_public_hosts():
    for url in (
        "http://127.0.0.1:8080/",
        "http://localhost:8080/",
        "http://10.0.0.5/",
        "http://192.168.1.20/",
        "https://example.com/path",
        "data:text/html,<b>x</b>",
        "blob:https://example.com/1",
    ):
        assert not _blocked_url(url), url


def test_blocked_host_names_and_ip_literals():
    for host in (
        "169.254.169.254",
        "[fe80::1]",
        "fe80::1%eth0",
        "metadata.google.internal",
        "METADATA.GOOGLE.INTERNAL.",
        "100.100.100.200",
        "fd00:ec2::254",
        "",
    ):
        assert _blocked_host(host), host
    for host in ("127.0.0.1", "10.0.0.5", "example.com", "[::1]"):
        assert not _blocked_host(host), host


@pytest.mark.parametrize(
    "url",
    [
        "http://169.254.169.254/latest/meta-data/",
        "http://[fe80::1]/",
        "http://metadata.google.internal/",
        "http://100.100.100.200/",
    ],
)
def test_navigation_to_metadata_is_rejected_before_dispatch(tmp_path, url):
    async def scenario():
        worker = Worker()
        try:
            result = await worker.invoke("browser_navigate", {"url": url}, _context(tmp_path))
            assert result.status == Outcome.ERROR and result.effect == Effect.NONE
            assert result.error_code == "invalid_capability_request"
        finally:
            await worker.close()

    asyncio.run(scenario())


def test_navigation_to_loopback_fixture_still_works(tmp_path, base_url):
    async def scenario():
        worker = Worker()
        try:
            result = await worker.invoke("browser_navigate", {"url": base_url + "/ok"}, _context(tmp_path))
            assert result.status == Outcome.OK and result.effect == Effect.APPLIED
            assert "fixture-ok" in result.text
            assert result.data["blocked_requests"] == 0
        finally:
            await worker.close()

    asyncio.run(scenario())


def test_metadata_subresource_is_aborted_without_breaking_the_page(tmp_path, base_url):
    async def scenario():
        worker = Worker()
        try:
            result = await worker.invoke("browser_navigate", {"url": base_url + "/img"}, _context(tmp_path))
            assert result.status == Outcome.OK and result.effect == Effect.APPLIED
            assert "fixture-img" in result.text
            assert result.data["blocked_requests"] == 1
        finally:
            await worker.close()

    asyncio.run(scenario())


def test_redirect_to_metadata_is_aborted(tmp_path, base_url):
    async def scenario():
        worker = Worker()
        try:
            result = await worker.invoke("browser_navigate", {"url": base_url + "/redirect"}, _context(tmp_path))
            # The navigation was attempted and then aborted before any metadata body was read.
            assert result.status == Outcome.UNKNOWN and result.effect == Effect.UNKNOWN
            assert result.error_code == "capability_outcome_unknown"
            assert "root:" not in result.text
        finally:
            await worker.close()

    asyncio.run(scenario())


def test_same_origin_subresource_is_still_allowed(tmp_path, base_url):
    async def scenario():
        worker = Worker()
        try:
            result = await worker.invoke("browser_navigate", {"url": base_url + "/allowed-img"}, _context(tmp_path))
            assert result.status == Outcome.OK and result.effect == Effect.APPLIED
            assert result.data["blocked_requests"] == 0
            assert _Handler.hits.get("/pixel") == 1
        finally:
            await worker.close()

    asyncio.run(scenario())
