"""Real skills API and per-case BrowserContext/DB, four widths and both themes."""

import pytest
from omni_test_support.v2.history_ui import connect
from omni_test_support.v2.memory_organization_ui import confirm
from omni_test_support.v2.skills import markdown
from omni_test_support.v2.ui import v2_server as v2_server
from playwright.sync_api import expect


@pytest.mark.parametrize("width", [390, 760, 1024, 1440])
@pytest.mark.parametrize("theme", ["light", "dark"])
def test_skill_draft_review_and_explicit_turn_selection(ui_browser, v2_server, width, theme):
    context = ui_browser.new_context(viewport={"width": width, "height": 1000})
    try:
        context.add_init_script(f"localStorage.setItem('omni_theme','{theme}')")
        page, errors = connect(context, v2_server)
        url, token = v2_server
        agent = page.locator("#agents").input_value()
        headers = {"Authorization": "Bearer " + token}
        base = url + f"/api/v1/agents/{agent}"
        settings = context.request.get(base + "/settings", headers=headers).json()
        model = context.request.get(
            base + "/configurations/" + settings["value"]["model_profile_id"], headers=headers
        ).json()
        assert (
            context.request.put(
                base + "/configurations/" + model["id"],
                headers=headers,
                data={
                    "name": model["name"],
                    "enabled": True,
                    "revision": model["revision"],
                    "value": {**model["value"], "model": "fixture"},
                },
            ).status
            == 200
        )
        page.locator('nav[aria-label="工作台"] [data-page="skills"]').click()
        page.locator("#skills-new").click()
        text = markdown(text="Follow procedure <script>window.__skillCanary753=1</script> and verify.")
        page.locator("#skills-content").fill(text)
        page.locator('[data-page="people"]').click()
        page.locator('nav[aria-label="工作台"] [data-page="skills"]').click()
        expect(page.locator("#skills-content")).to_have_value(text)
        page.locator("#skills-save").click()
        expect(page.locator("#skills-status")).to_contain_text("disabled")
        skill = page.locator("#skills-list").input_value()
        page.locator("#skills-enable").click()
        confirm(page, "启用此修订")
        expect(page.locator("#skills-status")).to_contain_text("enabled")
        page.locator('[data-page="chat"]').click()
        # P3 渐进披露：技能选择收进「更多」面板，操作前先展开（不放宽断言）
        page.locator("#dock-more").click()
        page.locator("#skill-turn").select_option([skill])
        page.locator("#message").fill("hello")
        page.get_by_role("button", name="发送", exact=True).click()
        expect(page.locator("#messages")).to_contain_text("V2 test reply")
        expect(page.locator("#skill-run-status")).to_contain_text("已使用")
        page.locator('nav[aria-label="工作台"] [data-page="skills"]').click()
        page.locator("#skills-disable").click()
        confirm(page, "停用此技能")
        expect(page.locator("#skills-status")).to_contain_text("disabled")
        page.locator("#skills-rollback").click()
        confirm(page, "创建停用修订")
        expect(page.locator("#skills-status")).to_contain_text("内容修订 2")
        assert page.evaluate("window.__skillCanary753") is None
        assert not page.evaluate("document.documentElement.scrollWidth > innerWidth") and not errors
    finally:
        context.close()
