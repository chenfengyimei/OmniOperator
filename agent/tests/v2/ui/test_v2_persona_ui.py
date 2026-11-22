"""Owner-only persona growth workbench page: ready, stale and explicit rebuild."""

import pytest
from omni_test_support.v2.history_ui import connect
from omni_test_support.v2.ui import v2_server as v2_server
from playwright.sync_api import expect


@pytest.mark.parametrize("v2_server", ["persona"], indirect=True)
@pytest.mark.parametrize("width,theme", [(390, "dark"), (1440, "light")])
def test_owner_reads_stale_and_rebuilds(ui_browser, v2_server, width, theme):
    seed = v2_server.app.state.persona_seed
    assert seed
    context = ui_browser.new_context(viewport={"width": width, "height": 1000})
    try:
        context.add_init_script(f"localStorage.setItem('omni_theme','{theme}')")
        page, errors = connect(context, v2_server)
        page.locator('nav[aria-label="工作台"] [data-page="persona-growth"]').click()
        expect(page.locator("#persona-growth-status")).to_contain_text("已同步")
        expect(page.locator("#persona-growth-content")).to_contain_text(seed["sentence"])
        expect(page.locator("#persona-growth-meta")).to_contain_text("1 条已批准")
        assert page.locator("#persona-content *").count() == 0
        expect(page.locator("#persona-growth-rebuild")).to_be_hidden()

        url, token = v2_server
        retract = page.request.post(
            f"{url}/api/v1/agents/{seed['agent']}/reflections/candidates/{seed['candidate']}/decision",
            headers={"Authorization": "Bearer " + token},
            data={"decision": "retract", "revision": seed["revision"], "digest": seed["digest"]},
        )
        assert retract.ok, retract.text()

        page.locator("#persona-growth-refresh").click()
        expect(page.locator("#persona-growth-status")).to_contain_text("过期")
        expect(page.locator("#persona-growth-content")).not_to_contain_text(seed["sentence"])
        expect(page.locator("#persona-growth-rebuild")).to_be_visible()

        page.locator("#persona-growth-rebuild").click()
        expect(page.locator("#persona-growth-status")).to_contain_text("重建完成")
        expect(page.locator("#persona-growth-content")).not_to_contain_text(seed["sentence"])
        expect(page.locator("#persona-growth-meta")).to_contain_text("0 条已批准")
        expect(page.locator("#persona-growth-rebuild")).to_be_hidden()
        assert not page.evaluate("document.documentElement.scrollWidth > innerWidth")
        assert errors == []
    finally:
        context.close()
