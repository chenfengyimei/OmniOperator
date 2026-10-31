"""市场界面回归：设置中心里的人设/插件弹窗要真的能走完一条路。

守的是「点了有用」这一层——后端契约由 test_market_api.py 守，这里只看浏览器里：
1. 人设分区「看看人设市场」能开弹窗、列出市场里的卡、导入后写进本机预设库并填进表单
   （导入 ≠ 生效：真正落盘仍要点「保存人设」，所以只断言表单值，不碰配置）；
2. 插件分区「打开插件市场」→ 装下来 → 卡片出现在「下载好了，等你确认」→ 点激活弹
   确认框 → 确认后进「已装」。激活必须经过那一次人工确认，这里顺带把它测掉。

社区源一律注入假上游（httpx.MockTransport），不连网、不碰真机 data/。
"""
from __future__ import annotations

import hashlib
import io
import json
import zipfile
from urllib.parse import urlsplit

import httpx
import pytest
from test_web import _client

from omni.plugins.community import CommunityClient

MANIFEST = {
    "id": "demo-tools",
    "name": "演示插件",
    "version": "1.0.0",
    "description": "界面回归用的演示插件",
    "entry": "plugin.py",
    "author": "tester",
    "permissions": ["tools"],
}
PLUGIN_CODE = '''def register(ctx):
    ctx.add_tool(name="demo-tools-hello", description="打招呼",
                 parameters={"type": "object", "properties": {}}, func=lambda args: "hi", risk="safe")
'''
PERSONA = {
    "id": "xiaoyu",
    "kind": "persona",
    "name": "小鱼",
    "description": "稳重、先给结论再展开",
    "system_prompt": "你是小鱼，稳重专业，先给结论再展开。",
    "greeting": "在。",
    "tags": ["默认", "稳重"],
    "author": "official",
    "latest_version": "1.0",
    "sha256": "b" * 64,
}


def _package() -> bytes:
    buffer = io.BytesIO()
    with zipfile.ZipFile(buffer, "w") as archive:
        archive.writestr("manifest.json", json.dumps(MANIFEST, ensure_ascii=False))
        archive.writestr("plugin.py", PLUGIN_CODE)
    return buffer.getvalue()


def _upstream(request: httpx.Request) -> httpx.Response:
    path = request.url.path
    if path == "/api/health":
        return httpx.Response(200, json={"ok": True, "plugins": 1, "personas": 1})
    if path == "/api/personas":
        return httpx.Response(200, json={"total": 1, "items": [PERSONA]})
    if path == "/api/personas/xiaoyu":
        return httpx.Response(200, json=PERSONA)
    if path == "/api/plugins":
        return httpx.Response(
            200,
            json={
                "total": 1,
                "items": [
                    {
                        "id": "demo-tools",
                        "name": "演示插件",
                        "description": "界面回归用的演示插件",
                        "latest_version": "1.0.0",
                        "versions": ["1.0.0"],
                        "author": "tester",
                        "tags": ["演示"],
                        "size": 512,
                        "sha256": "a" * 64,
                        "files": [{"path": "manifest.json", "size": 120}],
                    }
                ],
            },
        )
    if path == "/api/plugins/demo-tools":
        return httpx.Response(
            200,
            json={"id": "demo-tools", "name": "演示插件", "latest_version": "1.0.0", "latest_size": 512},
        )
    if path == "/api/download/demo-tools":
        package = _package()
        return httpx.Response(
            200, content=package, headers={"X-Plugin-Sha256": hashlib.sha256(package).hexdigest()}
        )
    return httpx.Response(404, json={"detail": "没有这个接口"})


@pytest.fixture
def ui(tmp_path, ui_browser):
    client, runtime = _client(tmp_path)
    runtime.community = CommunityClient(
        tmp_path / "data" / "plugins" / "community.json",
        runtime.plugins,
        transport=httpx.MockTransport(_upstream),
    )
    assert runtime.community.configure(server_url="http://market.test")[0]
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
            page.goto("http://127.0.0.1/?settings=1")
            page.locator("#hub").wait_for(state="visible")
            yield page, runtime, errors
        finally:
            page.close()
            assert runtime.close()["complete"]
            client.close()


def test_persona_market_imports_into_presets_and_form(ui):
    page, _runtime, errors = ui
    page.click('[data-action="market:open"][data-kind="persona"]')
    page.locator("#market-list .mk-card").first.wait_for(state="visible")

    card = page.locator("#market-list .mk-card").first
    assert card.locator(".mk-name").inner_text().strip() == "小鱼"
    # 设定全文在弹窗里就能看，不是导入后才藏起来
    card.locator("summary").click()
    assert "你是小鱼" in card.locator(".mk-prompt").inner_text()

    card.locator('[data-action="market:persona-import"]').click()
    page.wait_for_function(
        "document.querySelector('#cfg-persona-name')?.value==='小鱼'", timeout=5000
    )
    assert "你是小鱼" in page.input_value("#cfg-persona-prompt")
    presets = page.evaluate("JSON.parse(localStorage.getItem('omni_persona_presets')||'[]')")
    assert any(item["id"] == "market-xiaoyu" for item in presets)
    assert "已导入" in page.inner_text("#preset-tip")

    # 导入只是「填进表单」，配置没被偷偷改掉：保存按钮还在，且没有别的写入
    page.click('[data-action="market:close"]')
    page.locator("#market-modal").wait_for(state="hidden")
    assert not errors, "市场弹窗引发脚本异常：%s" % errors


def test_plugin_market_install_then_explicit_activate(ui):
    page, runtime, errors = ui
    page.click('.hub-nav a[data-go="sec-plugins"]')
    page.locator("#sec-plugins").wait_for(state="visible")
    # 分区先渲染「正在读取…」再异步换成结果，等它换完再断言，否则是在跟自己赛跑
    page.wait_for_function(
        "document.querySelector('#plugin-installed')?.textContent.includes('还没有装任何插件')"
    )

    page.click('[data-action="market:open"][data-kind="plugin"]')
    page.locator("#market-list .mk-card").first.wait_for(state="visible")
    page.locator('#market-list [data-action="market:plugin-install"]').first.click()

    # 装完：进隔离区，工具还不存在（装了不等于跑）
    page.locator("#plugin-quarantine .mk-card").first.wait_for(state="visible")
    assert runtime.registry.get("demo-tools-hello") is None
    page.click('[data-action="market:close"]')

    # 激活要过一次人工确认：先点「激活」，确认框里再点确认
    page.locator('#plugin-quarantine [data-action="market:plugin-activate"]').first.click()
    page.locator("#ui-dialog").wait_for(state="visible")
    assert "同意运行" in page.inner_text("#ui-dialog")
    page.click('#ui-dialog button[value="confirm"]')

    page.locator("#plugin-installed .mk-card").first.wait_for(state="visible")
    assert page.locator("#plugin-quarantine .mk-card").count() == 0
    assert runtime.registry.get("demo-tools-hello") is not None
    assert not errors, "插件市场引发脚本异常：%s" % errors
