"""Independent BrowserContexts exercise owner Android UI with fulfilled device endpoints, never actual devices."""

import json
import uuid

import pytest
from playwright.sync_api import expect
from test_v2_android import BOOT, SERIAL
from test_v2_history_ui import connect
from test_v2_ui import v2_server as v2_server


@pytest.mark.parametrize("width", [390, 760, 1024, 1440])
@pytest.mark.parametrize("theme", ["light", "dark"])
def test_android_scope_drafts_observation_clearing_and_approval_preparation(ui_browser, v2_server, width, theme):
    context = ui_browser.new_context(viewport={"width": width, "height": 1100})
    try:
        context.add_init_script(f"localStorage.setItem('omni_theme','{theme}')")
        page, errors = connect(context, v2_server)
        agent = page.locator("#agents").input_value()
        base = v2_server[0] + f"/api/v1/agents/{agent}"
        config = context.request.post(
            base + "/configurations",
            headers={"Authorization": "Bearer " + v2_server[1]},
            data={
                "kind": "channel",
                "name": "Android UI fixture",
                "enabled": True,
                "value": {"driver": "android", "endpoint": "http://127.0.0.1:5037", "account": SERIAL},
            },
        ).json()
        grant_id, observation_id, node_id = (str(uuid.uuid4()) for _ in range(3))
        row = {
            "id": grant_id,
            "revision": 1,
            "revoked_at": None,
            "device": {"serial": SERIAL, "transport_id": 7, "user_id": 0, "boot_id": BOOT, "fingerprint": "fixture"},
        }
        calls = []

        def fulfill(route):
            request = route.request
            calls.append((request.method, request.url, request.post_data))
            if request.method == "GET" and request.url.endswith("/unicode"):
                value = {"revision": 0, "enabled": False}
            elif request.method == "GET" and "/android/environments" in request.url:
                value = {"items": [], "next_after": None}
            elif request.method == "GET":
                value = {
                    "items": [] if "/android/access" in request.url else [row],
                    "next_after": None,
                    "capability": {},
                }
            elif request.url.endswith("/observe"):
                value = {
                    "observation_id": observation_id,
                    "grant_id": grant_id,
                    "expires_in_s": 60,
                    "window": {"package": "org.fixture", "activity": ".Main", "unlocked": True},
                    "truncated": False,
                    "nodes": [
                        {
                            "node_id": node_id,
                            "text": "<private-ui>",
                            "description": "editor",
                            "resource_id": "org.fixture:id/input",
                            "class": "EditText",
                            "actions": ["append_ascii", "tap"],
                            "password": False,
                        }
                    ],
                }
            elif request.method == "DELETE":
                value = {"closed": True}
            elif request.url.endswith("/actions"):
                value = {"run_id": str(uuid.uuid4()), "session_id": str(uuid.uuid4()), "duplicate": False}
            else:
                raise AssertionError(request.url)
            route.fulfill(status=200, content_type="application/json", body=json.dumps(value))

        page.route("**/api/v1/agents/*/android/**", fulfill)
        page.locator('[data-page="android"]').click()
        expect(page.locator("#android-list")).to_have_value(grant_id)
        page.locator("#android-config").select_option(config["id"])
        page.locator("#android-text").fill("draft ASCII")
        page.locator("#android-observe").click()
        expect(page.locator("#android-tree")).to_contain_text("<private-ui>")
        assert page.locator("#android-tree private-ui").count() == 0
        page.locator('[data-page="people"]').click()
        expect(page.locator("#android-tree")).to_have_text("")
        assert any(method == "DELETE" for method, _, _ in calls)
        page.locator('[data-page="android"]').click()
        expect(page.locator("#android-text")).to_have_value("draft ASCII")
        expect(page.locator("#android-action")).to_be_disabled()
        page.locator("#android-observe").click()
        expect(page.locator("#android-action")).to_be_enabled()
        page.locator("#android-session").fill(str(uuid.uuid4()))
        page.locator("#android-kind").select_option("append_ascii")
        page.locator("#android-action").click()
        expect(page.locator("#android-status")).to_contain_text("尚未执行")
        sent = [json.loads(data) for method, url, data in calls if method == "POST" and url.endswith("/actions")]
        assert len(sent) == 1 and sent[0]["text"] == "draft ASCII" and sent[0]["node_id"] == node_id
        assert not page.evaluate("document.documentElement.scrollWidth > innerWidth")
        assert not errors
    finally:
        context.close()


@pytest.mark.parametrize("v2_server", ["two_agents"], indirect=True)
def test_android_drafts_do_not_cross_agent_scopes(ui_browser, v2_server):
    context = ui_browser.new_context(viewport={"width": 1024, "height": 1000})
    try:
        page, errors = connect(context, v2_server)
        agents = page.locator("#agents option").evaluate_all("nodes => nodes.map(n => n.value)")
        assert len(agents) == 2
        first = page.locator("#agents").input_value()
        second = next(value for value in agents if value != first)
        page.locator('[data-page="android"]').click()
        page.locator("#android-text").fill("first owner draft")
        page.locator("#agents").select_option(second)
        expect(page.locator("nav")).not_to_have_attribute("inert", "")
        expect(page.locator("#android-text")).to_have_value("")
        page.locator("#android-text").fill("second owner draft")
        page.locator("#agents").select_option(first)
        expect(page.locator("nav")).not_to_have_attribute("inert", "")
        expect(page.locator("#android-text")).to_have_value("first owner draft")
        assert not errors
    finally:
        context.close()


def test_android_model_scope_revocation_and_key_parameters_use_owner_contract(ui_browser, v2_server):
    context = ui_browser.new_context(viewport={"width": 1024, "height": 1100})
    try:
        page, errors = connect(context, v2_server)
        grant, access, observation, node = (str(uuid.uuid4()) for _ in range(4))
        calls, revoked = [], False

        def fulfilled(route):
            nonlocal revoked
            request = route.request
            calls.append((request.method, request.url, request.post_data_json if request.post_data else None))
            if request.method == "GET" and "/android/access" in request.url:
                data = {
                    "items": [
                        {
                            "id": access,
                            "run_id": str(uuid.uuid4()),
                            "packages": ["org.fixture"],
                            "kinds": ["key"],
                            "expires_at": "2026-11-09T00:00:00+00:00",
                            "availability": "android_access_revoked" if revoked else "available",
                            "revoked_at": "now" if revoked else None,
                            "revision": 2 if revoked else 1,
                        }
                    ],
                    "next_after": None,
                }
            elif request.method == "GET" and request.url.endswith("/unicode"):
                data = {"revision": 0, "enabled": False}
            elif request.method == "GET" and "/android/environments" in request.url:
                data = {"items": [], "next_after": None}
            elif request.method == "GET":
                data = {
                    "items": [
                        {"id": grant, "device": {"serial": SERIAL, "user_id": 0}, "revoked_at": None, "revision": 1}
                    ],
                    "next_after": None,
                }
            elif request.url.endswith("/revoke"):
                assert request.post_data_json == {"revision": 1}
                revoked = True
                data = {"revoked": True}
            elif request.url.endswith("/apps"):
                assert request.post_data_json == {"consent": True, "after": ""}
                data = {"items": ["org.fixture"], "next_after": None, "truncated": False}
            elif request.url.endswith("/observe"):
                data = {
                    "observation_id": observation,
                    "expires_in_s": 60,
                    "window": {"unlocked": True, "package": "org.fixture"},
                    "truncated": False,
                    "nodes": [
                        {
                            "node_id": node,
                            "actions": ["key"],
                            "resource_id": "input",
                            "class": "EditText",
                            "text": "label",
                            "description": "",
                        }
                    ],
                }
            elif request.method == "DELETE":
                data = {"closed": True}
            elif request.url.endswith("/actions"):
                data = {"run_id": str(uuid.uuid4())}
            else:
                raise AssertionError(request.url)
            route.fulfill(status=200, content_type="application/json", body=json.dumps(data))

        page.route("**/api/v1/agents/*/android/**", fulfilled)
        page.locator('[data-page="android"]').click()
        expect(page.locator("#android-access-list")).to_contain_text("org.fixture")
        page.get_by_role("button", name="撤销本次运行权限", exact=True).click()
        expect(page.get_by_role("button", name="撤销本次运行权限", exact=True)).to_be_disabled()
        page.locator("#android-apps").click()
        expect(page.locator("#android-apps-result")).to_have_text("org.fixture")
        page.locator("#android-observe").click()
        expect(page.locator("#android-action")).to_be_enabled()
        page.locator("#android-kind").select_option("key")
        page.locator("#android-key").select_option("BACK")
        page.locator("#android-session").fill(str(uuid.uuid4()))
        page.locator("#android-action").click()
        expect(page.locator("#android-status")).to_contain_text("尚未执行")
        action = next(data for method, url, data in calls if method == "POST" and url.endswith("/actions"))
        assert action["params"] == {"key": "BACK"} and action["kind"] == "key" and action["text"] == ""
        assert not errors
    finally:
        context.close()
