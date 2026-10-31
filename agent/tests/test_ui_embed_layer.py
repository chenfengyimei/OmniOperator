"""内嵌层（embed）与终端主题桥的守卫。

背景：
- embed 降级规则原先只写在 storage.css（12 处）与 memory.css（6 处）里，
  frame.css / base.css / components.css 中 ui-embed 出现 0 次——
  这正是「5 个子站无法复用 embed 能力、嵌进去露出自身 header 与 rail 占位」的根因。
- 终端的 xterm 主题写死 #0e1116 / #dbe7ee，不随站点深浅色翻转；
  且主题切换事件是 document 级的，iframe 内页收不到父页的切换。
"""
import re
from pathlib import Path
from urllib.parse import urlsplit

import pytest
from test_web import _client

STATIC = Path(__file__).resolve().parents[1] / "src" / "omni" / "channels" / "web_static"

EMBED_PAGES = ["terminal", "android", "storage"]


def _read(rel):
    return (STATIC / rel).read_text(encoding="utf-8")


def test_embed_rules_live_only_in_frame_css():
    """通用 embed 降级规则（去 chrome / 布局归零）全站只有 ui/frame.css 一处。

    散在页面级 css 里的后果：新内嵌页要复用就得把别人的规则抄一遍，
    抄漏一条就露出自身 chrome。

    页面**专属**的 embed 尺寸约束（终端/安卓的视口单位改内容撑开）允许留在页面 css——
    那是那一页自己的布局问题，不通用。
    """
    # chrome 去除与布局归零的特征串：只在「带 ui-embed 的那条规则」里才算通用降级规则。
    # （非 embed 上下文里出现 padding:0 或 >header 是正常写法，不能误判。）
    common_marks = [">header", ">.ui-global-nav", "padding:0!important"]
    offenders = {}
    for path in STATIC.rglob("*.css"):
        if "vendor" in path.parts:
            continue
        rel = path.relative_to(STATIC).as_posix()
        if rel == "ui/frame.css":
            continue
        text = path.read_text(encoding="utf-8")
        # 规则块以 } 结尾；一个块里同时出现 ui-embed 与 chrome 特征串 = 重复实现了通用降级
        hits = []
        for block in text.split("}"):
            if "ui-embed" not in block:
                continue
            hits.extend(mark for mark in common_marks if mark in block)
        if hits:
            offenders[rel] = sorted(set(hits))
    assert not offenders, (
        "通用 embed 降级规则只准写在 ui/frame.css；页面级 css 里的副本已移除：%s" % offenders
    )


def test_frame_css_has_shared_embed_layer():
    """共享层必须同时覆盖 html.ui-embed 与 body.ui-embedded 两侧。

    shared.js 同时打这两个 class：html 侧负责背景透明（首帧即生效），
    body 侧负责布局归零。只写一侧会出现「背景透明了但留白还在」。
    """
    frame = _read("ui/frame.css")
    assert "html.ui-embed body[data-ui-page]" in frame
    assert "body[data-ui-page].ui-embedded" in frame
    # chrome 去除：header 与全局导航
    assert "html.ui-embed body[data-ui-page]>header" in frame
    assert "body[data-ui-page].ui-embedded>header" in frame


@pytest.mark.parametrize("page", EMBED_PAGES)
def test_embed_pages_load_height_reporter(page):
    """三个内嵌页都要引高度上报脚本——否则 iframe 停在兜底固定高度。"""
    html = _read("%s/index.html" % page)
    assert "/ui/embed-height.js?v=" in html, "%s 页未引 embed-height.js" % page


def test_height_reporter_uses_documented_protocol():
    """高度协议的消息名与字段名是宿主与子页之间的契约，改名要一起改。"""
    script = _read("ui/embed-height.js")
    assert "omni:'embed-height'" in script
    assert "location.origin" in script, "postMessage 必须限定同源"
    assert "ResizeObserver" in script, "内容变化要能触发重新上报"


def test_terminal_theme_bridge_reads_tokens_not_literals():
    """终端主题桥只读 token，不定义任何颜色。

    写死颜色就等于又多一个真源——浅色主题下终端会跟页面割裂。
    """
    script = _read("ui/terminal-theme.js")
    assert "resolve('--surface-inset')" in script
    assert "resolve('--text-1')" in script
    assert "resolve('--seed-accent')" in script
    # 不得出现裸色值
    assert not re.search(r"#[0-9a-fA-F]{3,8}\b", script), "终端主题桥不得写死颜色"
    assert "omni:theme-change" in script, "必须订阅主题变化事件"


def test_terminal_page_loads_theme_bridge_before_terminal_js():
    """主题桥必须在 terminal.js 之前加载——terminal.js 顶层就取 theme。"""
    html = _read("terminal/index.html")
    bridge = html.find("/ui/terminal-theme.js")
    terminal = html.find("/terminal/terminal.js")
    assert bridge != -1, "terminal 页未引 terminal-theme.js"
    assert terminal != -1
    assert bridge < terminal, "terminal-theme.js 必须排在 terminal.js 之前"


def test_terminal_js_no_longer_hardcodes_xterm_colors():
    """终端不再写死 xterm 配色——否则浅色主题下仍是深蓝黑。"""
    js = _read("terminal/terminal.js")
    assert "#0e1116" not in js and "#dbe7ee" not in js, "xterm 主题仍在写死颜色"
    assert "OmniTerminalTheme" in js, "terminal.js 未接入主题桥"
    assert "--font-mono" in js, "终端字体应走 --font-mono token"


def test_theme_js_syncs_across_documents():
    """theme.js 必须监听 storage 事件。

    omni:theme-change 是 document 级事件，跨 document 不传播；
    终端页是 iframe 里的独立文档，只能靠 localStorage 的 storage 事件同步。
    """
    script = _read("ui/theme.js")
    assert "addEventListener('storage'" in script, "theme.js 未监听 storage 事件"
    # 系统偏好变化时也要补派发，否则系统切浅色时终端不跟随
    assert script.count("omni:theme-change") >= 3, "系统偏好变化分支未派发主题变化事件"


def test_workbench_no_longer_loads_xterm():
    """工作台不再直接跑 xterm（终端已改为 iframe 内嵌），这两个请求是白付的。"""
    html = _read("index.html")
    assert "/vendor/xterm.js" not in html
    assert "/vendor/xterm.min.css" not in html


def test_vendor_assets_carry_cache_versions():
    """vendor 资源也要带 ?v=（规范 §10）。"""
    for page in EMBED_PAGES:
        html = _read("%s/index.html" % page)
        for match in re.findall(r'/vendor/[^"\']+', html):
            assert "?v=" in match, "vendor 资源缺版本串：%s（%s 页）" % (match, page)


@pytest.fixture
def ui(tmp_path):
    """本文件自带浏览器 fixture，不依赖 conftest 的 ui_browser。

    理由：本文件要能独立提交、独立跑绿——依赖工作区里尚未落库的 fixture 会让
    「只应用本文件的改动」时直接红。
    """
    from playwright.sync_api import sync_playwright

    client, runtime = _client(tmp_path)
    with sync_playwright() as playwright:
        browser = playwright.chromium.launch()
        context = browser.new_context()
        page = context.new_page()
        errors = []
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
            context.close()
            browser.close()
            assert runtime.close()["complete"]
            client.close()


def test_embedded_page_reports_its_height_to_host(ui):
    """内嵌页上报内容高度，宿主据此写 iframe 高度（而不是停在兜底固定值）。"""
    page, _client_, _runtime, errors = ui
    page.goto("http://127.0.0.1/?settings=1")
    page.locator("#hub").wait_for(state="visible")
    page.locator('.hub-nav a[data-go="sec-storage"]').click()
    page.wait_for_selector('iframe[data-embed-page="storage"]')
    page.wait_for_function(
        "()=>{const f=document.querySelector('iframe[data-embed-page=\"storage\"]');"
        "return f&&f.style.height&&parseInt(f.style.height)>100}"
    )
    height = page.evaluate(
        "()=>parseInt(document.querySelector('iframe[data-embed-page=\"storage\"]').style.height)"
    )
    assert 100 < height <= 1600, "iframe 高度应落在钳制区间内，实得 %s" % height
    assert not errors, "内嵌高度上报引发脚本异常：%s" % errors


def test_embedded_page_follows_host_theme(ui):
    """iframe 内页跟随宿主主题切换。

    路径：工作台改 localStorage → storage 事件 → 内嵌页重派发 omni:theme-change。
    """
    page, _client_, _runtime, errors = ui
    page.goto("http://127.0.0.1/?settings=1")
    page.locator("#hub").wait_for(state="visible")
    page.locator('.hub-nav a[data-go="sec-storage"]').click()
    page.wait_for_selector('iframe[data-embed-page="storage"]')
    frame = page.frame_locator('iframe[data-embed-page="storage"]')
    frame.locator("body").wait_for(state="attached")

    host_theme = page.evaluate("document.documentElement.getAttribute('data-theme')")
    assert host_theme in {"light", "dark"}

    page.evaluate("localStorage.setItem('omni_theme','light')")
    page.evaluate("document.documentElement.setAttribute('data-theme','light')")
    page.wait_for_function(
        "()=>{const f=document.querySelector('iframe[data-embed-page=\"storage\"]');"
        "return f&&f.contentDocument&&f.contentDocument.documentElement.getAttribute('data-theme')==='light'}"
    )
    assert not errors, "跨文档主题同步引发脚本异常：%s" % errors
