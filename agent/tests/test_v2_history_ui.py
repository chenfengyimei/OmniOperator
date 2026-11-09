"""Audit cursors are scoped, explicit about cleared history, and shared by both workbench views."""

from urllib.parse import parse_qs, urlsplit

import pytest
from playwright.sync_api import expect
from test_v2_ui import v2_server as v2_server


def history_page(marker, *, more=False, cursor="", trimmed=False):
    return {
        "items": [
            {
                "sequence": 7,
                "created_at": "2026-11-04T13:00:00.000000+00:00",
                "kind": "maintenance.future_kind",
                "operation_id": marker,
                "outcome": "unknown",
                "data": {"operation": "set"},
            }
        ],
        "next_cursor": cursor,
        "has_more": more,
        "stream": {
            "high_water": 7,
            "trim_revision": 2 if trimmed else 0,
            "deleted_count": 3 if trimmed else 0,
            "last_trim_at": "2026-11-04T12:00:00.000000+00:00" if trimmed else None,
        },
        "history_trimmed": trimmed,
    }


def connect(context, server):
    url, token = server
    page = context.new_page()
    errors = []
    page.on("pageerror", lambda error: errors.append(str(error)))
    page.goto(url)
    page.locator("#token").fill(token)
    page.get_by_role("button", name="连接实例", exact=True).click()
    expect(page.locator("nav")).not_to_have_attribute("inert", "")
    return page, errors


def assert_history_contract(context, server, suffix):
    url, token = server
    response = context.request.get(url + "/api/v1" + suffix, headers={"Authorization": "Bearer " + token})
    assert response.status == 200
    value = response.json()
    assert {"items", "next_cursor", "has_more", "stream", "history_trimmed"} <= value.keys()
    assert {"high_water", "trim_revision", "deleted_count", "last_trim_at"} <= value["stream"].keys()


@pytest.mark.parametrize("stream", ["agent", "instance"])
def test_audit_pages_expire_clear_old_view_and_reload_without_old_cursor(ui_browser, v2_server, stream):
    context = ui_browser.new_context(viewport={"width": 390 if stream == "agent" else 1440, "height": 1000})
    try:
        context.add_init_script(f"localStorage.setItem('omni_theme', '{'dark' if stream == 'agent' else 'light'}')")
        page, errors = connect(context, v2_server)
        agent = page.locator("#agents").input_value()
        suffix = f"/agents/{agent}/audit" if stream == "agent" else "/system/audit"
        # The live endpoint must expose the new contract before injected UI faults are exercised.
        assert_history_contract(context, v2_server, suffix)
        opaque = "opaque:&cursor=下一页?"
        requests = []

        def respond(route):
            query = parse_qs(urlsplit(route.request.url).query)
            requests.append(query)
            if len(requests) == 2:
                route.fulfill(
                    status=410,
                    json={"error": {"code": "history_cursor_expired", "message": "Expired history cursor"}},
                )
            else:
                route.fulfill(
                    json=history_page(
                        "first-page" if len(requests) == 1 else "retained-page",
                        more=len(requests) == 1,
                        cursor=opaque if len(requests) == 1 else "end-cursor",
                        trimmed=True,
                    )
                )

        page.route("**/api/v1" + suffix + "*", respond)
        if stream == "agent":
            page.get_by_role("button", name="审计记录", exact=True).click()
            list_id, status_id, more_id, refresh_id = "audit-list", "audit-status", "more-audit", "refresh-audit"
        else:
            page.get_by_role("button", name="诊断日志", exact=True).click()
            page.get_by_text("实例维护审计（最多显示最近读取的 200 条）", exact=True).click()
            page.locator("#read-instance-audit").click()
            list_id, status_id, more_id, refresh_id = (
                "instance-audit",
                "instance-audit-status",
                "more-instance-audit",
                "read-instance-audit",
            )
        expect(page.locator("#" + list_id)).to_contain_text("first-page")
        expect(page.locator("#" + status_id)).to_contain_text("部分审计历史已清理")
        expect(page.locator("#" + more_id)).to_be_enabled()
        if stream == "instance":
            text = page.locator("#" + list_id).text_content()
            assert "实例维护记录 · maintenance.future_kind" in text
            assert "保存凭据" not in text and "删除凭据" not in text and "undefined" not in text
        page.locator("#" + more_id).click()
        expect(page.locator("#" + status_id)).to_contain_text("原游标已失效；旧记录已清空")
        expect(page.locator("#" + list_id)).to_have_text("")
        expect(page.locator("#" + more_id)).to_be_disabled()
        expect(page.locator("#" + refresh_id)).to_be_enabled()
        expect(page.locator("#error")).to_be_hidden()
        page.locator("#" + refresh_id).click()
        expect(page.locator("#" + list_id)).to_contain_text("retained-page")
        expect(page.locator("#" + list_id)).not_to_contain_text("first-page")
        expect(page.locator("#" + more_id)).to_be_disabled()
        assert requests == [{"limit": ["100"]}, {"limit": ["100"], "cursor": [opaque]}, {"limit": ["100"]}]
        assert page.evaluate("document.documentElement.scrollWidth <= window.innerWidth")
        assert errors == []
    finally:
        context.close()


@pytest.mark.parametrize("v2_server", ["two_agents"], indirect=True)
def test_late_agent_audit_cannot_restore_previous_view_or_cursor(ui_browser, v2_server):
    context = ui_browser.new_context(viewport={"width": 760, "height": 1000})
    try:
        page, errors = connect(context, v2_server)
        choices = page.locator("#agents option").evaluate_all("options => options.map(option => option.value)")
        assert len(choices) == 2
        original = page.locator("#agents").input_value()
        other = next(value for value in choices if value != original)
        for agent in choices:
            assert_history_contract(context, v2_server, f"/agents/{agent}/audit")
        page.evaluate(
            r"""() => {
              const fetch = window.fetch.bind(window);
              window.__auditPending = [];
              window.fetch = (url, options) => {
                if (/\/agents\/[^/]+\/audit\?/.test(String(url))) {
                  return new Promise(resolve => window.__auditPending.push({url, resolve}));
                }
                return fetch(url, options);
              };
            }"""
        )
        page.get_by_role("button", name="审计记录", exact=True).click()
        page.wait_for_function("() => window.__auditPending.length === 1")
        page.locator("#agents").select_option(other)
        page.wait_for_function("() => window.__auditPending.length === 2")
        page.evaluate(
            "body => window.__auditPending[1].resolve(new Response(JSON.stringify(body), {status: 200}))",
            history_page("current-agent", cursor="current-cursor"),
        )
        expect(page.locator("#audit-list")).to_contain_text("current-agent")
        page.evaluate(
            """async body => {
              window.__auditPending[0].resolve(new Response(JSON.stringify(body), {status: 200}));
              await new Promise(requestAnimationFrame);
            }""",
            history_page("stale-agent", more=True, cursor="stale-cursor"),
        )
        expect(page.locator("#audit-list")).to_contain_text("current-agent")
        expect(page.locator("#audit-list")).not_to_contain_text("stale-agent")
        expect(page.locator("#more-audit")).to_be_disabled()
        assert page.evaluate("window.__auditPending[1].url").startswith(f"/api/v1/agents/{other}/audit?")
        assert errors == []
    finally:
        context.close()
