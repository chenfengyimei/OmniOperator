"""V2 development workbench: fresh database and BrowserContext for every scenario."""

import asyncio
import json
import socket
import threading
import time
from contextvars import ContextVar
from pathlib import Path

import pytest

pytestmark = pytest.mark.xdist_group("ui-sensitive")
import uvicorn
from playwright.sync_api import expect

from omni.composition.http import http_app
from omni.composition.instance import open_instance
from omni.domain.contracts import ModelReply


class UiModel:
    async def complete(self, messages, tools, settings):
        return ModelReply("V2 test reply", cost_micro=0)


class ServerInfo(tuple):
    def __new__(cls, url, token, app):
        instance = super().__new__(cls, (url, token))
        instance.app = app
        return instance


@pytest.fixture
def v2_server(tmp_path, request):
    if getattr(request, "param", None) in {"two_agents", "maintenance_erasure"}:

        async def seed_agents():
            instance = await open_instance(tmp_path, capabilities=())
            try:
                if request.param == "two_agents":
                    await instance._database.write(lambda u: u.create_agent("Other"))
                else:
                    agent = (await instance.application.query("agents", ""))[0]["id"]
                    source = await instance.documents.save(agent, "Interrupted erasure", "authorized source")
                    job = await instance._database.write(
                        lambda u: u.document_erasure_begin(agent, source["id"], source["revision"])
                    )
                    await instance._database.write(
                        lambda u: u.document_erasure_finish(agent, job["id"], "fixture_interrupted")
                    )
            finally:
                await instance.close()

    listener = socket.socket()
    listener.bind(("127.0.0.1", 0))
    listener.listen(128)
    port = listener.getsockname()[1]
    from v2_delivery_helpers import FakePort

    credential_backend = _credential_fixture(request)
    app = http_app(
        tmp_path,
        model=UiModel(),
        delivery_ports={"qq": FakePort()} if getattr(request, "param", None) == "delivery" else None,
        credential_backend=credential_backend,
    )
    app.state.credential_fixture = credential_backend
    app.state.slow_person = None
    app.state.slow_write = False
    app.state.slow_configuration = None
    app.state.slow_document = None
    app.state.slow_document_write = False
    app.state.hold_document_index = False
    app.state.document_index_started = threading.Event()
    app.state.document_index_release = threading.Event()
    app.state.slow_delivery_read = False

    @app.middleware("http")
    async def delayed_settings(request, call_next):
        if request.method == "GET" and app.state.slow_delivery_read and request.url.path.endswith("/deliveries"):
            await asyncio.sleep(0.4)
        if (
            request.method == "PUT"
            and app.state.slow_configuration
            and request.url.path.endswith(app.state.slow_configuration)
        ):
            await asyncio.sleep(0.4)
        if (
            request.method == "GET"
            and app.state.slow_document
            and request.url.path.endswith("/documents/" + app.state.slow_document)
        ):
            await asyncio.sleep(0.4)
        if request.method == "PUT" and app.state.slow_document_write and "/documents/" in request.url.path:
            await asyncio.sleep(0.4)
        if request.method == "GET" and request.url.path.endswith("/settings"):
            await asyncio.sleep(0.15)
        if (
            request.method == "GET"
            and app.state.slow_person
            and f"/{app.state.slow_person}/documents/" in request.url.path
        ):
            await asyncio.sleep(0.4)
        if request.method == "PUT" and app.state.slow_write and request.url.path.endswith("/documents/profile"):
            await asyncio.sleep(0.4)
        return await call_next(request)

    server = uvicorn.Server(
        uvicorn.Config(
            app, loop="omni.adapters.platform.event_loop:create_event_loop", log_config=None, access_log=False
        )
    )

    def run_server():
        if getattr(request, "param", None) in {"two_agents", "maintenance_erasure"}:
            asyncio.run(seed_agents())
        server.run(sockets=[listener])

    thread = threading.Thread(target=run_server, daemon=True)
    thread.start()
    try:
        deadline = time.monotonic() + 15
        while not server.started and thread.is_alive() and time.monotonic() < deadline:
            time.sleep(0.02)
        assert server.started, "V2 test server failed to start"
        documents = app.state.runtime.documents
        original_rebuild, original_source = documents.rebuild, documents._source
        rebuilding = ContextVar("ui_document_rebuild", default=False)

        async def delayed_rebuild(*args, **kwargs):
            token = rebuilding.set(True)
            try:
                return await original_rebuild(*args, **kwargs)
            finally:
                rebuilding.reset(token)

        async def delayed_source(*args, **kwargs):
            if rebuilding.get() and app.state.hold_document_index:
                app.state.document_index_started.set()
                async with asyncio.timeout(10):
                    while not app.state.document_index_release.is_set():
                        await asyncio.sleep(0.01)
            return await original_source(*args, **kwargs)

        documents.rebuild, documents._source = delayed_rebuild, delayed_source
        yield ServerInfo(f"http://127.0.0.1:{port}", (tmp_path / "run/owner.token").read_text(), app)
    finally:
        app.state.document_index_release.set()
        server.should_exit = True
        thread.join(15)
        listener.close()
        assert not thread.is_alive(), "V2 server leaked its runtime"


def _credential_fixture(request):
    if getattr(request, "param", None) == "credentials":
        from test_v2_credentials import Backend

        backend = Backend()
        backend.failure = True
        return backend
    return None


@pytest.mark.parametrize("v2_server", ["credentials"], indirect=True)
@pytest.mark.parametrize("width", [390, 760, 1024, 1440])
@pytest.mark.parametrize("theme", ["dark", "light"])
def test_credential_unknown_review_and_save_use_current_api(ui_browser, v2_server, width, theme):
    url, token = v2_server
    context = ui_browser.new_context(viewport={"width": width, "height": 1000})
    try:
        context.add_init_script(f"localStorage.setItem('omni_theme', '{theme}')")
        page = context.new_page()
        errors = []
        page.on("pageerror", lambda exc: errors.append(str(exc)))
        page.goto(url)
        page.locator("#token").fill(token)
        page.get_by_role("button", name="连接实例").click()
        expect(page.locator("#agents option")).to_have_count(1)
        page.get_by_role("button", name="设置", exact=True).click()
        page.locator("#secret-ref").fill("fixture/ui-key")
        page.locator("#secret").fill("isolated-ui-key-value")
        page.get_by_role("button", name="保存至凭据库", exact=True).click()
        expect(page.locator("#error")).to_have_text("Credential store is unavailable")
        expect(page.locator("#secret")).to_have_value("")
        page.get_by_role("button", name="重新载入并清空核查草稿", exact=True).click()
        expect(page.locator("#credential-list option")).to_have_count(2)
        identity = page.locator("#credential-list option").nth(1).get_attribute("value")
        page.locator("#credential-list").select_option(identity)
        expect(page.locator("#credential-state")).to_contain_text("待人工核查")
        expect(page.locator("#credential-history")).to_contain_text('"unknown"')
        page.locator("#credential-evidence").fill("External inspection of the isolated fake store")
        page.get_by_role("button", name="对话", exact=True).click()
        page.get_by_role("button", name="设置", exact=True).click()
        expect(page.locator("#credential-evidence")).to_have_value("External inspection of the isolated fake store")
        page.locator("#credential-decision").select_option("allow_current")
        page.get_by_role("button", name="提交凭据核查", exact=True).click()
        page.get_by_role("dialog").get_by_role("button", name="提交核查", exact=True).click()
        expect(page.locator("#credential-state")).to_contain_text("允许尝试使用")
        expect(page.locator("#credential-history")).to_contain_text('"unknown"')
        expect(page.get_by_role("button", name="提交凭据核查", exact=True)).to_be_disabled()
        v2_server.app.state.credential_fixture.failure = False
        page.locator("#secret").fill("replacement-ui-fixture")
        page.get_by_role("button", name="保存至凭据库", exact=True).click()
        expect(page.locator("#saved")).to_have_text("凭据修改已完成并记录")
        expect(page.locator("#secret")).to_have_value("")
        page.get_by_role("button", name="删除引用对应凭据", exact=True).click()
        page.get_by_role("dialog").get_by_role("button", name="删除凭据", exact=True).click()
        expect(page.locator("#credential-state")).to_contain_text("已撤销使用")
        assert page.evaluate("document.documentElement.scrollWidth <= window.innerWidth")
        screenshot = Path(__file__).resolve().parents[2] / "temp/build/v2-authority-ui" / f"{theme}-{width}.png"
        screenshot.parent.mkdir(parents=True, exist_ok=True)
        page.screenshot(path=str(screenshot), full_page=True)
        assert errors == []
    finally:
        context.close()


@pytest.mark.parametrize("width", [390, 760, 1024, 1440])
@pytest.mark.parametrize("theme", ["dark", "light"])
def test_workbench_chat_memory_and_theme(ui_browser, v2_server, width, theme):
    url, token = v2_server
    context = ui_browser.new_context(viewport={"width": width, "height": 1000})
    try:
        context.add_init_script(f"localStorage.setItem('omni_theme', '{theme}')")
        page = context.new_page()
        errors = []
        page.on("pageerror", lambda exc: errors.append(str(exc)))
        page.goto(url)
        page.locator("#token").fill(token)
        page.get_by_role("button", name="连接实例").click()
        expect(page.locator("#agents option")).to_have_count(1)
        page.get_by_role("button", name="设置", exact=True).click()
        page.locator("#model").fill("fake")
        page.get_by_role("button", name="保存配置", exact=True).click()
        expect(page.locator("#configuration-status")).to_contain_text("修订 2")
        page.get_by_role("button", name="保存设置", exact=True).click()
        expect(page.locator("#saved")).to_contain_text("已保存")
        page.get_by_role("button", name="对话", exact=True).click()
        page.locator("#message").fill("hello")
        page.get_by_role("button", name="发送", exact=True).click()
        expect(page.locator("#messages")).to_contain_text("V2 test reply", timeout=15_000)
        page.get_by_role("button", name="固定保留本次运行", exact=True).click()
        expect(page.locator("#run-retention-status")).to_contain_text("本次运行已固定")
        page.get_by_role("button", name="取消固定本次运行", exact=True).click()
        expect(page.locator("#run-retention-status")).to_contain_text("本次运行未固定")
        page.get_by_role("button", name="记忆", exact=True).click()
        page.locator("#memory-text").fill("V2 test memory")
        page.get_by_role("button", name="保存记忆", exact=True).click()
        expect(page.locator("#memory-list")).to_contain_text("V2 test memory")
        assert page.evaluate("document.documentElement.scrollWidth <= window.innerWidth")
        page.get_by_role("button", name="文件", exact=True).click()
        page.locator("#object-file").set_input_files(
            {"name": "example.txt", "mimeType": "text/plain", "buffer": b"uploaded file"}
        )
        page.get_by_role("button", name="上传文件", exact=True).click()
        expect(page.locator("#object-list")).to_contain_text("已固定")
        page.get_by_role("button", name="取消固定", exact=True).click()
        expect(page.locator("#object-list")).to_contain_text("未固定")
        assert page.evaluate("document.documentElement.scrollWidth <= window.innerWidth")
        page.get_by_role("button", name="人物与档案", exact=True).click()
        expect(page.locator("#person-role")).to_contain_text("所有者")
        page.locator("#profile-content").fill("Owner profile for this Agent")
        page.get_by_role("button", name="保存档案", exact=True).click()
        expect(page.locator("#profile-status")).to_contain_text("修订 1")
        page.locator("#resident-content").fill("Resident note for this Agent")
        page.get_by_role("button", name="保存常驻记忆", exact=True).click()
        expect(page.locator("#resident-status")).to_contain_text("修订 1")
        page.locator("#document-kind").select_option("resident")
        page.locator("#document-object").select_option(index=1)
        page.get_by_role("button", name="导入并保存", exact=True).click()
        expect(page.locator("#resident-status")).to_contain_text("修订 2")
        expect(page.locator("#resident-content")).to_have_value("uploaded file")
        page.get_by_role("button", name="常驻修订", exact=True).click()
        expect(page.locator("#resident-history")).to_contain_text("来源文件")
        page.locator("#profile-content").fill("Retained unsaved draft")
        page.get_by_role("button", name="记忆", exact=True).click()
        page.get_by_role("button", name="人物与档案", exact=True).click()
        expect(page.locator("#profile-content")).to_have_value("Retained unsaved draft")
        page.get_by_role("button", name="保存档案", exact=True).click()
        expect(page.locator("#profile-status")).to_contain_text("修订 2")
        page.get_by_role("button", name="档案修订", exact=True).click()
        expect(page.locator("#profile-history")).to_contain_text("修订 1")
        expect(page.locator("#profile-history")).to_contain_text("修订 2")
        assert page.locator("#erase-person").is_disabled()
        assert page.evaluate("document.documentElement.scrollWidth <= window.innerWidth")
        assert page.locator("html").get_attribute("data-theme") == theme
        assert not errors
        if width in {390, 1440}:
            output = Path(__file__).resolve().parents[2] / "temp/build/v2-ui"
            output.mkdir(parents=True, exist_ok=True)
            page.screenshot(path=str(output / f"people-{theme}-{width}.png"), full_page=True)
        page.get_by_role("button", name="诊断日志", exact=True).click()
        expect(page.locator("#log-list")).to_contain_text("run.succeeded")
        page.locator("#log-level").select_option("ERROR")
        page.get_by_role("button", name="重新查询", exact=True).click()
        expect(page.locator("#log-status")).to_contain_text("当前显示 0 条")
        assert page.locator("#more-logs").is_disabled()
        assert page.evaluate("document.documentElement.scrollWidth <= window.innerWidth")
    finally:
        context.close()


def connect_people(page, url, token):
    page.goto(url)
    page.locator("#token").fill(token)
    page.get_by_role("button", name="连接实例", exact=True).click()
    expect(page.locator("#agents option")).to_have_count(1)
    page.get_by_role("button", name="人物与档案", exact=True).click()
    expect(page.locator("#person-role")).to_contain_text("所有者")


def test_person_draft_conflict_is_kept_until_explicit_reload(ui_browser, v2_server):
    url, token = v2_server
    context = ui_browser.new_context(viewport={"width": 760, "height": 1000})
    try:
        page = context.new_page()
        connect_people(page, url, token)
        page.locator("#profile-content").fill("saved first")
        page.get_by_role("button", name="保存档案", exact=True).click()
        expect(page.locator("#profile-status")).to_contain_text("修订 1")
        agent = page.locator("#agents").input_value()
        person = page.locator("#person-select").input_value()
        headers = {"Authorization": "Bearer " + token}
        target = f"{url}/api/v1/agents/{agent}/people/{person}/documents/profile"
        document = page.request.get(target, headers=headers).json()
        page.locator("#profile-content").fill("unsaved local canary")
        response = page.request.put(
            target,
            headers=headers,
            data={**{k: document[k] for k in ("entry_id", "revision")}, "content": "external saved revision"},
        )
        assert response.status == 200
        page.get_by_role("button", name="保存档案", exact=True).click()
        expect(page.locator("#error")).to_contain_text("当前草稿保留")
        expect(page.locator("#profile-content")).to_have_value("unsaved local canary")
        page.get_by_role("button", name="记忆", exact=True).click()
        page.get_by_role("button", name="人物与档案", exact=True).click()
        expect(page.locator("#profile-content")).to_have_value("unsaved local canary")
        page.get_by_role("button", name="放弃档案草稿并重载", exact=True).click()
        expect(page.locator("#profile-content")).to_have_value("external saved revision")
    finally:
        context.close()


def test_save_reload_and_late_person_response_do_not_overwrite_current_editor(ui_browser, v2_server):
    url, token = v2_server
    context = ui_browser.new_context(viewport={"width": 390, "height": 1000})
    try:
        page = context.new_page()
        connect_people(page, url, token)
        v2_server.app.state.slow_write = True
        page.locator("#profile-content").fill("save before concurrent reload")
        with page.expect_request(
            lambda request: request.method == "PUT" and request.url.endswith("/documents/profile")
        ):
            page.get_by_role("button", name="保存档案", exact=True).click()
        page.get_by_role("button", name="重新载入人物", exact=True).click()
        expect(page.locator("#profile-status")).to_contain_text("修订 1")
        expect(page.locator("#profile-content")).to_have_value("save before concurrent reload")
        owner = page.locator("#person-select").input_value()
        agent = page.locator("#agents").input_value()
        headers = {"Authorization": "Bearer " + token}
        base = f"{url}/api/v1/agents/{agent}/people"
        other = page.request.post(base, headers=headers, data={"name": "Other person"}).json()
        assert (
            page.request.put(
                base + f"/{other['id']}/documents/profile",
                headers=headers,
                data={"entry_id": None, "revision": 0, "content": "other profile"},
            ).status
            == 200
        )
        v2_server.app.state.slow_person = owner
        with page.expect_response(lambda response: f"/{owner}/documents/profile" in response.url):
            with page.expect_request(
                lambda request: request.method == "GET" and f"/{owner}/documents/profile" in request.url
            ):
                page.get_by_role("button", name="重新载入人物", exact=True).click()
            page.locator("#person-select").select_option(other["id"])
            expect(page.locator("#profile-content")).to_have_value("other profile")
        expect(page.locator("#person-role")).to_contain_text("普通人物")
        expect(page.locator("#profile-content")).to_have_value("other profile")
    finally:
        context.close()


def test_person_create_bind_erase_and_saved_document_export(ui_browser, v2_server):
    url, token = v2_server
    context = ui_browser.new_context(viewport={"width": 1024, "height": 1000})
    try:
        page = context.new_page()
        connect_people(page, url, token)
        page.locator("#profile-content").fill("已保存的导出档案")
        page.get_by_role("button", name="保存档案", exact=True).click()
        expect(page.locator("#profile-status")).to_contain_text("修订 1")
        with page.expect_download() as exporting:
            page.get_by_role("button", name="导出已保存档案", exact=True).click()
        assert Path(exporting.value.path()).read_text(encoding="utf-8") == "已保存的导出档案"
        page.locator("#new-person-name").fill("A friend")
        page.get_by_role("button", name="创建人物", exact=True).click()
        expect(page.locator("#person-role")).to_contain_text("普通人物")
        page.locator("#account-channel").fill("qq")
        page.locator("#account-external").fill("123-test")
        page.get_by_role("button", name="关联账号", exact=True).click()
        expect(page.locator("#accounts-list")).to_contain_text("人工记录")
        page.locator("#erase-person-consent").check()
        page.get_by_role("button", name="遗忘此人物", exact=True).click()
        expect(page.locator("#person-role")).to_contain_text("所有者")
        expect(page.locator("#person-select option")).to_have_count(1)
    finally:
        context.close()


@pytest.mark.parametrize("width", [390, 760, 1024, 1440])
@pytest.mark.parametrize("theme", ["dark", "light"])
def test_summary_and_erasure_are_visible_in_the_workbench(ui_browser, v2_server, width, theme):
    url, token = v2_server
    context = ui_browser.new_context(viewport={"width": width, "height": 1000})
    try:
        context.add_init_script(f"localStorage.setItem('omni_theme', '{theme}')")
        page = context.new_page()
        headers = {"Authorization": "Bearer " + token}
        agent = page.request.get(url + "/api/v1/agents", headers=headers).json()["items"][0]["id"]
        base = f"{url}/api/v1/agents/{agent}"
        cfg = page.request.get(base + "/settings", headers=headers).json()
        profile = page.request.get(base + "/configurations/" + cfg["value"]["model_profile_id"], headers=headers).json()
        assert (
            page.request.put(
                base + "/configurations/" + profile["id"],
                headers=headers,
                data={
                    "name": profile["name"],
                    "enabled": True,
                    "revision": profile["revision"],
                    "value": {**profile["value"], "model": "fake"},
                },
            ).status
            == 200
        )
        session = None
        for i in range(22):
            receipt = page.request.post(
                base + "/turns", headers=headers, data={"key": str(i), "session_id": session, "message": f"history {i}"}
            ).json()
            session = receipt["session_id"]
            deadline = time.monotonic() + 15
            while time.monotonic() < deadline:
                checked = page.request.get(base + "/runs/" + receipt["run_id"], headers=headers).json()
                if checked["state"] in {"succeeded", "failed"}:
                    break
                time.sleep(0.02)
            assert checked["state"] == "succeeded", checked
        page.goto(url)
        page.locator("#token").fill(token)
        page.get_by_role("button", name="连接实例", exact=True).click()
        page.locator("#sessions li", has_text="history 0").first.click()
        expect(page.locator("#messages")).to_contain_text("history 21")
        page.locator("#message").fill("continue using the summary")
        page.get_by_role("button", name="发送", exact=True).click()
        expect(page.locator("#context-status")).to_contain_text("已登记持久摘要", timeout=15000)
        page.get_by_role("button", name="查看当前摘要", exact=True).click()
        expect(page.locator("#summary-body")).to_contain_text("已覆盖")
        if width in {390, 1440}:
            output = Path(__file__).resolve().parents[2] / "temp/build/v2-ui"
            output.mkdir(parents=True, exist_ok=True)
            page.screenshot(path=str(output / f"context-{theme}-{width}.png"), full_page=True)
        page.get_by_role("button", name="遗忘消息与派生内容", exact=True).first.click()
        page.get_by_role("dialog").get_by_role("button", name="取消", exact=True).click()
        expect(page.locator("#summary-body")).to_contain_text("已覆盖")
        page.get_by_role("button", name="遗忘消息与派生内容", exact=True).first.click()
        page.get_by_role("dialog").get_by_role("button", name="确认遗忘", exact=True).click()
        expect(page.locator("#context-status")).to_contain_text("此前历史已退出")
        expect(page.locator("#show-summary")).to_be_hidden()
        page.locator("#message").fill("start with new context")
        page.get_by_role("button", name="发送", exact=True).click()
        expect(page.locator("#context-status")).to_contain_text("遗忘后已排除旧历史", timeout=15000)
        assert page.evaluate("document.documentElement.scrollWidth <= window.innerWidth")
    finally:
        context.close()


@pytest.mark.parametrize("width", [390, 760, 1024, 1440])
@pytest.mark.parametrize("theme", ["dark", "light"])
def test_configuration_drafts_import_apply_and_export(ui_browser, v2_server, width, theme):
    url, token = v2_server
    context = ui_browser.new_context(viewport={"width": width, "height": 1000})
    try:
        context.add_init_script(f"localStorage.setItem('omni_theme', '{theme}')")
        page = context.new_page()
        errors = []
        page.on("pageerror", lambda exc: errors.append(str(exc)))
        page.goto(url)
        page.locator("#token").fill(token)
        page.get_by_role("button", name="连接实例", exact=True).click()
        expect(page.locator("#agents option")).to_have_count(1)
        page.get_by_role("button", name="设置", exact=True).click()
        expect(page.locator("#configuration-fields")).to_be_enabled()
        agent = page.locator("#agents").input_value()
        headers = {"Authorization": "Bearer " + token}
        base = f"{url}/api/v1/agents/{agent}"
        model_id = page.locator("#configuration-list").input_value()
        page.locator("#model").fill("configured-model")
        page.locator("#context-tokens").fill("8192")
        page.locator("#output-tokens").fill("512")
        page.get_by_role("button", name="保存配置", exact=True).click()
        expect(page.locator("#configuration-status")).to_contain_text("修订 2")
        page.get_by_role("button", name="查看配置修订", exact=True).click()
        expect(page.locator("#configuration-history")).to_contain_text("configured-model")
        page.locator("#model").fill("local unsaved model")
        external = page.request.get(base + "/configurations/" + model_id, headers=headers).json()
        assert (
            page.request.put(
                base + "/configurations/" + model_id,
                headers=headers,
                data={
                    "name": external["name"],
                    "revision": external["revision"],
                    "enabled": True,
                    "value": {**external["value"], "model": "external saved model"},
                },
            ).status
            == 200
        )
        page.get_by_role("button", name="保存配置", exact=True).click()
        expect(page.locator("#error")).to_contain_text("当前草稿保留")
        expect(page.locator("#model")).to_have_value("local unsaved model")
        with page.expect_download() as exported:
            page.get_by_role("button", name="导出已保存配置", exact=True).click()
        saved = json.loads(Path(exported.value.path()).read_text(encoding="utf-8"))
        assert saved["value"]["model"] == "external saved model"
        page.locator("#configuration-kind").select_option("channel")
        expect(page.locator("#cfg-channel-fields")).to_be_visible()
        page.locator("#cfg-name").fill("QQ draft")
        page.locator("#channel-account").fill("test-account")
        page.locator("#cfg-enabled").check()
        page.get_by_role("button", name="保存配置", exact=True).click()
        expect(page.locator("#configuration-status")).to_contain_text("修订 1")
        expect(page.locator("#configuration-diagnosis")).to_contain_text("尚未接入")
        page.locator("#configuration-kind").select_option("mcp")
        expect(page.locator("#cfg-mcp-fields")).to_be_visible()
        page.locator("#cfg-name").fill("Remote MCP metadata")
        page.locator("#mcp-transport").select_option("streamable_http")
        page.locator("#mcp-endpoint").fill("https://mcp.example.test")
        page.locator("#cfg-enabled").check()
        page.get_by_role("button", name="保存配置", exact=True).click()
        expect(page.locator("#configuration-status")).to_contain_text("修订 1")
        expect(page.locator("#configuration-diagnosis")).to_contain_text("尚未接入")
        page.locator("#configuration-kind").select_option("model")
        expect(page.locator("#model")).to_have_value("local unsaved model")
        page.get_by_role("button", name="放弃配置草稿并重载", exact=True).click()
        expect(page.locator("#model")).to_have_value("external saved model")
        document = {
            "schema": 1,
            "kind": "preset",
            "name": "Imported preset",
            "enabled": True,
            "value": {"content": "new preset persona"},
        }
        page.locator("#configuration-file").set_input_files(
            {"name": "preset.json", "mimeType": "application/json", "buffer": json.dumps(document).encode()}
        )
        page.get_by_role("button", name="导入为草稿", exact=True).click()
        expect(page.locator("#configuration-status")).to_contain_text("尚未保存或应用")
        assert page.request.get(base + "/configurations?kind=preset", headers=headers).json()["items"] == []
        original = page.request.get(base + "/settings", headers=headers).json()
        page.get_by_role("button", name="保存配置", exact=True).click()
        expect(page.locator("#configuration-status")).to_contain_text("修订 1")
        page.get_by_role("button", name="应用已保存预设到设置草稿", exact=True).click()
        expect(page.locator("#persona")).to_have_value("new preset persona")
        assert page.request.get(base + "/settings", headers=headers).json() == original
        page.get_by_role("button", name="保存设置", exact=True).click()
        expect(page.locator("#saved")).to_contain_text("已保存")
        assert page.request.get(base + "/settings", headers=headers).json()["value"]["persona"] == "new preset persona"
        page.get_by_role("button", name="归档配置", exact=True).click()
        page.locator("#ui-dialog .ui-danger").click()
        expect(page.locator("#configuration-status")).to_contain_text("草稿")
        assert page.request.get(base + "/configurations?kind=preset", headers=headers).json()["items"] == []
        assert not errors
        assert page.evaluate("document.documentElement.scrollWidth <= window.innerWidth")
    finally:
        context.close()


@pytest.mark.parametrize("v2_server", ["two_agents"], indirect=True)
def test_configuration_reload_waits_for_pending_write_and_agent_drafts_are_isolated(ui_browser, v2_server):
    url, token = v2_server
    context = ui_browser.new_context(viewport={"width": 760, "height": 1000})
    try:
        page = context.new_page()
        page.goto(url)
        page.locator("#token").fill(token)
        page.get_by_role("button", name="连接实例", exact=True).click()
        expect(page.locator("#agents option")).to_have_count(2)
        page.get_by_role("button", name="设置", exact=True).click()
        expect(page.locator("#configuration-fields")).to_be_enabled()
        agent = page.locator("#agents").input_value()
        headers = {"Authorization": "Bearer " + token}
        base = f"{url}/api/v1/agents/{agent}"
        profile = page.locator("#configuration-list").input_value()
        v2_server.app.state.slow_configuration = profile
        page.locator("#model").fill("saved before reload")
        with page.expect_request(lambda r: r.method == "PUT" and r.url.endswith(profile)):
            page.get_by_role("button", name="保存配置", exact=True).click()
        page.get_by_role("button", name="刷新配置列表", exact=True).click()
        expect(page.locator("#configuration-status")).to_contain_text("修订 2")
        expect(page.locator("#model")).to_have_value("saved before reload")
        v2_server.app.state.slow_configuration = "/settings"
        page.locator("#persona").fill("saved before settings reload")
        with page.expect_request(lambda r: r.method == "PUT" and r.url.endswith("/settings")):
            page.get_by_role("button", name="保存设置", exact=True).click()
        page.get_by_role("button", name="放弃设置草稿并重载", exact=True).click()
        expect(page.locator("#saved")).to_contain_text("修订 2")
        expect(page.locator("#persona")).to_have_value("saved before settings reload")
        page.locator("#persona").fill("agent one unsaved persona")
        page.locator("#model").fill("agent one unsaved model")
        other = next(
            value
            for value in page.locator("#agents option").evaluate_all("items => items.map(item => item.value)")
            if value != agent
        )
        page.locator("#agents").select_option(other)
        expect(page.locator("#model")).to_have_value("")
        expect(page.locator("#persona")).not_to_have_value("agent one unsaved persona")
        page.locator("#agents").select_option(agent)
        expect(page.locator("#model")).to_have_value("agent one unsaved model")
        expect(page.locator("#persona")).to_have_value("agent one unsaved persona")
        assert (
            page.request.get(base + "/settings", headers=headers).json()["value"]["persona"]
            == "saved before settings reload"
        )
    finally:
        context.close()


def test_import_replacement_requires_explicit_draft_choice(ui_browser, v2_server):
    url, token = v2_server
    context = ui_browser.new_context(viewport={"width": 390, "height": 1000})
    try:
        page = context.new_page()
        page.goto(url)
        page.locator("#token").fill(token)
        page.get_by_role("button", name="连接实例", exact=True).click()
        expect(page.locator("#agents option")).to_have_count(1)
        page.get_by_role("button", name="设置", exact=True).click()
        page.locator("#configuration-kind").select_option("preset")
        expect(page.locator("#cfg-preset-fields")).to_be_visible()
        page.locator("#cfg-name").fill("Existing draft")
        page.locator("#preset-content").fill("unsaved canary")
        document = {
            "schema": 1,
            "kind": "preset",
            "name": "Imported",
            "enabled": True,
            "value": {"content": "replacement"},
        }
        page.locator("#configuration-file").set_input_files(
            {"name": "preset.json", "mimeType": "application/json", "buffer": json.dumps(document).encode()}
        )
        page.get_by_role("button", name="导入为草稿", exact=True).click()
        page.locator("#ui-dialog").get_by_role("button", name="取消", exact=True).click()
        expect(page.locator("#preset-content")).to_have_value("unsaved canary")
        page.get_by_role("button", name="导入为草稿", exact=True).click()
        page.locator("#ui-dialog").get_by_role("button", name="替换草稿", exact=True).click()
        expect(page.locator("#preset-content")).to_have_value("replacement")
        agent = page.locator("#agents").input_value()
        assert (
            page.request.get(
                f"{url}/api/v1/agents/{agent}/configurations?kind=preset", headers={"Authorization": "Bearer " + token}
            ).json()["items"]
            == []
        )
    finally:
        context.close()


@pytest.mark.parametrize("width", [390, 760, 1024, 1440])
@pytest.mark.parametrize("theme", ["dark", "light"])
def test_source_documents_revision_draft_import_search_and_erasure(ui_browser, v2_server, width, theme):
    url, token = v2_server
    context = ui_browser.new_context(viewport={"width": width, "height": 1000})
    try:
        context.add_init_script(f"localStorage.setItem('omni_theme', '{theme}')")
        page = context.new_page()
        errors = []
        page.on("pageerror", lambda exc: errors.append(str(exc)))
        page.goto(url)
        page.locator("#token").fill(token)
        page.get_by_role("button", name="连接实例", exact=True).click()
        expect(page.locator("#agents option")).to_have_count(1)
        page.get_by_role("button", name="来源文档", exact=True).click()
        expect(page.locator("#document-fields")).to_be_enabled()
        agent = page.locator("#agents").input_value()
        headers = {"Authorization": "Bearer " + token}
        base = f"{url}/api/v1/agents/{agent}/documents"
        page.locator("#document-title").fill("来源文档测试")
        page.locator("#document-content").fill("第一版 source-original")
        page.get_by_role("button", name="保存来源文档", exact=True).click()
        expect(page.locator("#document-status")).to_contain_text("修订 1")
        identity = page.locator("#document-list").input_value()
        assert page.locator("#document-rebuild-vector").is_disabled()
        page.locator("#document-content").fill("第二版 source-second")
        page.get_by_role("button", name="保存来源文档", exact=True).click()
        expect(page.locator("#document-status")).to_contain_text("修订 2")
        page.get_by_role("button", name="查看来源修订", exact=True).click()
        page.get_by_role("button", name="查看修订 1", exact=True).click()
        expect(page.locator("#document-content")).to_have_value("第一版 source-original")
        assert page.locator("#document-save").is_disabled()
        page.get_by_role("button", name="放弃文档草稿并载入当前版本", exact=True).click()
        expect(page.locator("#document-content")).to_have_value("第二版 source-second")
        page.locator("#document-content").fill("本地未保存草稿")
        assert (
            page.request.put(
                base + "/" + identity,
                headers=headers,
                data={"title": "外部保存", "content": "第三版 source-external", "revision": 2},
            ).status
            == 200
        )
        page.get_by_role("button", name="保存来源文档", exact=True).click()
        expect(page.locator("#error")).to_contain_text("当前草稿保留")
        expect(page.locator("#document-content")).to_have_value("本地未保存草稿")
        with page.expect_download() as exporting:
            page.get_by_role("button", name="导出已保存来源", exact=True).click()
        assert Path(exporting.value.path()).read_text(encoding="utf-8") == "第三版 source-external"
        page.get_by_role("button", name="记忆", exact=True).click()
        page.get_by_role("button", name="来源文档", exact=True).click()
        expect(page.locator("#document-content")).to_have_value("本地未保存草稿")
        page.get_by_role("button", name="放弃文档草稿并载入当前版本", exact=True).click()
        expect(page.locator("#document-content")).to_have_value("第三版 source-external")
        page.locator("#document-import-title").fill("导入来源")
        page.locator("#document-import-file").set_input_files(
            {"name": "source.txt", "mimeType": "text/plain", "buffer": "imported 文档片段".encode()}
        )
        page.get_by_role("button", name="导入文档草稿", exact=True).click()
        expect(page.locator("#document-status")).to_contain_text("保存后才建立")
        assert len(page.request.get(base, headers=headers).json()["items"]) == 1
        page.get_by_role("button", name="保存来源文档", exact=True).click()
        expect(page.locator("#document-status")).to_contain_text("修订 1")
        with page.expect_response(lambda r: r.request.method == "POST" and r.url.endswith("/maintenance")) as queued:
            page.get_by_role("button", name="重建全文索引", exact=True).click()
        assert queued.value.status == 202
        maintenance = queued.value.json()
        assert maintenance["document_id"] == page.locator("#document-list").input_value()
        expect(page.locator("#document-maintenance-status")).to_contain_text("已完成（succeeded）")
        finished = page.request.get(
            f"{url}/api/v1/agents/{agent}/maintenance/{maintenance['id']}", headers=headers
        ).json()
        assert finished["state"] == "succeeded" and finished["result_build_id"]
        expect(page.locator("#document-index-state")).to_contain_text("ready")
        page.locator("#document-query").fill("文档片段")
        page.get_by_role("button", name="检索文档", exact=True).click()
        expect(page.locator("#document-results")).to_contain_text("imported 文档片段")
        expect(page.locator("#document-search-status")).to_contain_text("全文检索")
        assert page.evaluate("document.documentElement.scrollWidth <= window.innerWidth")
        if width in {390, 1440}:
            output = Path(__file__).resolve().parents[2] / "temp/build/v2-documents-ui"
            output.mkdir(parents=True, exist_ok=True)
            page.screenshot(path=str(output / f"documents-{theme}-{width}.png"), full_page=True)
        page.get_by_role("button", name="删除来源与派生内容", exact=True).click()
        page.locator("#ui-dialog").get_by_role("button", name="取消", exact=True).click()
        assert len(page.request.get(base, headers=headers).json()["items"]) == 2
        page.get_by_role("button", name="删除来源与派生内容", exact=True).click()
        page.locator("#ui-dialog .ui-danger").click()
        expect(page.locator("#document-status")).to_contain_text("新文档草稿")
        assert len(page.request.get(base, headers=headers).json()["items"]) == 1
        assert not errors
    finally:
        context.close()


def test_document_reload_waits_for_save_and_late_read_does_not_replace_selection(ui_browser, v2_server):
    url, token = v2_server
    context = ui_browser.new_context(viewport={"width": 760, "height": 1000})
    try:
        page = context.new_page()
        headers = {"Authorization": "Bearer " + token}
        agent = page.request.get(url + "/api/v1/agents", headers=headers).json()["items"][0]["id"]
        base = f"{url}/api/v1/agents/{agent}/documents"
        one = page.request.post(base, headers=headers, data={"title": "One", "content": "first source"}).json()
        two = page.request.post(base, headers=headers, data={"title": "Two", "content": "second source"}).json()
        page.goto(url)
        page.locator("#token").fill(token)
        page.get_by_role("button", name="连接实例", exact=True).click()
        expect(page.locator("#agents option")).to_have_count(1)
        page.get_by_role("button", name="来源文档", exact=True).click()
        page.locator("#document-list").select_option(one["id"])
        expect(page.locator("#document-content")).to_have_value("first source")
        page.locator("#document-content").fill("save before reload")
        v2_server.app.state.slow_document_write = True
        with page.expect_request(lambda r: r.method == "PUT" and r.url.endswith(one["id"])):
            page.get_by_role("button", name="保存来源文档", exact=True).click()
        page.get_by_role("button", name="刷新文档列表", exact=True).click()
        expect(page.locator("#document-status")).to_contain_text("修订 2")
        expect(page.locator("#document-content")).to_have_value("save before reload")
        v2_server.app.state.slow_document = one["id"]
        with page.expect_response(lambda r: r.request.method == "GET" and r.url.endswith(one["id"])):
            with page.expect_request(lambda r: r.method == "GET" and r.url.endswith(one["id"])):
                page.get_by_role("button", name="放弃文档草稿并载入当前版本", exact=True).click()
            page.locator("#document-list").select_option(two["id"])
            expect(page.locator("#document-content")).to_have_value("second source")
        expect(page.locator("#document-content")).to_have_value("second source")
    finally:
        context.close()


@pytest.mark.parametrize("v2_server", ["two_agents"], indirect=True)
def test_source_document_drafts_and_selection_are_isolated_across_agents(ui_browser, v2_server):
    url, token = v2_server
    context = ui_browser.new_context(viewport={"width": 760, "height": 1000})
    try:
        page = context.new_page()
        headers = {"Authorization": "Bearer " + token}
        agents = page.request.get(url + "/api/v1/agents", headers=headers).json()["items"]
        sources = []
        for i, agent in enumerate(agents):
            sources.append(
                page.request.post(
                    f"{url}/api/v1/agents/{agent['id']}/documents",
                    headers=headers,
                    data={"title": f"Source {i}", "content": f"agent {i} source"},
                ).json()
            )
        page.goto(url)
        page.locator("#token").fill(token)
        page.get_by_role("button", name="连接实例", exact=True).click()
        expect(page.locator("#agents option")).to_have_count(2)
        page.get_by_role("button", name="来源文档", exact=True).click()
        page.locator("#document-list").select_option(sources[0]["id"])
        expect(page.locator("#document-content")).to_have_value("agent 0 source")
        page.locator("#document-content").fill("unsaved first agent")
        page.locator("#agents").select_option(agents[1]["id"])
        expect(page.locator("#document-fields")).to_be_enabled()
        expect(page.locator("#document-content")).to_have_value("")
        page.locator("#document-list").select_option(sources[1]["id"])
        expect(page.locator("#document-content")).to_have_value("agent 1 source")
        page.locator("#agents").select_option(agents[0]["id"])
        expect(page.locator("#document-content")).to_have_value("unsaved first agent")
        assert page.locator("#document-list").input_value() == sources[0]["id"]
    finally:
        context.close()


def test_source_document_delete_waits_for_index_rebuild_and_refresh(ui_browser, v2_server):
    url, token = v2_server
    context = ui_browser.new_context(viewport={"width": 1440, "height": 1000})
    try:
        page = context.new_page()
        headers = {"Authorization": "Bearer " + token}
        agent = page.request.get(url + "/api/v1/agents", headers=headers).json()["items"][0]["id"]
        base = f"{url}/api/v1/agents/{agent}/documents"
        source = page.request.post(
            base, headers=headers, data={"title": "Index race", "content": "isolated source"}
        ).json()
        page.goto(url)
        page.locator("#token").fill(token)
        page.get_by_role("button", name="连接实例", exact=True).click()
        expect(page.locator("#agents option")).to_have_count(1)
        page.get_by_role("button", name="来源文档", exact=True).click()
        page.locator("#document-list").select_option(source["id"])
        expect(page.locator("#document-content")).to_have_value("isolated source")
        v2_server.app.state.hold_document_index = True
        with page.expect_response(lambda r: r.request.method == "POST" and r.url.endswith("/maintenance")) as queued:
            page.get_by_role("button", name="重建全文索引", exact=True).click()
        assert queued.value.status == 202
        job = queued.value.json()
        assert v2_server.app.state.document_index_started.wait(10)
        active = page.request.get(f"{url}/api/v1/agents/{agent}/maintenance/{job['id']}", headers=headers).json()
        assert active["state"] == "running" and active["attempt"]["state"] == "running"
        expect(page.locator("#document-maintenance-status")).not_to_contain_text("已完成")
        expect(page.get_by_role("button", name="删除来源与派生内容", exact=True)).to_be_disabled()
        expect(page.get_by_role("button", name="保存来源文档", exact=True)).to_be_disabled()
        v2_server.app.state.document_index_release.set()
        expect(page.locator("#document-maintenance-status")).to_contain_text("已完成（succeeded）")
        finished = page.request.get(f"{url}/api/v1/agents/{agent}/maintenance/{job['id']}", headers=headers).json()
        assert finished["state"] == "succeeded" and finished["result_build_id"]
        expect(page.get_by_role("button", name="删除来源与派生内容", exact=True)).to_be_enabled()
        page.get_by_role("button", name="删除来源与派生内容", exact=True).click()
        page.locator("#ui-dialog .ui-danger").click()
        expect(page.locator("#document-status")).to_contain_text("新文档草稿")
        assert page.request.get(base, headers=headers).json()["items"] == []
    finally:
        v2_server.app.state.document_index_release.set()
        context.close()


@pytest.mark.parametrize("width", [390, 760, 1024, 1440])
@pytest.mark.parametrize("theme", ["dark", "light"])
def test_schedules_drafts_revision_conflict_history_and_archive(ui_browser, v2_server, width, theme):
    url, token = v2_server
    context = ui_browser.new_context(viewport={"width": width, "height": 1000})
    try:
        context.add_init_script(f"localStorage.setItem('omni_theme', '{theme}')")
        page = context.new_page()
        errors = []
        page.on("pageerror", lambda exc: errors.append(str(exc)))
        page.goto(url)
        page.locator("#token").fill(token)
        page.get_by_role("button", name="连接实例", exact=True).click()
        expect(page.locator("#agents option")).to_have_count(1)
        page.get_by_role("button", name="持久调度", exact=True).click()
        expect(page.locator("#schedule-fields")).to_be_enabled()
        assert not page.locator("#schedule-enabled").is_checked()
        page.locator("#schedule-name").fill("离线计划")
        page.locator("#schedule-prompt").fill("原始指令")
        page.get_by_role("button", name="保存计划", exact=True).click()
        expect(page.locator("#schedule-status")).to_contain_text("修订 1")
        identity = page.locator("#schedule-list").input_value()
        agent = page.locator("#agents").input_value()
        base = f"{url}/api/v1/agents/{agent}/schedules/{identity}"
        headers = {"Authorization": "Bearer " + token}
        page.locator("#schedule-prompt").fill("本地未保存指令")
        page.get_by_role("button", name="记忆", exact=True).click()
        page.get_by_role("button", name="持久调度", exact=True).click()
        expect(page.locator("#schedule-prompt")).to_have_value("本地未保存指令")
        row = page.request.get(base, headers=headers).json()
        changed = page.request.put(
            base,
            headers=headers,
            data={
                "name": row["name"],
                "enabled": False,
                "value": {**row["value"], "prompt": "外部修订"},
                "revision": row["revision"],
            },
        )
        assert changed.status == 200
        page.get_by_role("button", name="保存计划", exact=True).click()
        expect(page.locator("#error")).to_contain_text("当前草稿保留")
        expect(page.locator("#schedule-prompt")).to_have_value("本地未保存指令")
        page.get_by_role("button", name="放弃计划草稿并重载", exact=True).click()
        page.get_by_role("button", name="放弃并重载", exact=True).click()
        expect(page.locator("#schedule-prompt")).to_have_value("外部修订")
        page.get_by_role("button", name="查看计划修订", exact=True).click()
        expect(page.locator("#schedule-history")).to_contain_text("修订 2")
        if (width, theme) in {(390, "dark"), (1440, "light")}:
            artifact = (
                Path(__file__).resolve().parents[2] / "temp/build/v2-scheduling-ui" / f"schedules-{theme}-{width}.png"
            )
            artifact.parent.mkdir(parents=True, exist_ok=True)
            page.screenshot(path=str(artifact), full_page=True)
        assert page.evaluate("document.documentElement.scrollWidth <= window.innerWidth")
        page.get_by_role("button", name="归档计划", exact=True).click()
        page.get_by_role("button", name="归档", exact=True).click()
        expect(page.locator("#schedule-list option")).to_have_count(1)
        page.locator("#schedule-archived").check()
        expect(page.locator("#schedule-list option")).to_have_count(2)
        page.locator("#schedule-list").select_option(identity)
        for field in ("schedule-name", "schedule-enabled", "schedule-kind", "schedule-timezone", "schedule-prompt"):
            expect(page.locator("#" + field)).to_be_disabled()
        expect(page.get_by_role("button", name="保存计划", exact=True)).to_be_disabled()
        expect(page.get_by_role("button", name="刷新到期记录", exact=True)).to_be_enabled()
        expect(page.locator("#schedule-status")).to_contain_text("已归档")
        page.get_by_role("button", name="查看计划修订", exact=True).click()
        expect(page.locator("#schedule-history")).to_contain_text("修订 2")
        page.get_by_role("button", name="刷新到期记录", exact=True).click()
        assert not errors
    finally:
        context.close()


@pytest.mark.parametrize("v2_server", ["two_agents"], indirect=True)
def test_schedule_drafts_are_isolated_across_agents(ui_browser, v2_server):
    url, token = v2_server
    context = ui_browser.new_context(viewport={"width": 1024, "height": 1000})
    try:
        page = context.new_page()
        page.goto(url)
        page.locator("#token").fill(token)
        page.get_by_role("button", name="连接实例", exact=True).click()
        expect(page.locator("#agents option")).to_have_count(2)
        agents = page.locator("#agents option").evaluate_all("items=>items.map(i=>i.value)")
        page.get_by_role("button", name="持久调度", exact=True).click()
        expect(page.locator("#schedule-fields")).to_be_enabled()
        page.locator("#schedule-name").fill("第一空间草稿")
        page.locator("#schedule-prompt").fill("私有草稿")
        page.locator("#agents").select_option(agents[1])
        expect(page.locator("#schedule-fields")).to_be_enabled()
        expect(page.locator("#schedule-prompt")).to_have_value("")
        page.locator("#schedule-name").fill("第二空间草稿")
        page.locator("#agents").select_option(agents[0])
        expect(page.locator("#schedule-name")).to_have_value("第一空间草稿")
        expect(page.locator("#schedule-prompt")).to_have_value("私有草稿")
    finally:
        context.close()


@pytest.mark.parametrize("v2_server", ["delivery"], indirect=True)
@pytest.mark.parametrize("width", [390, 760, 1024, 1440])
@pytest.mark.parametrize("theme", ["dark", "light"])
def test_delivery_draft_approval_preview_receipt_and_archive(ui_browser, v2_server, width, theme):
    url, token = v2_server
    context = ui_browser.new_context(viewport={"width": width, "height": 1000})
    try:
        context.add_init_script(f"localStorage.setItem('omni_theme', '{theme}')")
        page = context.new_page()
        errors = []
        page.on("pageerror", lambda exc: errors.append(str(exc)))
        page.goto(url)
        page.locator("#token").fill(token)
        page.get_by_role("button", name="连接实例", exact=True).click()
        expect(page.locator("#agents option")).to_have_count(1)
        page.get_by_role("button", name="设置", exact=True).click()
        page.locator("#model").fill("fake")
        page.get_by_role("button", name="保存配置", exact=True).click()
        expect(page.locator("#configuration-status")).to_contain_text("修订 2")
        agent = page.locator("#agents").input_value()
        base = f"{url}/api/v1/agents/{agent}"
        headers = {"Authorization": "Bearer " + token}
        channel = page.request.post(
            base + "/configurations",
            headers=headers,
            data={
                "kind": "channel",
                "name": "Offline sender",
                "enabled": True,
                "value": {"driver": "qq", "account": "account-one", "credential_ref": "fixture/key"},
            },
        ).json()
        page.get_by_role("button", name="对话", exact=True).click()
        page.locator("#message").fill("generate delivery source")
        page.get_by_role("button", name="发送", exact=True).click()
        expect(page.locator("#messages")).to_contain_text("V2 test reply", timeout=15000)
        page.get_by_role("button", name="建立投递草稿", exact=True).click()
        expect(page.locator("#delivery-fields")).to_be_enabled()
        page.locator("#delivery-channel").select_option(channel["id"])
        page.locator("#delivery-target").fill("fixture:private-target")
        v2_server.app.state.slow_delivery_read = True
        page.get_by_role("button", name="记忆", exact=True).click()
        page.get_by_role("button", name="持久投递", exact=True).click()
        expect(page.locator("#delivery-target")).to_have_value("fixture:private-target")
        page.get_by_role("button", name="建立待审批投递", exact=True).click()
        expect(page.locator("#delivery-status")).to_contain_text("等待审批")
        identity = page.locator("#delivery-list").input_value()
        expect(page.locator("#delivery-preview")).to_contain_text("V2 test reply")
        expect(page.locator("#delivery-preview")).to_contain_text("fixture:private-target")
        page.get_by_role("button", name="批准本次外发", exact=True).click()
        expect(page.get_by_role("dialog")).to_contain_text("V2 test reply")
        page.get_by_role("button", name="批准外发", exact=True).click()
        deadline = time.monotonic() + 10
        while time.monotonic() < deadline:
            row = page.request.get(base + "/deliveries/" + identity, headers=headers).json()
            if row["state"] == "sent":
                break
            time.sleep(0.02)
        assert row["state"] == "sent"
        page.get_by_role("button", name="刷新投递", exact=True).click()
        expect(page.locator("#delivery-status")).to_contain_text("服务方已接收")
        expect(page.locator("#delivery-attempts")).to_contain_text("fixture-receipt")
        if (width, theme) in {(390, "dark"), (1440, "light")}:
            artifact = (
                Path(__file__).resolve().parents[2] / "temp/build/v2-delivery-ui" / f"delivery-{theme}-{width}.png"
            )
            artifact.parent.mkdir(parents=True, exist_ok=True)
            page.screenshot(path=str(artifact), full_page=True)
        assert page.evaluate("document.documentElement.scrollWidth <= window.innerWidth")
        page.get_by_role("button", name="归档投递", exact=True).click()
        page.get_by_role("button", name="归档记录", exact=True).click()
        expect(page.locator("#delivery-list option")).to_have_count(1)
        page.locator("#delivery-archived").check()
        expect(page.locator("#delivery-list option")).to_have_count(2)
        page.locator("#delivery-list").select_option(identity)
        expect(page.locator("#delivery-status")).to_contain_text("已归档")
        expect(page.locator("#delivery-copy")).to_be_enabled()
        assert errors == []
    finally:
        context.close()


@pytest.mark.parametrize("width", [390, 760, 1024, 1440])
@pytest.mark.parametrize("theme", ["dark", "light"])
def test_operational_metrics_and_diagnostic_download(ui_browser, v2_server, width, theme):
    import zipfile

    url, token = v2_server
    context = ui_browser.new_context(viewport={"width": width, "height": 1000}, accept_downloads=True)
    try:
        context.add_init_script(f"localStorage.setItem('omni_theme', '{theme}')")
        page = context.new_page()
        errors = []
        page.on("pageerror", lambda exc: errors.append(str(exc)))
        page.goto(url)
        page.locator("#token").fill(token)
        page.get_by_role("button", name="连接实例", exact=True).click()
        expect(page.locator("#agents option")).to_have_count(1)
        page.get_by_role("button", name="设置", exact=True).click()
        page.locator("#model").fill("fake")
        page.get_by_role("button", name="保存配置", exact=True).click()
        expect(page.locator("#configuration-status")).to_contain_text("修订 2")
        page.get_by_role("button", name="诊断日志", exact=True).click()
        expect(page.locator("#metrics-status")).to_contain_text("实例已就绪")
        page.get_by_role("button", name="刷新运行指标", exact=True).click()
        expect(page.locator("#metrics-status")).to_contain_text("未知模型调用 0")
        page.get_by_text("实例维护审计（最多显示最近读取的 200 条）", exact=True).click()
        page.get_by_role("button", name="读取实例审计", exact=True).click()
        expect(page.locator("#instance-audit")).to_contain_text("暂无实例维护审计")
        page.get_by_role("button", name="开启限时调试", exact=True).click()
        expect(page.locator("#debug-status")).to_contain_text("限时调试已开启")
        page.get_by_role("button", name="对话", exact=True).click()
        page.locator("#message").fill("private-debug-body-canary")
        page.get_by_role("button", name="发送", exact=True).click()
        expect(page.locator("#messages")).to_contain_text("V2 test reply", timeout=15000)
        page.get_by_role("button", name="诊断日志", exact=True).click()
        page.get_by_text("调试记录（最多显示最近 500 条）", exact=True).click()
        deadline = time.monotonic() + 10
        while time.monotonic() < deadline:
            page.get_by_role("button", name="读取调试记录", exact=True).click()
            expect(page.locator("#read-debug")).to_be_enabled()
            if "span.completed" in page.locator("#debug-records").text_content():
                break
        expect(page.locator("#debug-records")).to_contain_text("span.completed")
        assert "private-debug-body-canary" not in page.locator("#debug-records").text_content()
        page.get_by_role("button", name="停止调试", exact=True).click()
        expect(page.locator("#debug-status")).to_contain_text("限时调试已关闭")
        with page.expect_download() as pending:
            page.get_by_role("button", name="导出诊断包", exact=True).click()
        download = pending.value
        assert download.suggested_filename == "omni-diagnostics.zip"
        with zipfile.ZipFile(download.path()) as archive:
            assert set(archive.namelist()) == {"summary.json", "correlations.jsonl", "README.txt"}
            report = json.loads(archive.read("summary.json"))
            assert report["host"]["platform_acceptance"] == "pending"
            assert report["runtime"]["ready"]
        if (width, theme) in {(390, "dark"), (1440, "light")}:
            artifact = (
                Path(__file__).resolve().parents[2] / "temp/build/v2-operations-ui" / f"operations-{theme}-{width}.png"
            )
            artifact.parent.mkdir(parents=True, exist_ok=True)
            page.screenshot(path=str(artifact), full_page=True)
        assert page.evaluate("document.documentElement.scrollWidth <= window.innerWidth")
        assert errors == []
    finally:
        context.close()
