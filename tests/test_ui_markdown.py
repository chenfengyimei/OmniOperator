"""Markdown 渲染器单测：直接加载 web_static/ui/markdown.js（不启动应用、不依赖任何夹具）。

为什么要测：助手回复直接来自模型，属于不可信输入。渲染器的安全模型是
「先整体转义、再在转义文本上做结构转换」，一旦有人为了支持新语法改成先拼标签
再转义，XSS 就会漏进来——这里把关键边界钉死。
"""
from __future__ import annotations

from pathlib import Path

import pytest
from playwright.sync_api import sync_playwright

ROOT = Path(__file__).resolve().parents[1]
SCRIPT = ROOT / "src" / "omni" / "channels" / "web_static" / "ui" / "markdown.js"


@pytest.fixture(scope="module")
def page():
    with sync_playwright() as playwright:
        browser = playwright.chromium.launch()
        pg = browser.new_page()
        pg.set_content("<body></body>")
        pg.add_script_tag(path=str(SCRIPT))
        try:
            yield pg
        finally:
            browser.close()


def render(page, text: str) -> str:
    return page.evaluate("t=>window.OmniMarkdown.render(t)", text)


def test_inline_formatting(page):
    html = render(page, "**加粗** 与 *斜体*，还有 `code()` 和 ~~删除~~")
    assert "<strong>加粗</strong>" in html
    assert "<em>斜体</em>" in html
    assert "<code>code()</code>" in html
    assert "<del>删除</del>" in html


def test_raw_html_is_escaped(page):
    html = render(page, '<script>alert(1)</script><img src=x onerror=alert(1)>')
    assert "&lt;script&gt;" in html
    assert "<script>" not in html
    assert "<img" not in html


def test_code_fence_is_verbatim(page):
    html = render(page, "```\n**不是加粗** <b>不是标签</b>\n```")
    assert "<pre><code>**不是加粗** &lt;b&gt;不是标签&lt;/b&gt;</code></pre>" in html
    assert "<strong>" not in html


def test_list_and_table(page):
    html = render(page, "- 第一项\n- 第二项\n\n| 盘 | 剩余 |\n|---|---|\n| C: | 11.6 GB |")
    assert "<ul>" in html and "<li>第一项</li>" in html and "<li>第二项</li>" in html
    assert "<table>" in html
    assert "<th>盘</th>" in html and "<td>C:</td>" in html


def test_link_sanitized(page):
    good = render(page, "[文档](https://example.com/a?b=1)")
    assert 'href="https://example.com/a?b=1"' in good
    bad = render(page, "[点我](javascript:alert(1))")
    assert 'href="#"' in bad
    assert "javascript:" not in bad


def test_paragraph_and_soft_breaks(page):
    html = render(page, "第一行\n第二行\n\n新段落")
    assert "<p>第一行<br>第二行</p>" in html
    assert "<p>新段落</p>" in html


def test_headings_and_quote(page):
    html = render(page, "## 小标题\n\n> 引用一句")
    assert "<h4>小标题</h4>" in html
    assert "<blockquote>引用一句</blockquote>" in html
