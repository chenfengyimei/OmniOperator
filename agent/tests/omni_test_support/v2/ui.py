"""Reusable ui fixtures, extracted without collecting test cases."""

import asyncio
import json
import socket
import threading
import time
from contextvars import ContextVar
from pathlib import Path

import pytest
import uvicorn
from playwright.sync_api import expect

from omni.composition.http import http_app
from omni.composition.instance import open_instance
from omni.domain.contracts import ModelReply
from omni_test_support.paths import REPOSITORY_ROOT as REPOSITORY_ROOT


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
    reflection_seed = None
    if getattr(request, "param", None) == "reflection":
        from omni_test_support.v2.reflection import seed_review
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
    from omni_test_support.v2.v2_delivery_helpers import FakePort

    credential_backend = _credential_fixture(request)
    app = http_app(
        tmp_path,
        model=UiModel(),
        delivery_ports={"qq": FakePort()} if getattr(request, "param", None) == "delivery" else None,
        credential_backend=credential_backend,
    )
    app.state.credential_fixture = credential_backend
    app.state.reflection_seed = reflection_seed
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
            app, loop="omni.adapters.platform.runtime.event_loop:create_event_loop", log_config=None, access_log=False
        )
    )

    def run_server():
        if getattr(request, "param", None) == "reflection":
            app.state.reflection_seed = asyncio.run(seed_review(tmp_path))
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
        from omni_test_support.v2.credentials import Backend

        backend = Backend()
        backend.failure = True
        return backend
    return None


def reveal_settings(page):
    """P4 渐进披露：设置页技术项默认收起，操作折叠内字段前先展开全部 details。"""
    page.evaluate("()=>{for (const d of document.querySelectorAll('#settings details')) d.open = true;}")


def connect_people(page, url, token):
    page.goto(url)
    page.locator("#token").fill(token)
    page.get_by_role("button", name="连接实例", exact=True).click()
    expect(page.locator("#agents option")).to_have_count(1)
    page.get_by_role("button", name="人物与档案", exact=True).click()
    expect(page.locator("#person-role")).to_contain_text("所有者")


def delivery_scenario(ui_browser, v2_server, width, theme):
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
        reveal_settings(page)
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
        page.locator('nav[aria-label="工作台"] [data-page="memory"]').click()
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
            artifact = REPOSITORY_ROOT / "temp/build/v2-delivery-ui" / f"delivery-{theme}-{width}.png"
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


test_delivery_draft_approval_preview_receipt_and_archive = delivery_scenario

__all__ = [
    "ContextVar",
    "ModelReply",
    "Path",
    "ServerInfo",
    "UiModel",
    "_credential_fixture",
    "asyncio",
    "connect_people",
    "delivery_scenario",
    "expect",
    "http_app",
    "json",
    "open_instance",
    "reveal_settings",
    "pytest",
    "socket",
    "test_delivery_draft_approval_preview_receipt_and_archive",
    "threading",
    "time",
    "uvicorn",
    "v2_server",
]
