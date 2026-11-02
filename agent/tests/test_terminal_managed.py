"""终端 v2 的真实路由与 Chromium 页面，PTY 使用可控假件。"""

import json
import queue
from types import SimpleNamespace
from urllib.parse import urlsplit

from fastapi.testclient import TestClient
import pytest
from playwright.sync_api import sync_playwright
from starlette.websockets import WebSocketDisconnect

from omni.bootstrap import build_runtime
from omni.channels.web import create_app
from omni.channels.terminal import backend


class Pty:
    def __init__(self):
        self.alive, self.exitstatus = True, 0
        self.chunks = queue.Queue()
        self.chunks.put("测试 PowerShell > ")
        self.inputs = []

    def read(self, size):
        return self.chunks.get(timeout=10)

    def write(self, data):
        self.inputs.append(data)
        self.chunks.put(data)

    def setwinsize(self, rows, cols):
        self.size = rows, cols

    def isalive(self):
        return self.alive

    def terminate(self, force):
        self.alive = False
        self.chunks.put("")

    def close(self, force):
        pass


@pytest.fixture
def env(tmp_path, monkeypatch):
    processes = []

    def spawn(*args, **kwargs):
        pty = Pty()
        processes.append(pty)
        return pty

    monkeypatch.setattr(backend, "AVAILABLE", True)
    monkeypatch.setattr(backend, "PtyProcess", SimpleNamespace(spawn=spawn))
    monkeypatch.setattr(backend, "shell_options", lambda: {"powershell": ["fake"]})
    config = tmp_path / "config.yaml"
    config.write_text(json.dumps({"terminal": {"roots": [str(tmp_path)], "buffer_bytes": 16384}}), encoding="utf-8")
    runtime = build_runtime(
        data_dir=tmp_path / "data", config_path=config, skills_root=tmp_path / "skills", start_background_workers=False
    )
    app = create_app(runtime)
    with TestClient(app, base_url="http://127.0.0.1") as client:
        assert client.post("/api/auth/pair", json={"code": app.state.web_auth.pairing_code}).status_code == 200
        yield client, processes


def create(client):
    response = client.post("/api/terminal/sessions", json={})
    assert response.status_code == 200, response.text
    return response.json()


def test_detach_resume_and_typed_keyboard_data(env):
    client, processes = env
    row = create(client)
    path = "/api/terminal/sessions/" + row["id"] + "/ws"
    with client.websocket_connect("ws://127.0.0.1" + path, headers={"origin": "http://127.0.0.1:8000"}) as ws:
        message = ws.receive_json()
        assert "PowerShell" in message["data"]
        ws.send_json({"v": 2, "type": "input", "data": '{"resize":this is keyboard text'})
        output = ws.receive_json()
        assert "keyboard text" in output["data"]
        seq = output["seq"]
    assert processes[0].alive
    import time

    deadline = time.monotonic() + 2
    while client.get("/api/terminal/sessions").json()["sessions"][0]["connected"] and time.monotonic() < deadline:
        time.sleep(0.01)
    assert not client.get("/api/terminal/sessions").json()["sessions"][0]["connected"]
    processes[0].chunks.put("中文后续输出")
    with client.websocket_connect(
        "ws://127.0.0.1" + path + "?after=" + str(seq), headers={"origin": "http://127.0.0.1:8000"}
    ) as ws:
        assert ws.receive_json()["data"] == "中文后续输出"
    assert len(processes[0].inputs) == 1
    assert client.delete("/api/terminal/sessions/" + row["id"]).json()["complete"]


def test_terminal_owner_and_single_writer(env):
    client, _ = env
    row = create(client)
    path = "/api/terminal/sessions/" + row["id"]
    other = TestClient(client.app, base_url="http://127.0.0.1")
    other.post("/api/auth/pair", json={"code": client.app.state.web_auth.pairing_code})
    assert other.get("/api/terminal/sessions").json()["sessions"] == []
    assert other.delete(path).status_code == 404
    with client.websocket_connect("ws://127.0.0.1" + path + "/ws", headers={"origin": "http://127.0.0.1:8000"}):
        with pytest.raises(WebSocketDisconnect) as rejected:
            with client.websocket_connect(
                "ws://127.0.0.1" + path + "/ws", headers={"origin": "http://127.0.0.1:8000"}
            ) as ws:
                ws.receive_json()
        assert rejected.value.code == 1008
    other.close()


def test_directory_shell_dimensions_and_session_limit(env, tmp_path):
    client, processes = env
    assert client.post("/api/terminal/sessions", json={"cwd": str(tmp_path.parent)}).status_code == 409
    assert client.post("/api/terminal/sessions", json={"shell": "arbitrary.exe"}).status_code == 422
    assert client.post("/api/terminal/sessions", json={"cols": 100000}).status_code == 422
    for _ in range(4):
        create(client)
    assert client.post("/api/terminal/sessions", json={}).status_code == 409
    assert len(processes) == 4


def test_buffer_gap_and_rename(env):
    client, processes = env
    row = create(client)
    item = client.app.state.terminal_manager.get(
        next(iter(client.app.state.terminal_manager._managed.values())).owner, row["id"]
    )
    for _ in range(8):
        processes[0].chunks.put("x" * 4096)
    import time

    deadline = time.monotonic() + 2
    while item.brief()["last_seq"] < 9 and time.monotonic() < deadline:
        time.sleep(0.01)
    gap, rows = item.replay(0)
    assert gap and sum(len(text.encode()) for _, text in rows) <= 16384
    assert client.patch("/api/terminal/sessions/" + row["id"], json={"label": "中文终端"}).json()["label"] == "中文终端"


def test_real_browser_terminal_tabs_reload_and_mobile(env, tmp_path):
    client, _ = env
    with sync_playwright() as p:
        browser = p.chromium.launch()
        try:
            page = browser.new_page()
            errors = []
            page.on("pageerror", lambda e: errors.append(str(e)))

            def route(r):
                url = urlsplit(r.request.url)
                response = client.request(
                    r.request.method,
                    url.path,
                    content=r.request.post_data,
                    headers={"Content-Type": "application/json"},
                )
                r.fulfill(
                    status=response.status_code,
                    body=response.content,
                    headers={"Content-Type": response.headers.get("content-type", "")},
                )

            page.route("**/*", route)
            page.add_init_script(
                """window.WebSocket=class {constructor(){this.readyState=1;setTimeout(()=>this.onopen?.(),10)}send(data){const m=JSON.parse(data);if(m.type==='input')this.onmessage?.({data:JSON.stringify({type:'output',seq:1,data:m.data})})}close(){this.readyState=3;this.onclose?.()}}"""
            )
            page.goto("http://127.0.0.1/terminal/?embed=1")
            page.locator("#shell option").wait_for(state="attached")
            page.get_by_role("button", name="新建终端").click()
            page.locator("#tabs button").wait_for()
            page.get_by_role("button", name="新建终端").click()
            page.wait_for_function('document.querySelectorAll("#tabs button").length===2')
            page.locator("#tabs button").first.click()
            page.reload()
            page.wait_for_function('document.querySelectorAll("#tabs button").length===2')
            page.set_viewport_size({"width": 390, "height": 844})
            assert page.evaluate("document.documentElement.scrollWidth<=innerWidth")
            page.screenshot(path=str(tmp_path / "terminal-mobile.png"), full_page=True)
            assert not errors
        finally:
            browser.close()


def test_invalid_resize_disconnects_without_replaying_input(env):
    client, processes = env
    row = create(client)
    with client.websocket_connect(
        "ws://127.0.0.1/api/terminal/sessions/" + row["id"] + "/ws", headers={"origin": "http://127.0.0.1"}
    ) as ws:
        ws.receive_json()
        ws.send_json({"v": 2, "type": "resize", "cols": 999999, "rows": 30})
        assert ws.receive_json()["type"] == "error"
        with pytest.raises(WebSocketDisconnect):
            ws.receive_json()
    assert not hasattr(processes[0], "size") and not processes[0].inputs


def test_idle_detached_session_is_reclaimed(env):
    import time

    client, processes = env
    row = create(client)
    host = client.app.state.terminal_manager
    item = host._managed[row["id"]]
    item.detached_at = time.monotonic() - host._detach_ttl - 1
    deadline = time.monotonic() + 3
    while processes[0].alive and time.monotonic() < deadline:
        time.sleep(0.01)
    assert not processes[0].alive
