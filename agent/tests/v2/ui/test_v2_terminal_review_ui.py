"""Terminal review editor via isolated BrowserContexts and real APIs, no native shell."""

import asyncio

import pytest
from omni_test_support.v2.history_ui import connect
from omni_test_support.v2.terminals_ui import headers
from omni_test_support.v2.terminals_ui import terminal_backend as terminal_backend
from omni_test_support.v2.terminals_ui import terminal_server as terminal_server
from omni_test_support.v2.ui import v2_server as v2_server
from omni_test_support.v2.v2_terminal_review_helpers import unknown
from playwright.sync_api import expect


@pytest.mark.parametrize("width,theme", [(390, "dark"), (1024, "light")])
def test_unknown_terminal_review_records_independent_evidence_and_panel_leave_clears_it(
    ui_browser, terminal_server, terminal_backend, tmp_path, width, theme
):
    context = ui_browser.new_context(viewport={"width": width, "height": 1000})
    try:
        context.add_init_script(f"localStorage.setItem('omni_theme', '{theme}')")
        page, errors = connect(context, terminal_server)
        agent = page.locator("#agents").input_value()
        # The existing isolated server publishes its loop after the first HTTP request.
        instance = terminal_server.app.state.runtime
        future = asyncio.run_coroutine_threadsafe(
            unknown(instance.application._db, agent, tmp_path / "workspace"),
            terminal_server.app.state.terminal_fixture_loop,
        )
        operation = future.result(10)
        page.get_by_role("button", name="所有者终端", exact=True).click()
        page.get_by_text("未知终端操作核查", exact=True).click()
        page.get_by_role("button", name="加载未知终端操作", exact=True).click()
        expect(page.locator("#terminal-review-select option")).to_have_count(2)
        page.locator("#terminal-review-select").select_option(operation["id"])
        expect(page.locator("#terminal-review-detail")).to_contain_text(operation["id"])
        page.locator("#terminal-review-decision").select_option("still_unknown")
        page.locator("#terminal-review-evidence").fill("ONLY-TERMINAL-REVIEW-UI-CANARY")
        expect(page.get_by_role("button", name="登记终端核查", exact=True)).to_be_disabled()
        page.locator("#terminal-review-ack").check()
        if width == 390:

            def lose_response(route):
                # The server commits; only its HTTP response is lost. The UI
                # must keep the same review identity when the owner retries.
                response = route.fetch()
                assert response.status == 201
                route.abort("failed")

            page.route("**/terminal-reviews/" + operation["id"], lose_response, times=1)
        page.get_by_role("button", name="登记终端核查", exact=True).click()
        if width == 390:
            expect(page.get_by_role("button", name="按原身份重试登记", exact=True)).to_be_enabled()
            page.get_by_role("button", name="按原身份重试登记", exact=True).click()
        expect(page.locator("#terminal-review-history")).to_contain_text("ONLY-TERMINAL-REVIEW-UI-CANARY")
        expect(page.locator("#terminal-review-history article")).to_have_count(1)
        assert not terminal_backend.processes
        response = context.request.get(
            f"{terminal_server[0]}/api/v1/agents/{agent}/terminal-operations/{operation['id']}",
            headers=headers(terminal_server),
        )
        assert response.json()["operation"]["state"] == "unknown"
        assert page.evaluate("document.documentElement.scrollWidth <= innerWidth")
        page.locator('nav[aria-label="工作台"] [data-page="memory"]').click()
        expect(page.locator("#terminal-review-history")).to_be_empty()
        expect(page.locator("#terminal-review-evidence")).to_have_value("")
        assert not errors
    finally:
        context.close()
