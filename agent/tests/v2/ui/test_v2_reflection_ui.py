"""Owner reflection review through the real workbench page and production HTTP."""

import pytest
from omni_test_support.v2.history_ui import connect
from omni_test_support.v2.ui import v2_server as v2_server
from playwright.sync_api import expect

SENTENCE = "📖 Observe before inferring."


@pytest.mark.parametrize("v2_server", ["reflection"], indirect=True)
@pytest.mark.parametrize("width,theme", [(390, "dark"), (1440, "light")])
def test_owner_reviews_approves_and_retracts(ui_browser, v2_server, width, theme):
    seed = v2_server.app.state.reflection_seed
    assert seed
    context = ui_browser.new_context(viewport={"width": width, "height": 1000})
    try:
        context.add_init_script(f"localStorage.setItem('omni_theme','{theme}')")
        page, errors = connect(context, v2_server)
        page.locator('nav[aria-label="工作台"] [data-page="reflections"]').click()
        expect(page.locator("#reflection-status")).to_contain_text("待决定 1 条")
        expect(page.locator("#reflection-pending option")).to_have_count(1)
        # Source text is rendered as untrusted text, never as markup.
        expect(page.locator("#reflection-detail")).to_contain_text(SENTENCE)
        expect(page.locator("#reflection-detail")).to_contain_text("事实：")
        expect(page.locator("#reflection-detail")).to_contain_text("来源 1 条")
        assert page.locator("#reflection-detail *").count() == 0
        expect(page.locator("#reflection-approve")).to_be_enabled()
        expect(page.locator("#reflection-retract")).to_be_disabled()

        page.locator("#reflection-approve").click()
        expect(page.locator("#reflection-status")).to_contain_text("已批准")
        expect(page.locator("#reflection-pending option")).to_have_count(0)
        expect(page.locator("#reflection-approved option")).to_have_count(1)
        page.locator("#reflection-approved").select_option(seed["journal"])
        expect(page.locator("#reflection-detail")).to_contain_text("候选：已批准")
        expect(page.locator("#reflection-retract")).to_be_enabled()

        page.locator("#reflection-retract").click()
        page.get_by_role("button", name="撤回", exact=True).click()
        expect(page.locator("#reflection-status")).to_contain_text("已撤回")
        expect(page.locator("#reflection-approved option")).to_have_count(0)
        expect(page.locator("#reflection-pending option")).to_have_count(0)

        assert not page.evaluate("document.documentElement.scrollWidth > innerWidth")
        assert errors == []
    finally:
        context.close()
