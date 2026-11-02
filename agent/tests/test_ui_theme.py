"""主题切换行为回归：真实 Chromium 验证三态循环、持久化、跨页一致与无闪烁。

守的是四条：
1. 9 个页面都在 <head> 最前引 /ui/theme.js?v=——引导脚本必须在样式前执行，
   否则浅色用户会先看到一帧暗色（FOUC）；
2. 切换后 <html data-theme> 真的变，刷新后仍是用户选的那个；
3. 跨页面保持（同源 localStorage），子站与工作台一致；
4. 首帧不得出现「暗色闪一下再变浅色」。

设计依据：UI_DESIGN_SYSTEM.md §3.6（主题）。
"""

from __future__ import annotations

from omni.config import PROJECT_ROOT, web_root

from pathlib import Path
from urllib.parse import urlsplit

import pytest
from test_web import _client

ROOT = Path(__file__).resolve().parents[1]
STATIC = web_root(PROJECT_ROOT)

# 9 个页面：工作台 + 7 子站 + 记忆独立页
PAGES = [
    ("/", "workbench"),
    ("/capabilities/", "capabilities"),
    ("/terminal/", "terminal"),
    ("/jobs/", "jobs"),
    ("/mcp/", "mcp"),
    ("/android/", "android"),
    ("/project/", "project"),
    ("/storage/", "storage"),
    ("/memory/", "memory"),
]


@pytest.fixture
def ui(tmp_path, ui_browser):
    client, runtime = _client(tmp_path)
    with ui_browser.new_context() as context:
        page = context.new_page()
        errors: list[str] = []
        page.on("pageerror", lambda error: errors.append(str(error)))

        def route(r):
            url = urlsplit(r.request.url)
            response = client.request(
                r.request.method,
                url.path + ("?" + url.query if url.query else ""),
                content=r.request.post_data,
                headers={"Content-Type": "application/json"},
            )
            r.fulfill(
                status=response.status_code,
                body=response.content,
                headers={"Content-Type": response.headers.get("content-type", "")},
            )

        page.route("**/*", route)
        try:
            yield page, client, runtime, errors
        finally:
            page.close()
            assert runtime.close()["complete"]
            client.close()


def test_theme_boot_script_present_on_all_pages():
    """9 个页面都要在 <head> 引 /ui/theme.js?v=，且位于所有样式表之前。

    顺序是硬要求：引导脚本必须同步执行在首次绘制之前，
    否则浅色主题用户会看到一帧暗色底。
    """
    for path, _ in PAGES:
        html = (STATIC / path.lstrip("/") / "index.html") if path != "/" else (STATIC / "index.html")
        text = html.read_text(encoding="utf-8")
        assert "/ui/theme.js?v=" in text, f"{path} 未引入 /ui/theme.js?v="
        script_at = text.index("/ui/theme.js?v=")
        first_link = text.find("<link")
        assert first_link != -1, f"{path} 没有样式表引用"
        assert script_at < first_link, f"{path} 的 theme.js 必须排在所有 <link> 之前，否则会闪暗色"


def test_theme_toggle_exists_on_all_pages(ui):
    """每页顶栏都有主题切换按钮，点击后主题偏好发生变化。

    从 light 出发点击（light→dark 是确定的变化方向）：auto→light 在系统本身
    偏好浅色时解析出的实际主题同为 light，看不出变化——那是 auto 的固有语义，
    不是缺陷。所以这里断言的是**偏好值**（OmniTheme.pref）而非实际主题。
    """
    page, _client_, _runtime, errors = ui
    for path, _ in PAGES:
        page.goto("http://127.0.0.1" + path)
        page.locator(".theme-toggle").wait_for(state="attached")
        page.evaluate("localStorage.setItem('omni_theme','light')")
        page.reload()
        page.locator(".theme-toggle").wait_for(state="attached")
        before = page.evaluate("window.OmniTheme.pref()")
        page.locator(".theme-toggle").click()
        after = page.evaluate("window.OmniTheme.pref()")
        assert before == "light", f"{path} 重置后偏好不是 light：{before!r}"
        assert after == "dark", f"{path} 点击后偏好未从 light 变到 dark：{after!r}"
        assert page.evaluate("document.documentElement.getAttribute('data-theme')") == "dark"
    assert not errors, f"主题切换引发脚本异常：{errors}"


def test_theme_persists_across_reload(ui):
    """切到浅色 → 刷新 → 仍是浅色（localStorage 持久化）。"""
    page, _client_, _runtime, errors = ui
    page.goto("http://127.0.0.1/")
    page.locator(".theme-toggle").wait_for(state="attached")
    page.evaluate("window.OmniTheme.set('light')")
    assert page.evaluate("document.documentElement.getAttribute('data-theme')") == "light"
    page.reload()
    page.locator(".theme-toggle").wait_for(state="attached")
    assert page.evaluate("document.documentElement.getAttribute('data-theme')") == "light"
    assert page.evaluate("localStorage.getItem('omni_theme')") == "light"
    assert not errors, f"主题持久化引发脚本异常：{errors}"


def test_theme_persists_across_pages(ui):
    """跨页面保持：工作台切浅色 → 子站仍是浅色（同源 localStorage）。"""
    page, _client_, _runtime, errors = ui
    page.goto("http://127.0.0.1/")
    page.locator(".theme-toggle").wait_for(state="attached")
    page.evaluate("window.OmniTheme.set('light')")
    for path, _ in PAGES:
        page.goto("http://127.0.0.1" + path)
        assert page.evaluate("document.documentElement.getAttribute('data-theme')") == "light", f"{path} 未继承浅色主题"
    assert not errors, f"跨页主题继承引发脚本异常：{errors}"


def test_theme_cycle_covers_three_states(ui):
    """三态循环 auto → light → dark → auto。"""
    page, _client_, _runtime, errors = ui
    page.goto("http://127.0.0.1/")
    page.locator(".theme-toggle").wait_for(state="attached")
    page.evaluate("localStorage.removeItem('omni_theme')")
    page.reload()
    page.locator(".theme-toggle").wait_for(state="attached")
    assert page.evaluate("window.OmniTheme.pref()") == "auto"
    page.evaluate("window.OmniTheme.cycle()")
    assert page.evaluate("window.OmniTheme.pref()") == "light"
    page.evaluate("window.OmniTheme.cycle()")
    assert page.evaluate("window.OmniTheme.pref()") == "dark"
    page.evaluate("window.OmniTheme.cycle()")
    assert page.evaluate("window.OmniTheme.pref()") == "auto"
    assert not errors, f"主题三态循环引发脚本异常：{errors}"


def test_theme_follows_system_when_auto(ui):
    """auto 模式下跟随系统 prefers-color-scheme。"""
    page, _client_, _runtime, errors = ui
    page.emulate_media(color_scheme="light")
    page.goto("http://127.0.0.1/")
    page.locator(".theme-toggle").wait_for(state="attached")
    page.evaluate("localStorage.removeItem('omni_theme')")
    page.reload()
    page.locator(".theme-toggle").wait_for(state="attached")
    assert page.evaluate("document.documentElement.getAttribute('data-theme')") == "light"
    page.emulate_media(color_scheme="dark")
    page.reload()
    page.locator(".theme-toggle").wait_for(state="attached")
    assert page.evaluate("document.documentElement.getAttribute('data-theme')") == "dark"
    assert not errors, f"跟随系统主题引发脚本异常：{errors}"


def test_no_flash_on_load(ui):
    """首帧不得出现暗色底后再转浅色。

    做法：add_init_script 在页面任何脚本之前装一个探针，在 DOMContentLoaded
    （即文档解析完毕、浏览器可以开始绘制与交互的时刻）记录 <html> 上的 data-theme。

    引导脚本 theme.js 是 head 内的同步阻塞脚本，它执行完才轮到样式表与后续解析，
    所以 DOMContentLoaded 时 data-theme 必须**已经**是浅色——这正是「首帧即浅色」的
    充要条件。若引导脚本被挪到 body 末尾或改成 async，这里就会读到 null/dark。

    另外实测 body 的计算背景色：浅色主题的 --surface-0 = #f4f6f8。
    """
    page, _client_, _runtime, errors = ui
    page.add_init_script(
        """
        window.__themeAtReady = null;
        document.addEventListener('DOMContentLoaded', function () {
          window.__themeAtReady = document.documentElement.getAttribute('data-theme');
        });
        """
    )
    page.goto("http://127.0.0.1/")
    # 先落盘浅色偏好，再重载让引导脚本在首帧生效
    page.evaluate("localStorage.setItem('omni_theme','light')")
    page.reload()
    page.locator(".theme-toggle").wait_for(state="attached")
    at_ready = page.evaluate("window.__themeAtReady")
    assert at_ready == "light", f"DOMContentLoaded 时主题未就位（读到 {at_ready!r}），浅色用户会闪一下暗色"
    # 实际渲染的背景也必须是浅色（--surface-0 = #f4f6f8）
    background = page.evaluate("getComputedStyle(document.body).backgroundColor")
    assert background == "rgb(244, 246, 248)", f"浅色主题下 body 背景不是浅色底：{background}"
    assert not errors, f"无闪烁检查引发脚本异常：{errors}"


def test_embedded_storage_follows_host_theme(ui):
    """内嵌版 /storage/?embed=1 与宿主同源同 key，主题一致。"""
    page, _client_, _runtime, errors = ui
    page.goto("http://127.0.0.1/storage/?embed=1")
    page.evaluate("localStorage.setItem('omni_theme','light')")
    page.reload()
    assert page.evaluate("document.documentElement.getAttribute('data-theme')") == "light"
    assert not errors, f"内嵌页主题引发脚本异常：{errors}"
