"""Extension workbench artifacts: recent action evidence and bounded text preview, at 390/1024."""

import socket
import threading
import time
from pathlib import Path

import pytest
import uvicorn
from playwright.sync_api import expect
from v2_extension_helpers import PUBLIC_TOOL, PluginModel, authorization, installation, plugin_source

from omni.composition.http import http_app


class ServerInfo(tuple):
    def __new__(cls, url, token, app):
        instance = super().__new__(cls, (url, token))
        instance.app = app
        return instance


@pytest.fixture
def extension_server(tmp_path):
    app = http_app(tmp_path, model=PluginModel())
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
        assert server.started, "V2 extension test server failed to start"
        yield ServerInfo(f"http://127.0.0.1:{port}", (tmp_path / "run/owner.token").read_text(encoding="utf-8"), app)
    finally:
        server.should_exit = True
        thread.join(15)
        listener.close()
        assert not thread.is_alive(), "V2 extension server leaked its runtime"


def wait_run(page, base, headers, run_id, states):
    deadline = time.monotonic() + 20
    while time.monotonic() < deadline:
        run = page.request.get(f"{base}/runs/{run_id}", headers=headers).json()
        if run["state"] in states:
            return run
        time.sleep(0.05)
    raise AssertionError(run)


@pytest.mark.parametrize("width", [390, 1024])
def test_extension_panel_shows_action_artifacts_and_preview(ui_browser, extension_server, tmp_path, width):
    url, token = extension_server
    source = plugin_source(tmp_path / "source", artifact_type="text/plain", artifact_body=b"artifact-preview-text")
    context = ui_browser.new_context(viewport={"width": width, "height": 1000})
    try:
        page = context.new_page()
        errors = []
        page.on("pageerror", lambda exc: errors.append(str(exc)))
        page.goto(url)
        page.locator("#token").fill(token)
        page.get_by_role("button", name="连接实例").click()
        expect(page.locator("#agents option")).to_have_count(1)
        headers = {"Authorization": "Bearer " + token}
        agent = page.request.get(url + "/api/v1/agents", headers=headers).json()["items"][0]["id"]
        base = f"{url}/api/v1/agents/{agent}"
        settings = page.request.get(base + "/settings", headers=headers).json()
        profile = page.request.get(
            base + "/configurations/" + settings["value"]["model_profile_id"], headers=headers
        ).json()
        page.request.put(
            base + "/configurations/" + profile["id"],
            headers=headers,
            data={
                "name": profile["name"],
                "enabled": True,
                "revision": profile["revision"],
                "value": {**profile["value"], "model": "fake"},
            },
        )
        installed = page.request.post(base + "/extensions", headers=headers, data=installation(source)).json()
        enabled = page.request.put(
            base + f"/extensions/{installed['id']}/authorization",
            headers=headers,
            data=authorization(installed),
        ).json()
        assert enabled["enabled"] is True
        receipt = page.request.post(
            base + "/turns", headers=headers, data={"key": "artifact-ui", "message": "use the fixture", "mode": "agent"}
        ).json()
        wait_run(page, base, headers, receipt["run_id"], {"waiting_approval"})
        approval = page.request.get(base + "/approvals", headers=headers).json()["items"][0]
        resolved = page.request.post(
            base + f"/approvals/{approval['id']}",
            headers=headers,
            data={"allowed": True, "policy_revision": approval["policy_revision"]},
        )
        assert resolved.status == 200, resolved.text
        final = wait_run(page, base, headers, receipt["run_id"], {"succeeded", "failed", "interrupted"})
        assert final["state"] == "succeeded", final

        page.get_by_role("button", name="可信插件", exact=True).click()
        expect(page.locator("#extension-select option")).to_have_count(2)
        page.locator("#extension-select").select_option(installed["id"])
        expect(page.locator("#extension-actions")).to_contain_text(PUBLIC_TOOL)
        expect(page.locator("#extension-actions")).to_contain_text("text/plain")
        page.get_by_role("button", name="预览 text/plain", exact=False).click()
        expect(page.locator("#extension-artifact-preview")).to_contain_text("artifact-preview-text")

        assert page.evaluate("document.documentElement.scrollWidth <= window.innerWidth")
        screenshot = Path(__file__).resolve().parents[2] / "temp/build/v2-extension-artifacts-ui" / f"ext-{width}.png"
        screenshot.parent.mkdir(parents=True, exist_ok=True)
        page.screenshot(path=str(screenshot), full_page=True)
        assert errors == []
    finally:
        context.close()
