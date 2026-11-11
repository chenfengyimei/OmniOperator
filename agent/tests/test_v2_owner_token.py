"""Owner token rotation: one-time return, immediate revocation, durable file and no hash leakage."""

import hashlib

from fastapi.testclient import TestClient

from omni.composition.http import http_app


def owner_token(home):
    return (home / "run/owner.token").read_text(encoding="utf-8")


def test_token_sessions_require_auth_and_never_expose_hashes(tmp_path):
    home = tmp_path / "instance"
    with TestClient(http_app(home)) as client:
        assert client.get("/api/v1/owner-token/sessions").status_code == 401
        token = owner_token(home)
        headers = {"Authorization": "Bearer " + token}
        response = client.get("/api/v1/owner-token/sessions", headers=headers)
        assert response.status_code == 200, response.text
        items = response.json()["items"]
        assert len(items) == 1 and items[0]["current"] is True and items[0]["revoked_at"] is None
        digest = hashlib.sha256(token.encode()).hexdigest()
        assert digest not in response.text and token not in response.text
        assert set(items[0]) == {"created_at", "expires_at", "revoked_at", "current"}


def test_rotation_revokes_old_token_updates_file_and_survives_restart(tmp_path):
    home = tmp_path / "instance"
    with TestClient(http_app(home)) as client:
        old = owner_token(home)
        old_headers = {"Authorization": "Bearer " + old}
        rotated = client.post("/api/v1/owner-token/rotate", headers=old_headers)
        assert rotated.status_code == 200, rotated.text
        body = rotated.json()
        new = body["token"]
        assert new != old and len(new) >= 32 and isinstance(body["expires_at"], str)
        assert owner_token(home) == new
        assert client.get("/api/v1/owner-token/sessions", headers=old_headers).status_code == 401
        new_headers = {"Authorization": "Bearer " + new}
        items = client.get("/api/v1/owner-token/sessions", headers=new_headers).json()["items"]
        assert sum(1 for item in items if item["current"]) == 1
        assert any(item["revoked_at"] is not None for item in items)
        assert hashlib.sha256(new.encode()).hexdigest() not in str(items)
        assert client.get("/api/v1/agents", headers=new_headers).status_code == 200

        second = client.post("/api/v1/owner-token/rotate", headers=new_headers).json()["token"]
        assert second not in {old, new} and owner_token(home) == second
        assert client.get("/api/v1/agents", headers=new_headers).status_code == 401

    # A restart reads the rotated control file; the server must not resurrect revoked tokens.
    with TestClient(http_app(home)) as client:
        headers = {"Authorization": "Bearer " + second}
        assert client.get("/api/v1/owner-token/sessions", headers=headers).status_code == 200
        assert client.get("/api/v1/agents", headers=headers).status_code == 200
        stale_headers = {"Authorization": "Bearer " + new}
        assert client.get("/api/v1/agents", headers=stale_headers).status_code == 401


def test_cli_auth_rotate_uses_running_instance_and_prints_the_new_token(tmp_path):
    import socket
    import subprocess
    import sys
    import threading
    import time

    import uvicorn

    home = tmp_path / "instance"
    app = http_app(home)
    listener = socket.socket()
    listener.bind(("127.0.0.1", 0))
    listener.listen(128)
    port = listener.getsockname()[1]
    server = uvicorn.Server(
        uvicorn.Config(
            app, loop="omni.adapters.platform.event_loop:create_event_loop", log_config=None, access_log=False
        )
    )
    thread = threading.Thread(target=lambda: server.run(sockets=[listener]), daemon=True)
    thread.start()
    try:
        deadline = time.monotonic() + 15
        while not server.started and thread.is_alive() and time.monotonic() < deadline:
            time.sleep(0.02)
        assert server.started
        old = owner_token(home)
        done = subprocess.run(
            [
                sys.executable,
                "-m",
                "omni.composition.cli",
                "--home",
                str(home),
                "auth",
                "--rotate",
                "--url",
                f"http://127.0.0.1:{port}",
            ],
            check=False,
            capture_output=True,
            text=True,
            encoding="utf-8",
            timeout=30,
        )
        assert done.returncode == 0, done.stderr
        new = done.stdout.strip()
        assert new != old and len(new) >= 32
        assert owner_token(home) == new
    finally:
        server.should_exit = True
        thread.join(15)
        listener.close()
