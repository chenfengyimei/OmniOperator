"""V2 media workbench page: register sources, run jobs, read transcripts and sheets in a browser."""

import socket
import threading
import time
from pathlib import Path

import pytest
import uvicorn
from playwright.sync_api import expect
from test_v2_media import BV, CHANNEL, FakeGateway, FakeProcessor, NoModel, Secrets

from omni.composition.http import http_app


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


@pytest.mark.parametrize("width", [390, 1024])
def test_media_panel_registers_runs_reads_and_deletes(ui_browser, media_server, width):
    url, token = media_server
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
        config = page.request.post(
            base + "/configurations",
            headers=headers,
            data={"kind": "channel", "name": "Bili fixture", "enabled": True, "value": CHANNEL},
        ).json()

        page.get_by_role("button", name="媒体", exact=True).click()
        expect(page.locator("#media-source-list option")).to_have_count(0)
        expect(page.locator("#media-jobs-refresh")).to_be_disabled()
        # An invalid target is rejected by the panel before any request leaves.
        page.locator("#media-config").fill(config["id"])
        page.locator("#media-target").fill("not-a-bv")
        page.get_by_role("button", name="登记媒体来源", exact=True).click()
        expect(page.locator("#error")).to_contain_text("BV 号格式")
        page.locator("#media-target").fill(BV)
        page.get_by_role("button", name="登记媒体来源", exact=True).click()
        expect(page.locator("#media-register-status")).to_contain_text("已登记来源")
        expect(page.locator("#media-source-list option")).to_have_count(1)
        expect(page.locator("#media-source-detail")).to_contain_text(BV)
        expect(page.locator("#media-jobs-refresh")).to_be_enabled()

        # Subtitle job: submit, wait for the terminal state, read the window.
        page.locator("#media-operation").select_option("subtitle")
        page.get_by_role("button", name="提交作业", exact=True).click()
        source_id = page.request.get(base + "/media/sources", headers=headers).json()["items"][0]["id"]
        done = wait_job_done(page, base, headers, wait_job_id(page, base, headers, source_id, "subtitle"))
        assert done["state"] == "done"
        page.get_by_role("button", name="刷新作业", exact=True).click()
        expect(page.locator("#media-job-list option")).to_contain_text("done")

        page.get_by_role("button", name="读取最新转写", exact=True).click()
        expect(page.locator("#media-transcript-window")).to_contain_text("第一行字幕")
        expect(page.locator("#media-window-prev")).to_be_disabled()
        expect(page.locator("#media-window-next")).to_be_disabled()

        # Frames job: the sheet is fetched as a verified object and previewed.
        page.locator("#media-operation").select_option("frames")
        page.locator("#media-frame-count").fill("3")
        page.get_by_role("button", name="提交作业", exact=True).click()
        done = wait_job_done(page, base, headers, wait_job_id(page, base, headers, source_id, "frames"))
        assert done["state"] == "done"
        page.get_by_role("button", name="读取最新帧拼图", exact=True).click()
        expect(page.locator("#media-frames-image")).to_be_visible()
        assert page.locator("#media-frames-image").get_attribute("src").startswith("blob:")
        expect(page.locator("#media-frames-detail")).to_contain_text("3 帧")

        # The local component probe reports the injected fake components.
        page.get_by_role("button", name="检查本地组件", exact=True).click()
        expect(page.locator("#media-processor-status")).to_contain_text("组件齐备")

        # Deleting asks once and cascades the panel state.
        page.get_by_role("button", name="删除所选来源", exact=False).click()
        page.get_by_role("dialog").get_by_role("button", name="删除", exact=True).click()
        expect(page.locator("#media-register-status")).to_contain_text("来源已删除")
        expect(page.locator("#media-source-list option")).to_have_count(0)
        expect(page.locator("#media-jobs-refresh")).to_be_disabled()

        assert page.evaluate("document.documentElement.scrollWidth <= window.innerWidth")
        screenshot = Path(__file__).resolve().parents[2] / "temp/build/v2-media-ui" / f"media-{width}.png"
        screenshot.parent.mkdir(parents=True, exist_ok=True)
        page.screenshot(path=str(screenshot), full_page=True)
        assert errors == []
    finally:
        context.close()
