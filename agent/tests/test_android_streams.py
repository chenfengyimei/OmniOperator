"""共享采集、慢客户端、释放与被动能力面板；截图与设备均为隔离假件。"""

import asyncio
from io import BytesIO
import json
import threading
import time
from types import SimpleNamespace

from PIL import Image
import pytest

from omni.tools.android.adb import AdbError
from omni.tools.android.stream import AndroidFrameHub
from omni.channels.web_api.android_stream import preview_response


def png():
    stream = BytesIO()
    Image.new("RGB", (64, 128), "#234567").save(stream, format="PNG")
    return stream.getvalue()


def next_frame(subscription, after=0):
    deadline = time.monotonic() + 4
    while time.monotonic() < deadline:
        frame = subscription.next(after, 0.1)
        if frame:
            return frame
        if frame is False:
            pytest.fail("预览提前结束")
    pytest.fail("没有得到预览帧")


def test_two_clients_share_frames_and_limit_is_enforced():
    entered, release = threading.Event(), threading.Event()
    calls = []

    def capture(serial):
        calls.append(serial)
        entered.set()
        assert release.wait(3)
        return png()

    hub = AndroidFrameHub(capture, idle_s=0.2)
    first = hub.subscribe("A")
    assert entered.wait(1)
    second = hub.subscribe("A")
    try:
        with pytest.raises(RuntimeError, match="上限"):
            hub.subscribe("A")
        release.set()
        one = next_frame(first)
        two = next_frame(second)
        assert one.seq == two.seq and one.jpeg == two.jpeg
        assert one.width == 32 and one.height == 64
        assert not hasattr(one, "observation")
        assert hub.status()[0]["clients"] == 2
    finally:
        first.close()
        second.close()
        release.set()
        assert hub.close(3)["complete"]


def test_slow_client_skips_old_frames_and_idle_capture_stops():
    calls = []
    hub = AndroidFrameHub(lambda serial: calls.append(serial) or png(), idle_s=0.2)
    subscription = hub.subscribe("A")
    first = next_frame(subscription)
    deadline = time.monotonic() + 3
    while hub.status()[0]["seq"] < first.seq + 2 and time.monotonic() < deadline:
        time.sleep(0.02)
    latest = next_frame(subscription, first.seq)
    assert latest.seq >= first.seq + 2
    subscription.close()
    count = len(calls)
    deadline = time.monotonic() + 2
    while hub.status()[0]["worker_alive"] and time.monotonic() < deadline:
        time.sleep(0.02)
    assert len(calls) == count and not hub.status()[0]["worker_alive"]
    assert hub.close()["complete"]


def test_preview_busy_skips_frame_instead_of_replaying_action():
    calls = []

    def capture(serial):
        calls.append(serial)
        if len(calls) == 1:
            raise AdbError("busy", code="device_busy")
        return png()

    hub = AndroidFrameHub(capture, idle_s=0.2)
    subscription = hub.subscribe("A")
    try:
        assert next_frame(subscription).seq == 1
        assert hub.status()[0]["error"] == ""
    finally:
        subscription.close()
        assert hub.close()["complete"]


def test_close_reports_blocked_capture_and_wakes_subscriber():
    entered, release = threading.Event(), threading.Event()

    def capture(serial):
        entered.set()
        release.wait(3)
        return png()

    hub = AndroidFrameHub(capture)
    subscription = hub.subscribe("A")
    assert entered.wait(1)
    try:
        result = hub.close(0)
        assert not result["complete"] and result["residual"] == ["A"]
        assert subscription.next(wait_s=0) is False
        with pytest.raises(RuntimeError, match="关闭"):
            hub.subscribe("B")
    finally:
        release.set()
        subscription.close()
        assert hub.close(3)["complete"]


def test_async_stream_disconnect_releases_subscription():
    hub = AndroidFrameHub(lambda serial: png(), idle_s=0.2)
    runtime = SimpleNamespace(
        android=SimpleNamespace(_require_device=lambda serial: serial or "A"), android_streams=hub
    )

    async def exercise():
        response = preview_response(runtime, "A")
        iterator = response.body_iterator
        chunk = await anext(iterator)
        assert b"Content-Type: image/jpeg" in chunk and b"X-Frame-Seq:" in chunk
        await iterator.aclose()
        assert hub.status()[0]["clients"] == 0

    try:
        asyncio.run(exercise())
    finally:
        assert hub.close()["complete"]


def test_runtime_preserves_database_while_capture_is_residual(tmp_path):
    from test_web import _client

    client, runtime = _client(tmp_path)
    entered, release = threading.Event(), threading.Event()

    def capture(serial):
        entered.set()
        release.wait(3)
        return png()

    runtime.android_streams.capture = capture
    subscription = runtime.android_streams.subscribe("A")
    assert entered.wait(1)
    try:
        result = runtime.close(wait_s=0.05)
        assert not result["complete"] and "android_streams" in result["residual"]
        assert runtime.store.list_sessions(1)
    finally:
        release.set()
        subscription.close()
        assert runtime.close(wait_s=3)["complete"]
        client.close()


def test_preview_frames_never_create_action_observations(tmp_path, monkeypatch):
    from test_web import _client

    client, runtime = _client(tmp_path)
    monkeypatch.setattr(runtime.android, "screenshot_bytes", lambda **kwargs: png())
    subscription = runtime.android_streams.subscribe("fixture")
    try:
        frame = next_frame(subscription)
        assert not runtime.android._observations
        result = runtime.android.execute_action("tap_coords", str(frame.seq), session_id=1, x=1, y=1)
        assert result.status == "error" and result.side_effect_state == "none"
    finally:
        subscription.close()
        assert runtime.close()["complete"]
        client.close()


def test_capabilities_are_passive_private_and_authenticated(tmp_path, monkeypatch):
    from test_web import _client

    client, runtime = _client(
        tmp_path,
        config_text="mcp:\n  servers:\n    fake:\n      command: missing.exe\n      args: [PRIVATE-ARG-CANARY]\n",
    )
    monkeypatch.setattr(runtime.android, "devices", lambda: pytest.fail("被动诊断不得访问设备"))
    monkeypatch.setattr(runtime.services, "overview", lambda: pytest.fail("被动诊断不得做端口探测"))
    try:
        row = runtime.command_jobs.store.create(
            session_id=1,
            run_id="test",
            owner="fixture",
            owner_identity="",
            command="PRIVATE-COMMAND-CANARY",
            shell="powershell",
            cwd=str(tmp_path),
            timeout_s=30,
        )
        response = client.get("/api/capabilities")
        assert response.status_code == 200
        data = response.json()
        assert data["read_only"] and data["terminal"]["active"] == 0
        assert runtime.mcp._connections["fake"]._thread is None
        assert not runtime.android_streams.status()
        assert "PRIVATE-" not in response.text
        assert client.get("/api/capabilities", headers={"Host": "evil.example"}).status_code == 403
        client.cookies.clear()
        assert client.get("/api/capabilities").status_code == 401
    finally:
        assert runtime.close()["complete"]
        client.close()


def test_real_browser_capabilities_page_is_passive_and_mobile(tmp_path, monkeypatch):
    from urllib.parse import urlsplit
    from playwright.sync_api import sync_playwright
    from test_web import _client

    client, runtime = _client(tmp_path)
    monkeypatch.setattr(runtime.android, "devices", lambda: pytest.fail("页面加载不应访问手机"))
    try:
        with sync_playwright() as p:
            browser = p.chromium.launch()
            try:
                page = browser.new_page()
                errors = []
                page.on("pageerror", lambda e: errors.append(str(e)))

                def route(r):
                    parsed = urlsplit(r.request.url)
                    response = client.get(parsed.path)
                    r.fulfill(
                        status=response.status_code,
                        body=response.content,
                        headers={"Content-Type": response.headers.get("content-type", "")},
                    )

                page.route("**/*", route)
                page.goto("http://127.0.0.1/?settings=1&section=sec-capabilities")
                page.locator(".card").first.wait_for()
                assert page.locator(".card").count() == 5
                assert page.locator('.hub-nav a[data-go="sec-android"]').is_visible()
                page.set_viewport_size({"width": 390, "height": 844})
                assert page.evaluate("document.documentElement.scrollWidth<=innerWidth")
                page.screenshot(path=str(tmp_path / "capabilities-mobile.png"), full_page=True)
                assert runtime.android_streams.status() == [] and not errors
            finally:
                browser.close()
    finally:
        assert runtime.close()["complete"]
        client.close()
