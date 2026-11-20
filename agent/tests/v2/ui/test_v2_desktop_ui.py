"""Owner desktop observations use real scoped HTTP and injected metadata, never a real native desktop."""

from omni_test_support.v2.desktop_ui import (
    BusinessError as BusinessError,
)
from omni_test_support.v2.desktop_ui import (
    DesktopClock as DesktopClock,
)
from omni_test_support.v2.desktop_ui import (
    FixtureDesktopBackend as FixtureDesktopBackend,
)
from omni_test_support.v2.desktop_ui import (
    connect as connect,
)
from omni_test_support.v2.desktop_ui import (
    desktop_backend as desktop_backend,
)
from omni_test_support.v2.desktop_ui import (
    desktop_server as desktop_server,
)
from omni_test_support.v2.desktop_ui import (
    expect as expect,
)
from omni_test_support.v2.desktop_ui import (
    headers as headers,
)
from omni_test_support.v2.desktop_ui import (
    hold as hold,
)
from omni_test_support.v2.desktop_ui import (
    install_response_gates as install_response_gates,
)
from omni_test_support.v2.desktop_ui import (
    list_targets as list_targets,
)
from omni_test_support.v2.desktop_ui import (
    open_desktop as open_desktop,
)
from omni_test_support.v2.desktop_ui import (
    parse_qs as parse_qs,
)
from omni_test_support.v2.desktop_ui import (
    probe as probe,
)
from omni_test_support.v2.desktop_ui import (
    probe_report as probe_report,
)
from omni_test_support.v2.desktop_ui import (
    pytest as pytest,
)
from omni_test_support.v2.desktop_ui import (
    release as release,
)
from omni_test_support.v2.desktop_ui import (
    shared_ui as shared_ui,
)
from omni_test_support.v2.desktop_ui import (
    status as status,
)
from omni_test_support.v2.desktop_ui import (
    target_identity as target_identity,
)
from omni_test_support.v2.desktop_ui import (
    urlsplit as urlsplit,
)
from omni_test_support.v2.desktop_ui import (
    uuid as uuid,
)
from omni_test_support.v2.desktop_ui import (
    v2_server as v2_server,
)


def test_target_specific_read_capabilities_disable_unsupported_buttons(ui_browser, desktop_server, desktop_backend):
    semantic = {**target_identity(), "platform": "linux", "backend": "atspi", "native_id": "atspi::1.20|/window"}
    capture = {**semantic, "backend": "x11", "native_id": "x11:00400001:" + "a" * 64}
    desktop_backend.items = [semantic, capture]
    desktop_backend.report["capabilities"][1].update(
        supported=True, installed=True, permission="granted", available=True, error_code=None
    )
    desktop_backend.report["capabilities"][2].update(
        supported=True, installed=True, permission="granted", available=True, error_code=None
    )
    context = ui_browser.new_context(viewport={"width": 1024, "height": 1000})
    try:
        page, errors = connect(context, desktop_server)
        open_desktop(page)
        probe(page)
        list_targets(page)
        rows = page.locator("#desktop-targets article")
        expect(rows).to_have_count(2)
        expect(rows.nth(0).get_by_role("button", name="读取界面内容", exact=True)).to_be_enabled()
        expect(rows.nth(1).get_by_role("button", name="读取界面内容", exact=True)).to_be_disabled()
        assert (
            rows.nth(0).get_by_role("button", name="截图保存至当前会话", exact=True).get_attribute("data-supported")
            == "false"
        )
        assert (
            rows.nth(1).get_by_role("button", name="截图保存至当前会话", exact=True).get_attribute("data-supported")
            == "true"
        )
        assert not any(name in {"capture", "observe"} for name, _ in desktop_backend.calls)
        assert errors == []
    finally:
        context.close()


@pytest.mark.parametrize("width,theme", [(390, "dark"), (1440, "light")])
def test_capture_is_explicit_persistent_preview_is_released_and_late_capture_stays_in_history(
    ui_browser, desktop_server, desktop_backend, width, theme
):
    desktop_backend.report["capabilities"][2].update(
        supported=True, installed=True, permission="not_applicable", available=True, error_code=None
    )
    context = ui_browser.new_context(viewport={"width": width, "height": 1000})
    try:
        context.add_init_script(f"localStorage.setItem('omni_theme', '{theme}')")
        page, errors = connect(context, desktop_server)
        open_desktop(page)
        probe(page)
        list_targets(page)
        expect(page.get_by_role("button", name="截图保存至当前会话", exact=True)).to_be_disabled()
        page.get_by_role("button", name="设置", exact=True).click()
        shared_ui.reveal_settings(page)
        page.locator("#model").fill("fake")
        page.get_by_role("button", name="保存配置", exact=True).click()
        expect(page.locator("#configuration-status")).to_contain_text("修订 2")
        page.get_by_role("button", name="保存设置", exact=True).click()
        expect(page.locator("#saved")).to_contain_text("已保存")
        page.get_by_role("button", name="对话", exact=True).click()
        page.locator("#message").fill("isolated capture session")
        page.get_by_role("button", name="发送", exact=True).click()
        expect(page.locator("#messages")).to_contain_text("V2 test reply", timeout=15_000)
        open_desktop(page)
        list_targets(page)
        assert not any(name == "capture" for name, _ in desktop_backend.calls)
        with page.expect_response(lambda r: r.url.endswith("/desktop/capture")) as result:
            page.get_by_role("button", name="截图保存至当前会话", exact=True).click()
        assert result.value.status == 200, result.value.text()
        expect(page.locator("#desktop-captures article")).to_have_count(1)
        expect(page.locator("#desktop-captures")).to_contain_text("未附加到模型请求")
        page.get_by_role("button", name="查看截图", exact=True).click()
        picture = page.locator("#desktop-captures img")
        expect(picture).to_be_visible()
        page.wait_for_function("() => document.querySelector('#desktop-captures img').naturalWidth === 32")
        assert page.evaluate("document.documentElement.scrollWidth <= innerWidth")
        install_response_gates(page)
        hold(page, "/desktop/capture")
        page.get_by_role("button", name="截图保存至当前会话", exact=True).click()
        page.wait_for_function("() => window.__desktopHeld.length === 1")
        page.locator('nav[aria-label="工作台"] [data-page="memory"]').click()
        release(page)
        expect(page.locator("#desktop-captures")).to_be_empty()
        open_desktop(page)
        page.locator("#desktop-captures-refresh").click()
        expect(page.locator("#desktop-captures article")).to_have_count(2)
        assert page.locator("#desktop-captures img[src]").count() == 0
        page.get_by_role("button", name="移除会话引用", exact=True).first.click()
        expect(page.locator("#desktop-captures article")).to_have_count(1)
        assert errors == []
    finally:
        context.close()


def test_ui_observation_is_explicit_text_only_and_late_content_cannot_reappear(
    ui_browser, desktop_server, desktop_backend
):
    desktop_backend.report["capabilities"][1].update(
        supported=True, installed=True, permission="not_applicable", available=True, error_code=None
    )
    context = ui_browser.new_context(viewport={"width": 1024, "height": 900})
    try:
        page, errors = connect(context, desktop_server)
        open_desktop(page)
        probe(page)
        list_targets(page)
        assert not any(name == "observe" for name, _ in desktop_backend.calls)
        with page.expect_response(lambda r: r.url.endswith("/desktop/observe")) as response:
            page.get_by_role("button", name="读取界面内容", exact=True).click()
        assert response.value.status == 200
        expect(page.get_by_label("本次界面观察")).to_contain_text("<fixture-private-ui>")
        assert page.locator("fixture-private-ui").count() == 0
        install_response_gates(page)
        hold(page, "/desktop/observe")
        page.get_by_role("button", name="读取界面内容", exact=True).click()
        page.wait_for_function("() => window.__desktopHeld.length === 1")
        page.locator('nav[aria-label="工作台"] [data-page="memory"]').click()
        release(page)
        expect(page.locator("#desktop-targets")).to_have_text("")
        open_desktop(page)
        expect(page.locator("#desktop-targets")).to_have_text("")
        assert errors == []
    finally:
        context.close()


def test_desktop_requires_explicit_native_queries_and_releases_observations_on_panel_leave(
    ui_browser, desktop_server, desktop_backend
):
    context = ui_browser.new_context(viewport={"width": 390, "height": 1000})
    try:
        context.add_init_script("localStorage.setItem('omni_theme', 'dark')")
        page, errors = connect(context, desktop_server)
        agent = page.locator("#agents").input_value()
        assert desktop_backend.calls == []
        view_id = open_desktop(page)
        expect(page.locator("#desktop-summary")).to_contain_text("尚未检查")
        expect(page.locator("#desktop-list")).to_be_disabled()
        assert desktop_backend.calls == []
        checked = probe(page)
        assert checked["capabilities"]["acceptance"] == "pending"
        expect(page.locator("#desktop-summary")).to_contain_text("完整验收仍待完成")
        expect(page.locator("#desktop-capabilities article")).to_have_count(4)
        observed = list_targets(page)
        assert observed["observation_only"] is True
        assert set(observed["items"][0]) == {"target_id", "application", "pid", "backend", "can_observe", "can_capture"}
        assert observed["items"][0]["backend"] == "win32"
        assert observed["items"][0]["can_observe"] is True and observed["items"][0]["can_capture"] is True
        rendered = page.locator("#desktop-targets").inner_text()
        assert "fixture.exe" in rendered and "4123" in rendered
        assert "window:0x1234" not in rendered and "fixture-session" not in rendered
        with page.expect_response(lambda response: response.url.endswith("/desktop/verify")) as verified:
            page.locator("#desktop-targets").get_by_role("button", name="重新核验身份", exact=True).click()
        assert verified.value.status == 200 and verified.value.json()["matches"] is True
        assert verified.value.request.post_data_json == {
            "consent": True,
            "view_id": view_id,
            "snapshot_id": observed["snapshot_id"],
            "target_id": observed["items"][0]["target_id"],
        }
        expect(page.locator("#desktop-targets")).to_contain_text("不授予点击、输入或截图权限")
        assert [operation for operation, _ in desktop_backend.calls] == ["probe", "targets", "verify"]
        with page.expect_response(
            lambda response: response.request.method == "DELETE" and "/desktop/snapshot?" in response.url
        ) as forgotten:
            page.locator('nav[aria-label="工作台"] [data-page="memory"]').click()
        assert forgotten.value.status == 200 and forgotten.value.json()["forgotten"] is True
        assert parse_qs(urlsplit(forgotten.value.url).query) == {"view_id": [view_id]}
        assert status(context, desktop_server, agent)["snapshot_id"] is None
        expect(page.locator("#desktop-targets")).to_have_text("")
        assert page.evaluate("document.documentElement.scrollWidth <= window.innerWidth")
        assert errors == []
    finally:
        context.close()


@pytest.mark.parametrize("v2_server", ["two_agents"], indirect=True)
def test_late_agent_and_panel_responses_cannot_clear_a_new_view_snapshot(ui_browser, desktop_server, desktop_backend):
    context = ui_browser.new_context(viewport={"width": 1440, "height": 1000})
    try:
        page, errors = connect(context, desktop_server)
        first = page.locator("#agents").input_value()
        other = next(
            value
            for value in page.locator("#agents option").evaluate_all("nodes => nodes.map(n => n.value)")
            if value != first
        )
        open_desktop(page)
        probe(page)
        install_response_gates(page)
        desktop_backend.items = [target_identity(application_id="fixture-first-agent.exe")]
        hold(page, "/desktop/targets")
        page.locator("#desktop-list").click()
        page.wait_for_function("() => window.__desktopHeld.length === 1")
        assert page.evaluate("window.__desktopHeld[0].body.items[0].application") == "fixture-first-agent.exe"
        page.locator("#agents").select_option(other)
        expect(page.locator("nav")).not_to_have_attribute("inert", "")
        expect(page.locator("#desktop-probe")).to_be_enabled()
        probe(page)
        desktop_backend.items = [target_identity(application_id="fixture-current-agent.exe", native_id="window:second")]
        current = list_targets(page)
        release(page)
        expect(page.locator("#desktop-targets")).to_contain_text("fixture-current-agent.exe")
        expect(page.locator("#desktop-targets")).not_to_contain_text("fixture-first-agent.exe")
        assert status(context, desktop_server, other)["snapshot_id"] == current["snapshot_id"]

        hold(page, "/desktop/targets")
        page.locator("#desktop-list").click()
        page.wait_for_function("() => window.__desktopHeld.length === 1")
        old_view = page.evaluate("window.__desktopHeld[0].body.snapshot_id")
        assert old_view != current["snapshot_id"]
        hold(page, "/desktop/snapshot", method="DELETE", before=True)
        page.locator('nav[aria-label="工作台"] [data-page="memory"]').click()
        page.wait_for_function("() => window.__desktopHeld.length === 2")
        open_desktop(page)
        desktop_backend.items = [target_identity(application_id="fixture-new-view.exe", native_id="window:third")]
        latest = list_targets(page)
        release(page, 0)  # Old targets response itself also sends conditional cleanup for its captured view.
        late_delete = release(page, 0)
        assert late_delete == {"status": 200, "body": {"forgotten": False, "observation_only": True}}
        expect(page.locator("#desktop-targets")).to_contain_text("fixture-new-view.exe")
        assert status(context, desktop_server, other)["snapshot_id"] == latest["snapshot_id"]
        with page.expect_response(lambda response: response.url.endswith("/desktop/verify")) as verified:
            page.locator("#desktop-targets").get_by_role("button", name="重新核验身份", exact=True).click()
        assert verified.value.status == 200 and verified.value.json()["matches"] is True
        assert errors == []
    finally:
        context.close()


def test_unavailable_permissions_expired_observations_and_errors_recover_without_hidden_polling(
    ui_browser, desktop_server, desktop_backend
):
    context = ui_browser.new_context(viewport={"width": 760, "height": 1000})
    try:
        page, errors = connect(context, desktop_server)
        agent = page.locator("#agents").input_value()
        open_desktop(page)
        desktop_backend.report = probe_report(available=False)
        desktop_backend.report["capabilities"][0]["permission"] = "denied"
        desktop_backend.report["capabilities"][0]["recovery"] = ["Fixture permission must be restored explicitly."]
        probe(page)
        expect(page.locator("#desktop-list")).to_be_disabled()
        expect(page.locator("#desktop-capabilities")).to_contain_text("权限未允许")
        expect(page.locator("#desktop-capabilities")).to_contain_text("restored explicitly")
        desktop_backend.report = probe_report()
        probe(page)
        desktop_backend.errors["targets"] = BusinessError(
            "desktop_native_unavailable", "Fixture unavailable", status=503
        )
        with page.expect_response(lambda response: response.url.endswith("/desktop/targets")) as unavailable:
            page.locator("#desktop-list").click()
        assert unavailable.value.status == 503
        expect(page.locator("#desktop-status")).to_contain_text("desktop_native_unavailable")
        expect(page.locator("#desktop-list")).to_be_enabled()
        del desktop_backend.errors["targets"]
        list_targets(page)
        desktop_server.app.state.desktop_fixture_clock.advance(61)
        sent_before_expiry = len(desktop_backend.calls)
        with page.expect_response(lambda response: response.url.endswith("/desktop/verify")) as expired:
            page.locator("#desktop-targets").get_by_role("button", name="重新核验身份", exact=True).click()
        assert expired.value.status == 410 and expired.value.json()["error"]["code"] == "desktop_snapshot_expired"
        assert len(desktop_backend.calls) == sent_before_expiry
        expect(page.locator("#desktop-targets")).to_have_text("")
        expect(page.locator("#desktop-status")).to_contain_text("观察已过期")
        list_targets(page)
        desktop_backend.match = False
        with page.expect_response(lambda response: response.url.endswith("/desktop/verify")) as changed:
            page.locator("#desktop-targets").get_by_role("button", name="重新核验身份", exact=True).click()
        assert changed.value.status == 200 and changed.value.json()["matches"] is False
        expect(page.locator("#desktop-targets")).to_contain_text("身份已变化")
        expect(page.locator("#desktop-targets").get_by_role("button", name="重新核验身份", exact=True)).to_have_count(0)
        list_targets(page)
        requests = []
        page.on("request", lambda request: requests.append(request.url) if "/desktop" in request.url else None)
        with page.expect_response(
            lambda response: response.request.method == "DELETE" and "/desktop/snapshot?" in response.url
        ) as hidden:
            page.evaluate(
                """() => {
                  Object.defineProperty(document, 'hidden', {configurable:true, get:() => true});
                  document.dispatchEvent(new Event('visibilitychange'));
                }"""
            )
        assert hidden.value.status == 200 and hidden.value.json()["forgotten"] is True
        assert status(context, desktop_server, agent)["snapshot_id"] is None
        requests.clear()
        native_count = len(desktop_backend.calls)
        page.clock.install()
        page.clock.fast_forward(120000)
        assert requests == [] and len(desktop_backend.calls) == native_count
        expect(page.locator("#desktop-targets")).to_have_text("")
        expect(page.locator("#desktop-probe")).to_be_disabled()
        page.evaluate(
            """() => {
              Object.defineProperty(document, 'hidden', {configurable:true, get:() => false});
              document.dispatchEvent(new Event('visibilitychange'));
            }"""
        )
        expect(page.locator("#desktop-probe")).to_be_enabled()
        assert len(desktop_backend.calls) == native_count
        assert errors == []
    finally:
        context.close()
