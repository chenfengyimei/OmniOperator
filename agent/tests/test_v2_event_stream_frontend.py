"""Run the browser-independent production SSE parser and connection lifecycle against injected streams."""

import os
import subprocess
import uuid
from pathlib import Path

import httpx
from playwright.sync_api import expect
from test_v2_ui import v2_server as v2_server


def test_event_stream_frontend_protocol_and_connection_ownership():
    root = Path(__file__).resolve().parents[2]
    result = subprocess.run(
        ["node", "--test", "web/tests/v2-event-stream.test.mjs"],
        cwd=root,
        capture_output=True,
        text=True,
        encoding="utf-8",
        timeout=30,
        check=False,
        env={**os.environ, "NODE_OPTIONS": ""},
    )
    assert result.returncode == 0, result.stdout + result.stderr


def test_workbench_receives_other_clients_messages_and_disconnects_on_token_rotation(ui_browser, v2_server):
    url, token = v2_server
    headers = {"Authorization": "Bearer " + token}
    with httpx.Client(base_url=url, headers=headers, timeout=10) as api:
        agent = api.get("/api/v1/agents").json()["items"][0]["id"]
        base = f"/api/v1/agents/{agent}"
        settings = api.get(base + "/settings").json()
        profile = api.get(base + "/configurations/" + settings["value"]["model_profile_id"]).json()
        saved = api.put(
            base + "/configurations/" + profile["id"],
            json={
                "name": profile["name"],
                "enabled": True,
                "revision": profile["revision"],
                "value": {**profile["value"], "model": "fake"},
            },
        )
        assert saved.status_code == 200
        context = ui_browser.new_context()
        try:
            page = context.new_page()
            errors, requests = [], []
            page.on("pageerror", lambda error: errors.append(str(error)))
            page.on(
                "request", lambda request: requests.append(request) if request.url.endswith("/events/stream") else None
            )
            page.goto(url)
            page.locator("#token").fill(token)
            page.get_by_role("button", name="连接实例", exact=True).click()
            expect(page.locator("#agents option")).to_have_count(1)
            expect(page.locator("body")).to_have_attribute("data-connected", "true")
            page.locator("#message").fill("local-message")
            page.get_by_role("button", name="发送", exact=True).click()
            expect(page.locator("#messages")).to_contain_text("V2 test reply", timeout=15000)
            expect(page.locator("#cancel")).to_be_disabled()
            session = api.get(base + "/sessions").json()["items"][0]["id"]
            sent = api.post(
                base + "/turns",
                json={"session_id": session, "key": str(uuid.uuid4()), "message": "other-client-message"},
            )
            assert sent.status_code == 202
            expect(page.locator("#messages")).to_contain_text("other-client-message", timeout=15000)
            expect(page.locator("#messages .msg.assistant")).to_have_count(2, timeout=15000)
            assert requests and all(token not in request.url for request in requests)
            assert all(request.headers.get("authorization") == "Bearer " + token for request in requests)
            assert api.post("/api/v1/owner-token/rotate").status_code == 200
            expect(page.locator("#health")).to_contain_text("令牌已失效", timeout=15000)
            assert page.evaluate("document.body.dataset.connected") is None
            assert errors == []
        finally:
            context.close()
