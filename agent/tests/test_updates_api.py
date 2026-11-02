"""更新 API 与关于页的配对、维护和真实 Chromium 回归；不访问 Gitee。"""

from __future__ import annotations

import json

import pytest

from test_web import _client
from test_ui_flows import ui
from omni.system.updates.common import read_json, write_json


@pytest.fixture
def client(tmp_path):
    web, runtime = _client(tmp_path)
    calls = []
    runtime.updates.client.candidate = lambda channel: calls.append(channel) or None
    yield web, runtime, calls
    assert runtime.close()["complete"]
    web.close()


def test_reading_version_and_cached_status_never_requests_network(client):
    web, runtime, calls = client
    assert runtime.updates.gate.lease is None
    assert web.get("/api/system/version").json()["version"] == "0.1.0"
    result = web.get("/api/system/updates").json()
    assert result["check"] is None and result["policy"] == {"channel": "stable", "auto_check": True}
    assert calls == []
    assert web.post("/api/system/updates/check").json()["status"] == "no_release"
    assert calls == ["stable"]


def test_policy_is_validated_and_preserves_unrelated_configuration(client):
    web, runtime, _ = client
    result = web.put("/api/system/updates/policy", json={"channel": "development", "auto_check": False})
    assert result.status_code == 200 and result.json() == {"channel": "development", "auto_check": False}
    assert "测试小鱼" in runtime.paths.config.read_text(encoding="utf-8")
    assert web.put("/api/system/updates/policy", json={"channel": "https://evil.invalid"}).status_code == 422
    assert runtime.updates.policy()["channel"] == "development"


def test_updates_require_pairing_and_origin_validation(tmp_path):
    web, runtime = _client(tmp_path, pair=False)
    try:
        for path in ("/api/system/version", "/api/system/updates", "/api/system/updates/operations/" + "a" * 32):
            assert web.get(path).status_code == 401
        assert web.post("/api/system/updates/check").status_code == 401
        web.post("/api/auth/pair", json={"code": web.app.state.web_auth.pairing_code})
        assert web.post("/api/system/updates/check", headers={"Origin": "https://evil.invalid"}).status_code == 403
    finally:
        assert runtime.close()["complete"]


def test_maintenance_blocks_new_writes_but_keeps_status_readable(client):
    web, runtime, _ = client
    runtime.updates.gate.begin({"phase": "draining", "operation": "a" * 32})
    try:
        assert web.get("/api/system/updates").status_code == 200
        assert web.post("/api/sessions", json={}).status_code == 503
        assert web.put("/api/system/updates/policy", json={"channel": "stable"}).status_code == 503
        assert web.get("/api/system/updates/operations/not-an-operation").status_code == 404
    finally:
        runtime.updates.gate.path.unlink()


def test_install_confirmation_and_operation_validation(client):
    web, runtime, _ = client
    assert web.post("/api/system/updates/prepare", json={"candidate_id": "master"}).status_code == 422
    response = web.post("/api/system/updates/install", json={"operation_id": "a" * 32})
    assert response.status_code == 409 and "确认" in response.json()["detail"]
    assert web.get("/api/system/updates/operations/" + "a" * 32).status_code == 404


@pytest.mark.parametrize("width", [390, 1440])
def test_about_shows_versions_and_no_release_without_script_errors(ui, width):
    page, web, runtime, errors = ui
    runtime.updates.client.candidate = lambda channel: None
    version = runtime.updates.version()
    runtime.updates.version = lambda: {**version, "dirty": [" M agent/src/omni/config.py", "?? agent/local-notes.txt"]}
    page.set_viewport_size({"width": width, "height": 900})
    page.goto("http://127.0.0.1/")
    page.locator(".ui-settings" if width < 900 else ".hub-open").click()
    page.locator('.hub-nav [data-go="sec-about"]').click()
    page.locator("#about-version").get_by_text("0.1.0", exact=False).wait_for()
    page.locator("#update-check").click()
    page.locator("#update-status").get_by_text("暂无正式发行版", exact=False).wait_for()
    assert page.locator("#update-blockers").is_visible()
    assert "agent/src/omni/config.py" in page.locator("#update-blockers").inner_text()
    assert "agent/local-notes.txt" in page.locator("#update-blockers").inner_text()
    assert page.locator("#update-prepare").is_disabled()
    assert page.locator("#update-install").is_disabled()
    assert page.evaluate("document.documentElement.scrollWidth <= innerWidth")
    assert not errors


def test_about_prepare_confirmation_and_failure_are_visible(ui):
    page, web, runtime, errors = ui
    identifier = "d" * 32
    version = {
        "version": "0.1.0",
        "installation": "archive",
        "commit": "a" * 40,
        "dirty": [],
        "disk_changed": False,
        "automatic_install": True,
    }
    runtime.updates.version = lambda: version
    write_json(
        runtime.updates.control / "check.json",
        {
            "status": "available",
            "message": "发现可用更新",
            "channel": "stable",
            "candidate": {
                "id": "c" * 32,
                "version": "0.2.0",
                "commit": "b" * 40,
                "notes": "<script>window.pwned=true</script>",
            },
        },
    )
    state = {
        "id": identifier,
        "phase": "ready",
        "message": "准备完成",
        "target": {"version": "0.2.0", "commit": "b" * 40},
    }

    def prepare(_):
        write_json(runtime.updates.control / "operations" / identifier / "state.json", state)
        return state

    installed = []

    def install(operation, *, confirm):
        installed.append((operation, confirm))
        state.update(phase="cancelled", message="任务尚未空闲，本次升级已取消并恢复运行")
        return state

    runtime.updates.prepare = prepare
    runtime.updates.install = install
    page.goto("http://127.0.0.1/")
    page.locator(".hub-open").click()
    page.locator('.hub-nav [data-go="sec-about"]').click()
    page.locator("#update-prepare").click()
    page.locator("#update-install").click()
    page.locator("#update-confirm").wait_for()
    assert installed == []
    page.get_by_role("button", name="确认安装并重启", exact=True).click()
    page.locator("#update-progress").get_by_text("取消", exact=False).wait_for()
    assert installed == [(identifier, True)]
    assert page.evaluate("window.pwned") is None
    assert not errors


def test_probe_startup_only_exposes_update_status(client):
    web, runtime, _ = client
    runtime.updates.probe_mode = True
    assert web.get("/api/system/version").json()["starting"]
    assert web.get("/api/system/updates").status_code == 200
    assert web.get("/api/state").status_code == 503
    assert web.post("/api/sessions", json={}).status_code == 503


def test_about_reconnects_after_restart_and_shows_rollback(ui):
    page, web, runtime, errors = ui
    identifier = "f" * 32
    runtime.updates.version = lambda: {
        "version": "0.1.0",
        "installation": "archive",
        "commit": "a" * 40,
        "dirty": [],
        "disk_changed": False,
        "automatic_install": True,
    }
    write_json(
        runtime.updates.control / "check.json",
        {
            "channel": "stable",
            "status": "available",
            "message": "发现可用更新",
            "candidate": {"id": "c" * 32, "version": "0.2.0", "commit": "b" * 40},
        },
    )
    state = {"id": identifier, "phase": "ready", "target": {"version": "0.2.0", "commit": "b" * 40}}
    runtime.updates.prepare = lambda candidate: state.copy()
    installs = []

    def install(operation, *, confirm):
        installs.append(operation)
        state.update(phase="waiting_idle", message="等待现有任务完成")
        return state.copy()

    runtime.updates.install = install
    attempts = []

    def reconnect(route):
        attempts.append(1)
        if len(attempts) == 1:
            route.fulfill(status=503, content_type="application/json", body=json.dumps({"detail": "restarting"}))
        else:
            route.fulfill(
                status=200,
                content_type="application/json",
                body=json.dumps({**state, "phase": "rolled_back", "message": "已恢复升级前版本"}),
            )

    page.route("**/api/system/updates/operations/*", reconnect)
    page.goto("http://127.0.0.1/")
    page.locator(".hub-open").click()
    page.locator('.hub-nav [data-go="sec-about"]').click()
    page.locator("#update-prepare").click()
    page.locator("#update-install").click()
    page.get_by_role("button", name="确认安装并重启", exact=True).click()
    page.locator("#update-progress").get_by_text("等待重新连接", exact=False).wait_for()
    page.locator("#update-progress").get_by_text("已恢复升级前版本", exact=False).wait_for()
    assert len(attempts) == 2 and installs == [identifier]
    assert not errors
