"""QQ workbench grants retain drafts across sections with independent browser and database scopes."""

import pytest
from playwright.sync_api import expect
from test_v2_history_ui import connect
from test_v2_ui import v2_server as v2_server


@pytest.mark.parametrize("width", [390, 760, 1024, 1440])
@pytest.mark.parametrize("theme", ["light", "dark"])
def test_qq_grants_and_drafts(ui_browser, v2_server, width, theme):
    context = ui_browser.new_context(viewport={"width": width, "height": 1000})
    try:
        context.add_init_script(f"localStorage.setItem('omni_theme', '{theme}')")
        page, errors = connect(context, v2_server)
        agent = page.locator("#agents").input_value()
        result = context.request.post(
            v2_server[0] + f"/api/v1/agents/{agent}/configurations",
            headers={"Authorization": "Bearer " + v2_server[1]},
            data={
                "kind": "channel",
                "name": "QQ fixture",
                "enabled": False,
                "value": {"driver": "qq", "account": "456", "endpoint": "ws://127.0.0.1:3001"},
            },
        )
        assert result.status == 201
        channel = result.json()
        page.locator('[data-page="qq"]').click()
        expect(page.locator("#qq-fields")).to_be_enabled()
        page.locator("#qq-config").fill(channel["id"])
        page.locator("#qq-peer").fill("123")
        page.locator("#qq-persona").fill("独立 QQ 人设 <script>literal</script>")
        page.locator('[data-page="people"]').click()
        page.locator('[data-page="qq"]').click()
        expect(page.locator("#qq-persona")).to_have_value("独立 QQ 人设 <script>literal</script>")
        page.locator("#qq-save").click()
        expect(page.locator("#qq-config")).to_be_disabled()
        expect(page.locator("#qq-draft-status")).to_contain_text("已载入修订 1")
        page.locator("#qq-persona").fill("修订后的私聊人设")
        page.locator("#qq-save").click()
        expect(page.locator("#qq-draft-status")).to_contain_text("已载入修订 2")
        assert not page.evaluate("document.documentElement.scrollWidth > innerWidth")
        assert errors == []
    finally:
        context.close()
