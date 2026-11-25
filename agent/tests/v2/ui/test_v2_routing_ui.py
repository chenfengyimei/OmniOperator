"""Tier drafts, model selection and persistent cost/tool reports at all supported viewports/themes."""

import pytest
from omni_test_support.v2.history_ui import connect
from omni_test_support.v2.ui import reveal_settings
from omni_test_support.v2.ui import v2_server as v2_server
from playwright.sync_api import expect


@pytest.mark.parametrize("width", [390, 760, 1024, 1440])
@pytest.mark.parametrize("theme", ["light", "dark"])
def test_tier_editor_draft_and_actual_model_report(ui_browser, v2_server, width, theme):
    context = ui_browser.new_context(viewport={"width": width, "height": 1000})
    try:
        context.add_init_script(f"localStorage.setItem('omni_theme','{theme}')")
        page, errors = connect(context, v2_server)
        url, token = v2_server
        agent = page.locator("#agents").input_value()
        response = context.request.post(
            url + f"/api/v1/agents/{agent}/configurations",
            headers={"Authorization": "Bearer " + token},
            data={
                "kind": "model",
                "name": "UI cheap <script>window.injected=1</script>",
                "enabled": True,
                "value": {"model": "fixture-small"},
            },
        )
        assert response.status == 201
        model = response.json()
        # 上下文页另有「运行设置」快捷按钮同带 data-page=settings，裸选择器二义；
        # 按既有约定把切页入口限定到导航（同技能/记忆测试）。
        page.locator('nav[aria-label="工作台"] [data-page="settings"]').click()
        reveal_settings(page)
        expect(page.locator("#route-fields")).to_be_enabled()
        page.locator("#route-chat-model").select_option(model["id"])
        page.locator("#route-chat-reserve").fill("321")
        page.locator("#route-default").select_option("auto")
        page.locator("#kernel-safe_resume").check()
        page.locator("#kernel-fallback-1").select_option(model["id"])
        page.locator('nav[aria-label="工作台"] [data-page="people"]').click()
        page.locator('nav[aria-label="工作台"] [data-page="settings"]').click()
        reveal_settings(page)
        expect(page.locator("#route-chat-reserve")).to_have_value("321")
        page.get_by_role("button", name="保存档位配置", exact=True).click()
        expect(page.locator("#route-config-status")).to_contain_text("已载入修订 1")
        stored = context.request.get(
            url + f"/api/v1/agents/{agent}/routing", headers={"Authorization": "Bearer " + token}
        )
        assert stored.json()["kernel"]["safe_resume"]
        assert stored.json()["kernel"]["fallback_model_ids"] == [model["id"]]
        page.locator('nav[aria-label="工作台"] [data-page="chat"]').click()
        page.locator("#message").fill("你好")
        page.get_by_role("button", name="发送", exact=True).click()
        expect(page.locator("#messages")).to_contain_text("V2 test reply")
        expect(page.locator("#route-run-status")).to_contain_text("档位 聊天")
        expect(page.locator("#route-run-status")).to_contain_text("fixture-small")
        expect(page.locator("#route-run-status")).to_contain_text("321 微美元")
        expect(page.locator("#route-run-status")).to_contain_text("不是费用收益")
        expect(page.locator("#route-call-list")).to_contain_text("memory_search")
        assert not page.locator("#route-run-status script").count() and page.evaluate("window.injected") is None
        assert not page.evaluate("document.documentElement.scrollWidth > innerWidth") and not errors
    finally:
        context.close()
