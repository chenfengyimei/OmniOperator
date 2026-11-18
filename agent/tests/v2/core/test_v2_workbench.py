"""Explicit browser launch proves the current V2 boot and closes its probe thread without owner credentials."""

from __future__ import annotations

import json
import os
import socket
import threading
from pathlib import Path

import httpx
import psutil
import pytest
import uvicorn

from omni.adapters.platform.runtime import workbench
from omni.adapters.platform.runtime.runtime_identity import canonical_proof
from omni.composition.http import http_app
from omni.domain.contracts import BusinessError


def fixture(monkeypatch, *, ready=True, corruption=None):
    key = b"a" * 32
    identity = {
        "format": "omni-runtime-identity-1",
        "state": "running",
        "instance_id": "00000000-0000-0000-0000-000000000001",
        "boot_id": "00000000-0000-0000-0000-000000000002",
        "pid": os.getpid(),
        "process_created": psutil.Process().create_time(),
        "build_id": "fixture",
        "api_version": 1,
        "schema_version": "v2_0037",
        "service_plan_sha256": None,
    }
    reads, requests, streams = [], [], []

    def read(path, limit=16384):
        reads.append(path.name)
        assert path.name in {"runtime.json", "readiness.key"}
        return key if path.name == "readiness.key" else json.dumps(identity).encode()

    def handle(request):
        requests.append(request)
        assert request.url.path == "/health/identity" and "authorization" not in request.headers
        assert request.headers["Accept-Encoding"] == "identity"
        proof = canonical_proof({**identity, "ready": ready}, request.url.params["challenge"], key)
        if corruption == "challenge":
            proof["challenge"] = "0" * 64
        elif corruption == "signature":
            proof["signature"] = "0" * 64
        elif corruption == "boot":
            proof["identity"]["boot_id"] = "00000000-0000-0000-0000-000000000003"
        elif corruption == "shape":
            proof["extra"] = "untrusted"
        raw = b" " * 65537 if corruption == "oversize" else json.dumps(proof).encode()
        response = httpx.Response(
            200,
            stream=httpx.ByteStream(raw),
            headers={"content-encoding": "gzip"} if corruption == "encoding" else {},
        )
        streams.append(response)
        return response

    monkeypatch.setattr(workbench, "read_private", read)
    return identity, reads, requests, streams, httpx.MockTransport(handle)


@pytest.mark.parametrize("ready", [False, True])
def test_manual_boot_proof_requires_exact_identity_and_readiness_without_reading_owner_token(monkeypatch, ready):
    _, reads, requests, streams, transport = fixture(monkeypatch, ready=ready)
    browser = workbench.WorkbenchBrowser(Path("unused"), "127.0.0.1", 8766)
    with httpx.Client(transport=transport) as client:
        assert browser._ready(client) is ready
    assert (
        reads == ["runtime.json", "readiness.key", "runtime.json"]
        if ready
        else reads
        == [
            "runtime.json",
            "readiness.key",
        ]
    )
    assert len(requests) == 1 and all(response.is_closed for response in streams)


@pytest.mark.parametrize("corruption", ["challenge", "signature", "boot", "shape", "oversize", "encoding"])
def test_bad_boot_proof_never_opens_browser_and_closes_response(monkeypatch, corruption):
    _, _, requests, streams, transport = fixture(monkeypatch, corruption=corruption)
    opened = []
    browser = workbench.WorkbenchBrowser(Path("unused"), "127.0.0.1", 8766, open_url=opened.append)
    with httpx.Client(transport=transport) as client, pytest.raises(BusinessError):
        browser._ready(client)
    assert len(requests) == 1 and not opened
    assert all(response.is_closed for response in streams)


@pytest.mark.parametrize(
    "field,value", [("pid", 0), ("process_created", 0), ("state", "draining"), ("service_plan_sha256", "a" * 64)]
)
def test_another_or_draining_runtime_is_rejected_before_network(monkeypatch, field, value):
    identity, _, requests, _, transport = fixture(monkeypatch)
    identity[field] = value
    browser = workbench.WorkbenchBrowser(Path("unused"), "127.0.0.1", 8766)
    with httpx.Client(transport=transport) as client, pytest.raises(BusinessError) as caught:
        browser._ready(client)
    assert caught.value.code == "workbench_identity_mismatch" and not requests


@pytest.mark.parametrize(
    "host,port", [("fixture.invalid", 8766), ("192.0.2.1", 8766), ("127.0.0.1", 0), ("127.0.0.1", 65536)]
)
def test_open_browser_refuses_nonlocal_or_unfixed_listener(host, port):
    with pytest.raises(BusinessError) as caught:
        workbench.WorkbenchBrowser(Path("unused"), host, port)
    assert caught.value.code == "workbench_listener_invalid" and caught.value.status == 422


@pytest.mark.parametrize("host,authority", [("localhost", "127.0.0.1"), ("0.0.0.0", "127.0.0.1"), ("::", "[::1]")])
def test_listener_aliases_open_only_local_origin(host, authority):
    browser = workbench.WorkbenchBrowser(Path("unused"), host, 8766)
    assert browser._origin == f"http://{authority}:8766"


def test_explicit_browser_thread_opens_once_and_is_joined_when_server_scope_closes(monkeypatch):
    _, reads, requests, streams, transport = fixture(monkeypatch)
    original_client = httpx.Client
    clients, opened, finished = [], [], threading.Event()

    def client(**kwargs):
        assert kwargs == {"trust_env": False, "follow_redirects": False, "timeout": 3}
        value = original_client(transport=transport, **kwargs)
        clients.append(value)
        return value

    def open_url(url):
        opened.append(url)
        finished.set()

    monkeypatch.setattr(workbench.httpx, "Client", client)
    with workbench.WorkbenchBrowser(Path("unused"), "127.0.0.1", 8766, open_url=open_url) as browser:
        assert finished.wait(5)
    assert browser._thread is not None and not browser._thread.is_alive()
    assert opened == ["http://127.0.0.1:8766/"] and len(requests) == 1
    assert all(value.is_closed for value in clients) and all(value.is_closed for value in streams)
    assert "owner.token" not in reads


def test_real_loopback_workbench_boot_proof_and_local_owner_authentication(tmp_path):
    listener = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    listener.bind(("127.0.0.1", 0))
    port = listener.getsockname()[1]
    server = uvicorn.Server(
        uvicorn.Config(
            http_app(tmp_path / "home"),
            host="127.0.0.1",
            port=port,
            loop="omni.adapters.platform.runtime.event_loop:create_event_loop",
            log_config=None,
            access_log=False,
        )
    )
    thread = threading.Thread(target=lambda: server.run(sockets=[listener]), name="fixture-v2-workbench")
    opened, arrived = [], threading.Event()

    def open_url(url):
        opened.append(url)
        arrived.set()

    thread.start()
    try:
        with workbench.WorkbenchBrowser(tmp_path / "home", "127.0.0.1", port, open_url=open_url) as browser:
            assert arrived.wait(20), "the actual local server must publish its own ready boot"
            assert opened == [f"http://127.0.0.1:{port}/"]
            with httpx.Client(base_url=opened[0], trust_env=False, follow_redirects=False) as client:
                assert client.get("/api/v1/agents").status_code == 401
                token = client.get("/api/v1/owner-token/local")
                assert token.status_code == 200 and token.headers["cache-control"] == "no-store"
                response = client.get("/api/v1/agents", headers={"Authorization": "Bearer " + token.json()["token"]})
                assert response.status_code == 200 and len(response.json()["items"]) == 1
        assert browser._thread is not None and not browser._thread.is_alive()
    finally:
        server.should_exit = True
        thread.join(30)
        listener.close()
        assert not thread.is_alive()
    identity = json.loads(workbench.read_private(tmp_path / "home/run/runtime.json"))
    assert identity["state"] == "stopped"
