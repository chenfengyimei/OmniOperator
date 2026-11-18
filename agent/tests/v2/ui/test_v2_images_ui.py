"""Image-only production chat at four viewports and both themes, with isolated browser contexts."""

import pytest
from omni_test_support.v2.history_ui import connect
from omni_test_support.v2.images import png
from omni_test_support.v2.ui import v2_server as v2_server
from playwright.sync_api import expect


@pytest.mark.parametrize("width", [390, 760, 1024, 1440])
@pytest.mark.parametrize("theme", ["light", "dark"])
def test_image_draft_switch_and_image_only_message(ui_browser, v2_server, width, theme):
    context = ui_browser.new_context(viewport={"width": width, "height": 1000})
    try:
        context.add_init_script(f"localStorage.setItem('omni_theme','{theme}')")
        page, errors = connect(context, v2_server)
        url, token = v2_server
        headers = {"Authorization": "Bearer " + token}
        agent = page.locator("#agents").input_value()
        base = url + f"/api/v1/agents/{agent}"
        settings = context.request.get(base + "/settings", headers=headers).json()
        row = context.request.get(
            base + "/configurations/" + settings["value"]["model_profile_id"], headers=headers
        ).json()
        assert (
            context.request.put(
                base + "/configurations/" + row["id"],
                headers=headers,
                data={
                    "name": row["name"],
                    "enabled": True,
                    "revision": row["revision"],
                    "value": {**row["value"], "model": "gpt-4o"},
                    "image_policy": {"protocol": "openai_low", "per_call_limit": 2, "run_budget": 4},
                },
            ).status
            == 200
        )
        page.locator("#image-file").set_input_files({"name": "source.png", "mimeType": "image/png", "buffer": png()})
        expect(page.locator("#image-drafts")).to_contain_text("待附加")
        assert "private-image-canary" not in page.locator("#image-drafts").inner_text()
        page.locator('[data-page="people"]').click()
        page.locator('[data-page="chat"]').click()
        expect(page.locator("#image-drafts li")).to_have_count(1)
        page.get_by_role("button", name="发送", exact=True).click()
        expect(page.locator("#messages")).to_contain_text("V2 test reply")
        expect(page.locator("#messages")).to_contain_text("图片 80 × 40")
        expect(page.locator("#image-run-status")).to_contain_text("图片请求次数 1/4")
        expect(page.locator("#image-run-status")).to_contain_text("已附加到完成的请求")
        expect(page.locator("#image-drafts li")).to_have_count(0)
        assert "private-image-canary" not in page.evaluate("JSON.stringify({...localStorage})")
        assert not page.evaluate("document.documentElement.scrollWidth > innerWidth") and not errors
    finally:
        context.close()
