"""Trusted-plugin owner reviews use live APIs without exposing any HTTP code-execution endpoint."""

import json
import uuid

import pytest
from playwright.sync_api import expect
from test_v2_history_ui import connect
from test_v2_ui import v2_server as v2_server
from v2_extension_helpers import PUBLIC_TOOL, authorization, installation, plugin_source


def headers(server):
    return {"Authorization": "Bearer " + server[1]}


def endpoint(server, agent, suffix=""):
    return f"{server[0]}/api/v1/agents/{agent}/extensions{suffix}"


def register(context, server, agent, source):
    response = context.request.post(
        endpoint(server, agent), headers=headers(server), data=installation(source, str(uuid.uuid4()))
    )
    assert response.status == 201
    return response.json()


def current(context, server, agent, extension):
    response = context.request.get(endpoint(server, agent, "/" + extension["id"]), headers=headers(server))
    assert response.status == 200
    return response.json()


def confirm(page, label):
    expect(page.locator("#ui-dialog")).to_be_visible()
    page.locator("#ui-dialog").get_by_role("button", name=label, exact=True).click()


def select_plugin(page, extension):
    page.locator("#extension-select").select_option(extension["id"])
    expect(page.locator("#extension-details")).to_contain_text(extension["id"])
    expect(page.locator("#extension-refresh-detail")).to_be_enabled()


def owner_choices(page, name=PUBLIC_TOOL):
    page.get_by_role("checkbox", name=f"授权工具 {name}", exact=True).check()
    page.locator("#extension-trust").check()


def hold_replies(page):
    page.evaluate(
        """() => {
          const fetch = window.fetch.bind(window);
          window.__extensionHold = null;
          window.__extensionReplies = [];
          window.fetch = async (url, options = {}) => {
            const filter = window.__extensionHold, path = String(url);
            if (!filter || path !== filter.path || (options.method || 'GET') !== filter.method)
              return fetch(url, options);
            const response = await fetch(url, options), body = await response.text();
            return new Promise(resolve => window.__extensionReplies.push({body, status: response.status, resolve}));
          };
        }"""
    )


def hold(page, agent, suffix, method="GET"):
    page.evaluate(
        "filter => { window.__extensionReplies = []; window.__extensionHold = filter; }",
        {"path": f"/api/v1/agents/{agent}/extensions{suffix}", "method": method},
    )


def release(page):
    page.evaluate(
        """() => {
          window.__extensionHold = null;
          for (const reply of window.__extensionReplies.splice(0))
            reply.resolve(new Response(reply.body, {status:reply.status, headers:{'Content-Type':'application/json'}}));
        }"""
    )


def test_register_manual_trust_cas_and_disable_never_import_plugin(ui_browser, v2_server, tmp_path):
    context = ui_browser.new_context(viewport={"width": 390, "height": 1000})
    try:
        context.add_init_script("localStorage.setItem('omni_theme', 'dark')")
        marker = tmp_path / "plugin-must-not-import"
        source = plugin_source(tmp_path / "source", marker=marker)
        manifest_path = source / "extension.json"
        manifest = json.loads(manifest_path.read_text())
        manifest["tools"].append({**manifest["tools"][0], "name": "second"})
        manifest_path.write_text(json.dumps(manifest), encoding="utf-8")
        page, errors = connect(context, v2_server)
        agent = page.locator("#agents").input_value()
        page.get_by_role("button", name="可信插件", exact=True).click()
        page.locator("#extension-source").fill(str(source))
        page.locator("#extension-install").click()
        expect(page.locator("#ui-dialog")).to_contain_text("不执行插件代码")
        with page.expect_response(
            lambda response: response.request.method == "POST" and response.url == endpoint(v2_server, agent)
        ) as registered:
            confirm(page, "仅登记包")
        assert registered.value.status == 201
        extension = registered.value.json()
        request = registered.value.request.post_data_json
        assert request == {
            "key": str(uuid.UUID(request["key"])),
            "source": str(source),
            "extension_id": None,
            "revision": 0,
        }
        expect(page.locator("#extension-details")).to_contain_text(extension["id"])
        tools = page.locator("#extension-tools input[type=checkbox]")
        expect(tools).to_have_count(2)
        assert tools.evaluate_all("rows => rows.every(row => !row.checked)")
        expect(page.locator("#extension-trust")).not_to_be_checked()
        page.get_by_role("checkbox", name=f"授权工具 {PUBLIC_TOOL}", exact=True).check()
        expect(page.locator("#extension-authorize")).to_be_disabled()
        page.locator("#extension-trust").check()
        page.locator("#extension-authorize").click()
        expect(page.locator("#ui-dialog")).to_contain_text("不是操作系统沙箱")
        expect(page.locator("#ui-dialog")).to_contain_text("逐次人工审批")
        with page.expect_response(
            lambda response: response.request.method == "PUT" and response.url.endswith("/authorization")
        ) as allowed:
            confirm(page, "信任并授权所选工具")
        assert allowed.value.status == 200
        assert allowed.value.request.post_data_json == {**authorization(extension), "tools": [PUBLIC_TOOL]}
        assert not marker.exists()
        expect(page.locator("#extension-tools")).to_contain_text("当前已授权")
        owner_choices(page)
        page.locator("#extension-authorize").click()
        actual = current(context, v2_server, agent, extension)
        revoked = context.request.post(
            endpoint(v2_server, agent, f"/{extension['id']}/disable"),
            headers=headers(v2_server),
            data={"revision": actual["revision"]},
        )
        assert revoked.status == 200
        with page.expect_response(
            lambda response: response.request.method == "PUT" and response.url.endswith("/authorization")
        ) as conflict:
            confirm(page, "信任并授权所选工具")
        assert conflict.value.status == 409
        expect(page.locator("#extension-status")).to_contain_text("旧详情已清空")
        expect(page.locator("#extension-details")).to_have_text("")
        expect(page.locator("#extension-source")).to_have_value(str(source))
        page.locator("#extension-refresh-detail").click()
        expect(page.locator("#extension-details")).to_contain_text("后续调用授权已禁用")
        owner_choices(page)
        page.locator("#extension-authorize").click()
        with page.expect_response(
            lambda response: response.request.method == "PUT" and response.url.endswith("/authorization")
        ) as reallowed:
            confirm(page, "信任并授权所选工具")
        assert reallowed.value.status == 200
        page.locator("#extension-disable").click()
        with page.expect_response(lambda response: response.url.endswith("/disable")) as disabled:
            confirm(page, "禁用并核查回收")
        assert disabled.value.status == 200 and not disabled.value.json()["enabled"]
        expect(page.locator("#extension-status")).to_contain_text("当前未报告待回收宿主")
        assert not marker.exists() and current(context, v2_server, agent, extension)["runtime"]["resident"] == 0
        assert page.evaluate("document.documentElement.scrollWidth <= window.innerWidth")
        assert errors == []
    finally:
        context.close()


@pytest.mark.parametrize("v2_server", ["two_agents"], indirect=True)
def test_late_real_receipts_and_details_keep_current_agent_and_new_path_draft(ui_browser, v2_server, tmp_path):
    context = ui_browser.new_context(viewport={"width": 760, "height": 1000})
    try:
        page, errors = connect(context, v2_server)
        original = page.locator("#agents").input_value()
        other = next(
            agent
            for agent in page.locator("#agents option").evaluate_all("rows => rows.map(row => row.value)")
            if agent != original
        )
        source = plugin_source(tmp_path / "source")
        next_source = plugin_source(tmp_path / "next-source", identity="nextfixture")
        old = register(context, v2_server, original, source)
        newer = register(context, v2_server, other, source)
        page.get_by_role("button", name="可信插件", exact=True).click()
        select_plugin(page, old)
        hold_replies(page)
        hold(page, original, "/" + old["id"])
        page.locator("#extension-refresh-detail").click()
        page.wait_for_function("() => window.__extensionReplies.length === 1")
        page.locator("#agents").select_option(other)
        expect(page.locator("nav")).not_to_have_attribute("inert", "")
        select_plugin(page, newer)
        draft = str(tmp_path / "current-draft")
        page.locator("#extension-source").fill(draft)
        release(page)
        expect(page.locator("#extension-details")).to_contain_text(newer["id"])
        expect(page.locator("#extension-details")).not_to_contain_text(old["id"])
        expect(page.locator("#extension-source")).to_have_value(draft)
        owner_choices(page)
        hold(page, other, f"/{newer['id']}/authorization", "PUT")
        page.locator("#extension-authorize").click()
        confirm(page, "信任并授权所选工具")
        page.wait_for_function("() => window.__extensionReplies.length === 1")
        assert current(context, v2_server, other, newer)["enabled"]
        page.locator("#agents").select_option(original)
        expect(page.locator("nav")).not_to_have_attribute("inert", "")
        expect(page.locator("#extension-details")).to_contain_text(old["id"])
        page.locator("#extension-source").fill("new input after Agent switch")
        release(page)
        expect(page.locator("#extension-source")).to_have_value("new input after Agent switch")
        expect(page.locator("#extension-details")).to_contain_text(old["id"])
        expect(page.locator("#extension-details")).not_to_contain_text(newer["id"])
        page.locator("#extension-new").click()
        page.locator("#extension-source").fill(str(next_source))
        hold(page, original, "", "POST")
        page.locator("#extension-install").click()
        confirm(page, "仅登记包")
        page.wait_for_function("() => window.__extensionReplies.length === 1")
        page.locator("#extension-source").fill("next path while prior registration settles")
        release(page)
        expect(page.locator("#extension-status")).to_contain_text("当前新输入保持不变")
        expect(page.locator("#extension-source")).to_have_value("next path while prior registration settles")
        expect(page.locator("#extension-details")).to_have_text("")
        assert errors == []
    finally:
        context.close()


def test_snapshot_drift_revokes_authority_and_pending_cleanup_is_explicit_instance_total(
    ui_browser, v2_server, tmp_path, monkeypatch
):
    context = ui_browser.new_context(viewport={"width": 1440, "height": 1000})
    try:
        marker = tmp_path / "never-imported"
        source = plugin_source(tmp_path / "source", marker=marker)
        page, errors = connect(context, v2_server)
        agent = page.locator("#agents").input_value()
        extension = register(context, v2_server, agent, source)
        enabled = context.request.put(
            endpoint(v2_server, agent, f"/{extension['id']}/authorization"),
            headers=headers(v2_server),
            data=authorization(extension),
        )
        assert enabled.status == 200 and not marker.exists()
        runtime = v2_server.app.state.runtime.extensions
        health = runtime._hosts.health()
        monkeypatch.setattr(
            runtime._hosts,
            "health",
            lambda *_args: {**health, "resident": 2, "active": 0, "cleanup_pending": 1, "workers": []},
        )
        snapshot = runtime._packages._root / agent / extension["package_sha256"] / "plugin.py"
        snapshot.write_text("raise RuntimeError('snapshot drift canary')", encoding="utf-8")
        page.get_by_role("button", name="可信插件", exact=True).click()
        select_plugin(page, enabled.json())
        expect(page.locator("#extension-details")).to_contain_text("实例插件宿主合计：常驻 2，活跃 0，待回收 1")
        expect(page.locator("#extension-status")).to_contain_text("实例仍有宿主待回收")
        owner_choices(page)
        page.locator("#extension-authorize").click()
        with page.expect_response(
            lambda response: response.request.method == "PUT" and response.url.endswith("/authorization")
        ) as drifted:
            confirm(page, "信任并授权所选工具")
        assert drifted.value.status == 409
        page.locator("#extension-refresh-detail").click()
        expect(page.locator("#extension-details")).to_contain_text("指纹已漂移")
        expect(page.locator("#extension-authorize")).to_be_disabled()
        page.locator("#extension-disable").click()
        with page.expect_response(lambda response: response.url.endswith("/disable")) as disabled:
            confirm(page, "禁用并核查回收")
        assert disabled.value.status == 200
        expect(page.locator("#extension-status")).to_contain_text("实例仍有宿主待回收")
        assert not marker.exists() and snapshot.read_text() == "raise RuntimeError('snapshot drift canary')"
        assert errors == []
    finally:
        context.close()
