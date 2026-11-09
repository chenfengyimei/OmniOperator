"""Unicode configuration/approval/recovery UI in isolated BrowserContexts; no Android access."""

import json
import uuid

import pytest
from playwright.sync_api import expect
from test_v2_history_ui import connect
from test_v2_ui import v2_server as v2_server


@pytest.mark.parametrize("width", [390, 760, 1024, 1440])
@pytest.mark.parametrize("theme", ["light", "dark"])
def test_unicode_profile_draft_approval_and_recovery_show_honest_state(ui_browser, v2_server, width, theme):
    context = ui_browser.new_context(viewport={"width": width, "height": 1100})
    try:
        context.add_init_script(f"localStorage.setItem('omni_theme','{theme}')")
        page, errors = connect(context, v2_server)
        grant, observation, node, environment, action = (str(uuid.uuid4()) for _ in range(5))
        saved = {"revision": 1, "enabled": False, "credential_ref": "", "apk_sha256": "", "certificate_sha256": ""}
        calls = []

        def fulfill(route):
            nonlocal saved
            request = route.request
            body = request.post_data_json if request.post_data else None
            calls.append((request.method, request.url, body))
            if request.url.endswith("/unicode"):
                if request.method == "PUT":
                    assert body["revision"] == saved["revision"]
                    saved = {**body["profile"], "revision": saved["revision"] + 1}
                value = saved
            elif request.method == "GET":
                if "/environments?" in request.url:
                    items = [
                        {
                            "id": environment,
                            "action_id": action,
                            "device_serial": "isolated-phone",
                            "state": "pending",
                            "original_ime": "org.fixture/.Keyboard",
                            "original_subtype": -1,
                            "result_code": "cleanup_observation_unknown",
                            "revision": 2,
                        }
                    ]
                elif "/access?" in request.url:
                    items = []
                else:
                    items = [
                        {
                            "id": grant,
                            "device": {"serial": "isolated-phone", "user_id": 0},
                            "revoked_at": None,
                            "revision": 1,
                        }
                    ]
                value = {"items": items, "next_after": None}
            elif request.method == "DELETE":
                value = {"closed": True}
            elif request.url.endswith("/observe"):
                value = {
                    "observation_id": observation,
                    "expires_in_s": 60,
                    "window": {"unlocked": True, "package": "org.fixture"},
                    "truncated": False,
                    "unicode_helper": {"revision": saved["revision"], "state": "configured_not_probed"},
                    "nodes": [
                        {
                            "node_id": node,
                            "actions": ["append_unicode"],
                            "resource_id": "org.fixture:id/input",
                            "class": "EditText",
                            "text": "label",
                            "description": "",
                        }
                    ],
                }
            elif request.url.endswith("/actions") or request.url.endswith("/restore"):
                value = {"run_id": str(uuid.uuid4())}
            else:
                raise AssertionError(request.url)
            route.fulfill(status=200, content_type="application/json", body=json.dumps(value))

        page.route("**/api/v1/agents/*/android/**", fulfill)
        page.locator('[data-page="android"]').click()
        expect(page.locator("#android-list")).to_have_value(grant)
        page.get_by_text("受控 Unicode 输入辅助组件", exact=True).click()
        page.locator("#android-unicode-reference").fill("fixture/paired-key-reference")
        page.locator('[data-page="people"]').click()
        page.locator('[data-page="android"]').click()
        expect(page.locator("#android-unicode-reference")).to_have_value("fixture/paired-key-reference")
        page.locator("#android-unicode-apk").fill("a" * 64)
        page.locator("#android-unicode-certificate").fill("b" * 64)
        page.locator("#android-unicode-enabled").check()
        page.get_by_role("button", name="保存辅助组件配置", exact=True).click()
        try:
            expect(page.locator("#android-unicode-status")).to_contain_text("已保存修订 2")
        except AssertionError:
            print("UI failure details", errors, page.locator("#error").text_content(), calls)
            raise
        assert saved["credential_ref"] == "fixture/paired-key-reference"
        page.locator("#android-session").fill(str(uuid.uuid4()))
        page.locator("#android-observe").click()
        expect(page.locator("#android-action")).to_be_enabled()
        page.locator("#android-kind").select_option("append_unicode")
        page.locator("#android-text").fill("中文🙂")
        page.locator("#android-action").click()
        expect(page.locator("#android-status")).to_contain_text("尚未执行")
        sent = next(data for method, url, data in calls if method == "POST" and url.endswith("/actions"))
        assert sent["text"] == "中文🙂" and sent["params"] == {"helper_revision": 2, "restore_ime": True}
        page.get_by_role("button", name="准备核查/恢复并请求审批", exact=True).click()
        expect(page.locator("#android-status")).to_contain_text("恢复动作")
        recovery = next(data for method, url, data in calls if method == "POST" and url.endswith("/restore"))
        assert recovery["revision"] == 2 and recovery["consent"] is True and "text" not in recovery
        assert page.locator('#android input[type="password"]').count() == 0
        assert not page.evaluate("document.documentElement.scrollWidth > innerWidth")
        assert not errors
    finally:
        context.close()
