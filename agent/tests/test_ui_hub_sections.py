"""设置中心分区结构与命名统一的守卫。

背景：7 个工具子站并入设置中心后，侧栏从 14 项涨到 20 项、分区从 4 组涨到 5 组；
插件市场并入「工具与设备」、服务与运行并入「平台与通道」后为 22 项；
数字具身（B站/浏览器/手机/QQ + 总览）独立成组后为 23 项、6 组。
「导航名 / 浏览器标题 / 页内 h1」三处说法必须逐字一致——不一致时用户点进去会以为
换了个东西（本轮统一前实际产出过「人工终端 · 人工终端 · OmniOperator」这种重复后缀）。
"""

from omni.config import PROJECT_ROOT, web_root
import re
from pathlib import Path
from urllib.parse import urlsplit

import pytest
from test_web import _client

STATIC = web_root(PROJECT_ROOT)
INDEX = STATIC / "index.html"

# 工具与设备组：7 个工具页里除 storage（已是分区）外新并入的 6 个，加上插件（市场入口）
TOOL_SECTIONS = {
    "sec-terminal": "人工终端",
    "sec-jobs": "后台任务",
    "sec-capabilities": "能力诊断",
    "sec-project": "项目索引",
    "sec-plugins": "插件",
}

# 数字具身组：当前 agent 真正"身处"的平台 + 一张总览卡
EMBODIMENT_SECTIONS = {
    "sec-embodiment": "数字具身总览",
    "sec-media": "哔哩哔哩",
    "sec-browser": "浏览器",
    "sec-android": "手机",
    "sec-qq": "QQ",
}

# 六组固定：核心系统 3 / 对话与模型 3 / 数字具身 5 / 工具与设备 6 / 平台与通道 2 / 文件与用量 4
EXPECTED_GROUPS = {
    "核心系统": {"sec-context", "sec-skills", "sec-memory-system"},
    "对话与模型": {"sec-persona", "sec-models", "sec-memory"},
    "数字具身": set(EMBODIMENT_SECTIONS),
    "工具与设备": set(TOOL_SECTIONS),
    "平台与通道": {"sec-services", "sec-cron"},
    "文件与用量": {"sec-storage", "sec-usage", "sec-mcp", "sec-about"},
}


def _nav_groups(html):
    """解析 .hub-nav：返回 [(分组标题, [(data-go, 标签)])]。"""
    block = re.search(r'<nav class="hub-nav">(.*?)</nav>', html, re.S)
    assert block, "index.html 找不到 .hub-nav 设置中心侧边栏"
    token = re.compile(r'<div class="hub-group">([^<]+)</div>|<a data-go="([^"]+)"[^>]*>([^<]*)</a>')
    groups = []
    for match in token.finditer(block.group(1)):
        title, go, label = match.group(1), match.group(2), match.group(3)
        if title is not None:
            groups.append((title.strip(), []))
        else:
            assert groups, "侧边栏条目 %s 不在任何分组下（游离项）" % go
            groups[-1][1].append((go, label.strip()))
    return groups


def test_hub_nav_has_six_groups_with_expected_members():
    """侧栏六组固定，每组成员写死——分组是导航骨架，改结构必须同时改这里。"""
    groups = _nav_groups(INDEX.read_text(encoding="utf-8"))
    titles = [title for title, _ in groups]
    assert len(titles) == len(set(titles)), "侧边栏分组标题重复：%s" % titles
    assert set(titles) == set(EXPECTED_GROUPS), "侧边栏分组集合不对：%s" % titles
    for title, members in groups:
        assert set(go for go, _ in members) == EXPECTED_GROUPS[title], "「%s」组成员应为 %s，实得 %s" % (
            title,
            sorted(EXPECTED_GROUPS[title]),
            sorted(go for go, _ in members),
        )


def test_hub_nav_entries_match_cards_exactly():
    """侧栏条目与分区卡片一一对应——多一个条目（点了没反应）或少一个卡片（入口是死的）都是错。"""
    html = INDEX.read_text(encoding="utf-8")
    nav = {go for _, members in _nav_groups(html) for go, _ in members}
    cards = set(re.findall(r'<div class="hub-card[^"]*" id="(sec-[a-z-]+)"', html))
    assert nav == cards, "侧栏与分区卡片不一致：仅侧栏有 %s，仅卡片有 %s" % (sorted(nav - cards), sorted(cards - nav))


def test_tool_sections_have_label_and_hint():
    """工具分区与数字具身分区：导航标签 = 卡片 h3，且都要有一句说明（空卡片让人不知道能干什么）。"""
    html = INDEX.read_text(encoding="utf-8")
    labels = {go: label for _, members in _nav_groups(html) for go, label in members}
    for go, expected in {**TOOL_SECTIONS, **EMBODIMENT_SECTIONS, "sec-mcp": "MCP 服务"}.items():
        assert labels.get(go) == expected, "%s 导航标签应为「%s」，实得 %r" % (go, expected, labels.get(go))
        # 卡片内可能嵌任意层 <div>（如右上角「整页打开」入口），截到下一张卡片为止而不是
        # 第一个 </div>，否则说明文案（hint）会落在截断区外被误判缺失。
        card = re.search(
            r'<div class="hub-card[^"]*" id="%s">(.*?)(?=<div class="hub-card[^"]*" id="sec-|\Z)' % re.escape(go),
            html,
            re.S,
        )
        assert card, "index.html 找不到 %s 卡片" % go
        body = card.group(1)
        heading = re.search(r"<h3>([^<]+)</h3>", body)
        assert heading and heading.group(1).strip() == expected, "%s 卡片标题应为「%s」" % (go, expected)
        assert 'class="hint"' in body, "%s 分区缺少说明文案（hint）" % go


def test_page_names_are_consistent_across_nav_title_and_heading():
    """导航名 / <title> / 页内 h1 三处逐字一致。

    权威名字只有一处——ui/shared.js 的 pageMeta.label。
    """
    shared = (STATIC / "ui" / "shared.js").read_text(encoding="utf-8")
    labels = dict(re.findall(r"(\w+):\s*\{\s*icon:\s*'[^']+',\s*label:\s*'([^']+)'\s*\}", shared))
    assert labels, "shared.js 的 pageMeta 解析失败"
    for page, label in labels.items():
        path = STATIC / page / "index.html"
        if not path.exists():
            continue
        html = path.read_text(encoding="utf-8")
        title = re.search(r"<title>([^<]*)</title>", html)
        assert title, "%s 页缺 <title>" % page
        assert title.group(1).strip() == label + " · OmniOperator", (
            "%s 页 <title> 应为「%s · OmniOperator」，实得「%s」" % (page, label, title.group(1))
        )
        heading = re.search(r"<h1>([^<]*)</h1>", html)
        assert heading, "%s 页缺 <h1>" % page
        assert heading.group(1).strip() == label, "%s 页 h1 应为「%s」，实得「%s」" % (page, label, heading.group(1))


@pytest.fixture
def ui(tmp_path, ui_browser):
    """同文件共享浏览器，每例独立上下文和数据库。"""
    client, runtime = _client(tmp_path)
    with ui_browser.new_context() as context:
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
            assert runtime.close()["complete"]
            client.close()


def test_hub_nav_search_filters_sections(ui):
    """侧栏搜索框：输入即筛，组标题在没有可见条目时一并隐藏。"""
    page, _client_, _runtime, errors = ui
    page.goto("http://127.0.0.1/?settings=1")
    page.locator("#hub").wait_for(state="visible")
    search = page.locator("#hub-nav-search")
    search.wait_for(state="visible")

    total = page.locator(".hub-nav a[data-go]").count()
    assert total == 22, "合并重复MCP入口后侧栏应有22个分区，实得 %d" % total

    search.fill("终端")
    page.wait_for_function("document.querySelectorAll('.hub-nav a[data-go]:not([hidden])').length===1")
    visible = page.locator(".hub-nav a[data-go]:not([hidden])")
    assert visible.count() == 1
    assert visible.first.get_attribute("data-go") == "sec-terminal"

    # 组标题在没有可见条目时隐藏——否则会剩一串空标题，像坏了
    assert page.locator(".hub-nav .hub-group:visible").count() == 1

    search.fill("这个分区不存在")
    page.wait_for_function("document.querySelectorAll('.hub-nav a[data-go]:not([hidden])').length===0")
    assert page.locator(".hub-nav-empty").is_visible()

    search.fill("")
    page.wait_for_function("document.querySelectorAll('.hub-nav a[data-go]:not([hidden])').length===22")
    assert not errors, "侧栏搜索引发脚本异常：%s" % errors
