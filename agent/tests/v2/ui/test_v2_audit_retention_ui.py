"""Audit retention preview stays read-only and scoped while sharing history paging and expiration UI."""

from urllib.parse import parse_qs, urlsplit

import pytest
from omni_test_support.v2.history_ui import connect
from omni_test_support.v2.ui import v2_server as v2_server
from playwright.sync_api import expect

AS_OF = "2026-11-05T01:00:00.000000+00:00"


def preview_page(marker, *, eligible=False, more=False, cursor=""):
    return {
        "format": "omni-audit-retention-preview-1",
        "policy": "fixture-audit-retention-v1",
        "agent_id": "fixture-agent",
        "instance_id": "fixture-instance",
        "schema_version": "v2_0026",
        "as_of": AS_OF,
        "cutoff": "2026-05-09T01:00:00.000000+00:00",
        "items": [
            {
                "audit_id": marker,
                "sequence": 7,
                "agent_id": "fixture-agent",
                "kind": "action.ok",
                "operation_id": "fixture-action",
                "created_at": "2026-05-01T01:00:00.000000+00:00",
                "subject_kind": "action",
                "run_id": "fixture-run",
                "eligible": eligible,
                "protected_reasons": [] if eligible else ["model_cost_unresolved", "future_protection"],
                "data": {"private": "private-preview-body-canary"},
            }
        ],
        "counts": {"scanned": 1, "eligible": int(eligible), "protected": int(not eligible)},
        "next_cursor": cursor,
        "has_more": more,
        "stream": {"high_water": 9, "trim_revision": 0, "deleted_count": 0, "last_trim_at": None},
        "history_trimmed": False,
        "can_apply": False,
    }


def assert_preview_contract(context, server, agent):
    url, token = server
    response = context.request.get(
        f"{url}/api/v1/agents/{agent}/audit/retention-preview?limit=20",
        headers={"Authorization": "Bearer " + token},
    )
    assert response.status == 200
    data = response.json()
    assert {
        "as_of",
        "cutoff",
        "items",
        "counts",
        "next_cursor",
        "has_more",
        "stream",
        "history_trimmed",
    } <= data.keys()
    assert data["agent_id"] == agent and data["can_apply"] is False
    assert set(data["counts"]) == {"scanned", "eligible", "protected"}


def test_preview_metadata_pages_expire_and_refresh_without_execution_or_body(ui_browser, v2_server):
    context = ui_browser.new_context(viewport={"width": 390, "height": 1000})
    try:
        page, errors = connect(context, v2_server)
        agent = page.locator("#agents").input_value()
        assert_preview_contract(context, v2_server, agent)
        requests = []

        def respond(route):
            requests.append((route.request.method, parse_qs(urlsplit(route.request.url).query)))
            number = len(requests)
            if number == 3:
                route.fulfill(status=410, json={"error": {"code": "history_cursor_expired", "message": "Expired"}})
            else:
                route.fulfill(
                    json=preview_page(
                        {1: "eligible-first", 2: "protected-second", 4: "fresh-preview"}[number],
                        eligible=number == 1,
                        more=number < 3,
                        cursor=f"opaque:{number}&next",
                    )
                )

        page.route(f"**/api/v1/agents/{agent}/audit/retention-preview*", respond)
        page.get_by_role("button", name="诊断日志", exact=True).click()
        expect(page.get_by_role("heading", name="180天审计候选（仅预览）", exact=True)).to_be_visible()
        assert requests == []
        page.locator("#refresh-audit-retention").click()
        expect(page.locator("#audit-retention-list")).to_contain_text("eligible-first")
        expect(page.locator("#audit-retention-list")).to_contain_text("符合预览条件")
        expect(page.locator("#audit-retention-status")).to_contain_text("本页检查 1 条，符合预览条件 1 条")
        page.locator("#more-audit-retention").click()
        expect(page.locator("#audit-retention-list")).to_contain_text("protected-second")
        expect(page.locator("#audit-retention-list")).to_contain_text("模型费用尚未核清；future_protection")
        expect(page.locator("#audit-retention-status")).to_contain_text("本页检查 1 条，符合预览条件 0 条，受保护 1 条")
        assert "private-preview-body-canary" not in page.locator("#audit-retention-list").text_content()
        page.locator("#more-audit-retention").click()
        expect(page.locator("#audit-retention-status")).to_contain_text("旧记录已清空")
        expect(page.locator("#audit-retention-list")).to_have_text("")
        expect(page.locator("#more-audit-retention")).to_be_disabled()
        page.locator("#refresh-audit-retention").click()
        expect(page.locator("#audit-retention-list")).to_contain_text("fresh-preview")
        expect(page.locator("#audit-retention-list")).not_to_contain_text("eligible-first")
        assert requests == [
            ("GET", {"limit": ["20"]}),
            ("GET", {"limit": ["20"], "cursor": ["opaque:1&next"], "as_of": [AS_OF]}),
            ("GET", {"limit": ["20"], "cursor": ["opaque:2&next"], "as_of": [AS_OF]}),
            ("GET", {"limit": ["20"]}),
        ]
        assert page.evaluate("document.documentElement.scrollWidth <= window.innerWidth")
        assert errors == []
    finally:
        context.close()


@pytest.mark.parametrize("v2_server", ["two_agents"], indirect=True)
def test_late_preview_expiration_cannot_clear_new_agent_results(ui_browser, v2_server):
    context = ui_browser.new_context(viewport={"width": 1024, "height": 1000})
    try:
        page, errors = connect(context, v2_server)
        original = page.locator("#agents").input_value()
        choices = page.locator("#agents option").evaluate_all("options => options.map(option => option.value)")
        other = next(value for value in choices if value != original)
        for agent in choices:
            assert_preview_contract(context, v2_server, agent)
        page.evaluate(
            """() => {
              const fetch = window.fetch.bind(window);
              window.__previewPending = [];
              window.fetch = (url, options) => {
                if (String(url).includes('/audit/retention-preview?')) {
                  return new Promise(resolve => window.__previewPending.push({url, resolve}));
                }
                return fetch(url, options);
              };
            }"""
        )
        page.get_by_role("button", name="诊断日志", exact=True).click()
        page.locator("#refresh-audit-retention").click()
        page.wait_for_function("() => window.__previewPending.length === 1")
        page.evaluate(
            "body => window.__previewPending[0].resolve(new Response(JSON.stringify(body), {status: 200}))",
            preview_page("old-agent", more=True, cursor="old-preview"),
        )
        expect(page.locator("#audit-retention-list")).to_contain_text("old-agent")
        page.locator("#more-audit-retention").click()
        page.wait_for_function("() => window.__previewPending.length === 2")
        page.locator("#agents").select_option(other)
        expect(page.locator("nav")).not_to_have_attribute("inert", "")
        expect(page.locator("#audit-retention-list")).to_have_text("")
        page.locator("#refresh-audit-retention").click()
        page.wait_for_function("() => window.__previewPending.length === 3")
        page.evaluate(
            "body => window.__previewPending[2].resolve(new Response(JSON.stringify(body), {status: 200}))",
            preview_page("current-agent", cursor="current-preview"),
        )
        expect(page.locator("#audit-retention-list")).to_contain_text("current-agent")
        page.evaluate(
            """async () => {
              window.__previewPending[1].resolve(new Response(JSON.stringify({
                error: {code: 'history_cursor_expired', message: 'Expired previous scope'}
              }), {status: 410}));
              await new Promise(requestAnimationFrame);
            }"""
        )
        expect(page.locator("#audit-retention-list")).to_contain_text("current-agent")
        expect(page.locator("#audit-retention-status")).not_to_contain_text("旧记录已清空")
        expect(page.locator("#more-audit-retention")).to_be_disabled()
        expect(page.locator("#error")).to_be_hidden()
        assert parse_qs(urlsplit(page.evaluate("window.__previewPending[1].url")).query) == {
            "limit": ["20"],
            "cursor": ["old-preview"],
            "as_of": [AS_OF],
        }
        current_url = page.evaluate("window.__previewPending[2].url")
        assert current_url.startswith(f"/api/v1/agents/{other}/audit/")
        assert parse_qs(urlsplit(current_url).query) == {"limit": ["20"]}
        assert errors == []
    finally:
        context.close()
