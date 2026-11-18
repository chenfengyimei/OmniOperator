"""Upload-to-job production workbench at phone/desktop widths, isolated processor and browser context."""

import pytest
from omni_test_support.v2.media_ui import media_server as media_server
from omni_test_support.v2.media_ui import wait_job_done, wait_job_id
from playwright.sync_api import expect


@pytest.mark.parametrize("width", [390, 1024])
def test_media_upload_workbench_registers_audio_then_transcribes_without_channel(
    ui_browser, media_server, width, tmp_path
):
    url, token = media_server
    context = ui_browser.new_context(viewport={"width": width, "height": 1000})
    try:
        page = context.new_page()
        errors = []
        page.on("pageerror", lambda exc: errors.append(str(exc)))
        page.goto(url)
        page.locator("#token").fill(token)
        page.get_by_role("button", name="连接实例").click()
        expect(page.locator("#agents option")).to_have_count(1)
        headers = {"Authorization": "Bearer " + token}
        agent = page.request.get(url + "/api/v1/agents", headers=headers).json()["items"][0]["id"]
        base = f"{url}/api/v1/agents/{agent}"
        page.get_by_role("button", name="媒体", exact=True).click()
        page.locator("#media-upload-file").set_input_files(
            {"name": "fixture.wav", "mimeType": "audio/wav", "buffer": b"ui-owned-audio"}
        )
        page.locator("#media-upload-title").fill("本地音频")
        page.get_by_role("button", name="上传并登记", exact=True).click()
        expect(page.locator("#media-upload-status")).to_contain_text("已登记上传来源")
        expect(page.locator("#media-source-list option")).to_have_count(1)
        expect(page.locator("#media-source-detail")).to_contain_text("上传音频")
        assert page.locator("#media-operation").input_value() == "transcribe"
        # Playwright's is_disabled reports false for OPTION in this browser;
        # the native DOM property/attribute are the actual select contract.
        for operation in ("subtitle", "frames"):
            option = page.locator(f"#media-operation option[value={operation}]")
            expect(option).to_have_attribute("disabled", "")
            assert option.evaluate("el => el.disabled") is True
        page.locator("#media-upload-form").scroll_into_view_if_needed()
        page.screenshot(path=str(tmp_path / f"upload-form-{width}.png"))
        page.locator("#media-job-form button[type=submit]").click()
        source = page.locator("#media-source-list").input_value()
        job = wait_job_id(page, base, headers, source, "transcribe")
        wait_job_done(page, base, headers, job)
        page.locator("#media-transcript-open").click()
        expect(page.locator("#media-transcript-window")).to_contain_text("你好世界")
        assert not errors and page.evaluate("document.documentElement.scrollWidth <= window.innerWidth")
        page.screenshot(path=str(tmp_path / f"upload-{width}.png"), full_page=True)
    finally:
        context.close()
