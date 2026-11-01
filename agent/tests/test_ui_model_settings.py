"""设置中心「模型」页：选完 → 保存 → 重新打开还在（真实 Chromium 端到端）。

背景（用户实测）：模型明明选过，刷新或重启后再进设置中心又变回"没选"，只能再选
一遍。根因有三层，本文件逐条钉住：

1. `GET /api/system/config` 以前读的是**进程启动那一刻的快照**，所以保存成功后
   重新打开页面看到的还是旧值——用户以为没存上，于是再选一遍，第二遍又覆盖第一遍。
   现在 GET 读磁盘，页面看到的等于文件里的（端到端锁住：reload 后必须看到刚存的型号）。
2. 列表里没有的型号（比如新发布的名字）以前在卡片上退化成槽位说明文案，和"没选"
   长得一模一样；现在一律显示原始模型串。
3. 再点一次同一家供应商卡会把已选型号重置成内置列表第一个——这是"选过又变没选"
   最隐蔽的一种成因，静态守卫锁住 pickProvider 的早退分支。

另外钉住 D30 的思考开关：档位能存能回读，且前后端档位集合必须一致（不一致的
表现是界面给了一个后端不认的值，保存直接 400）。
"""
from __future__ import annotations

import re
from pathlib import Path
from urllib.parse import urlsplit

import pytest
from test_web import _client

STATIC = Path(__file__).resolve().parents[1] / "src" / "omni" / "channels" / "web_static"
APP = STATIC / "ui" / "app"


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


def open_models(page):
    """从工作台进设置中心 → 模型分区，等槽位卡渲染出来。"""
    page.goto("http://127.0.0.1/")
    page.wait_for_selector(".hub-open")
    page.click(".hub-open")
    page.wait_for_selector("#hub:not(.hidden)")
    page.click('.hub-nav a[data-go="sec-models"]')
    page.wait_for_selector('#model-slots .slot-card[data-kind="chat"]')


def chat_slot_text(page):
    return page.text_content('#model-slots .slot-card[data-kind="chat"] .sc-model') or ""


def test_chosen_model_survives_reload(ui):
    """选一个非默认型号 → 保存 → 刷新页面 → 再进设置中心，它还在。"""
    page, _client_, runtime, errors = ui
    open_models(page)

    page.click('#model-slots .slot-card[data-kind="chat"]')
    page.click('[data-action="models:pick-provider"][data-prov="deepseek"]')
    page.click('[data-action="models:pick-model"][data-model="deepseek-v4-pro"]')
    assert "deepseek/deepseek-v4-pro" in page.text_content("#mk-model")

    page.click('[data-action="hub:save"][data-scope="models"]')
    page.wait_for_function(
        "() => (document.querySelector('#save-msg-models')?.textContent || '').includes('已保存')"
    )

    from omni.config import load_config

    assert load_config(runtime.paths.config).models.chat == "deepseek/deepseek-v4-pro"

    # 关键断言：刷新后看到的必须是文件里的值，而不是启动快照
    open_models(page)
    assert "DeepSeek V4 Pro" in chat_slot_text(page)
    assert "还没选供应商" not in chat_slot_text(page)
    assert errors == []


def test_reasoning_level_survives_reload(ui):
    """思考档位（D30）存得下、回得来；回读后按钮高亮在刚选的那一档。"""
    page, _client_, runtime, errors = ui
    open_models(page)
    page.click('#model-slots .slot-card[data-kind="chat"]')
    page.click('[data-action="models:pick-provider"][data-prov="deepseek"]')
    page.click('[data-action="models:set-reasoning"][data-level="high"]')
    page.click('[data-action="hub:save"][data-scope="models"]')
    page.wait_for_function(
        "() => (document.querySelector('#save-msg-models')?.textContent || '').includes('已保存')"
    )

    from omni.config import load_config

    assert load_config(runtime.paths.config).reasoning.chat == "high"

    open_models(page)
    assert (page.text_content(".rsg-btn.active") or "").strip() == "高"
    assert errors == []


def test_clearing_a_slot_writes_empty_and_does_not_block_save(ui):
    """「看截图」留空 = 跟随日常对话：以前空值会让整次保存 400，界面只显示"保存失败"。"""
    page, _client_, runtime, errors = ui
    open_models(page)
    page.click('#model-slots .slot-card[data-kind="vision"]')
    page.click('[data-action="models:pick-provider"][data-prov="deepseek"]')
    page.click('[data-action="models:clear-slot"]')
    page.click('[data-action="hub:save"][data-scope="models"]')
    page.wait_for_function(
        "() => (document.querySelector('#save-msg-models')?.textContent || '').includes('已保存')"
    )

    from omni.config import load_config

    assert load_config(runtime.paths.config).models.vision == ""
    open_models(page)
    assert "跟随对话" in page.text_content('#model-slots .slot-card[data-kind="vision"] .sc-badge')
    assert errors == []


# ---------------------------------------------------------------- 静态守卫（不启浏览器）


def _hub_models_text():
    return (APP / "hub-models.js").read_text(encoding="utf-8")


def test_catalog_knows_current_deepseek_models():
    """目录里必须有现役型号，旧名标成"已下线"而不是继续当成推荐。"""
    text = _hub_models_text()
    assert "{id:'deepseek-flash'" in text
    assert "{id:'deepseek-v4-pro'" in text
    # 旧名保留（有人已经配了它），但必须带 retired 标记，不再当默认推荐
    assert re.search(r"\{id:'deepseek-chat'[^}]*retired:true", text)
    assert re.search(r"\{id:'deepseek-reasoner'[^}]*retired:true", text)


def test_unknown_model_still_renders_as_chosen():
    """认不出的型号必须显示原文：退回槽位说明文案 = 界面上"已选"和"没选"无法区分。"""
    text = _hub_models_text()
    assert "function slotSubtitle(" in text
    # 副标题兜底必须落到原始模型串
    assert re.search(r"function slotSubtitle\([\s\S]{0,600}?return st\.modelStr", text)


def test_repicking_same_provider_keeps_chosen_model():
    """点已选中的供应商卡不能把型号重置成列表第一个。"""
    text = _hub_models_text()
    body = re.search(r"function pickProvider\(provId\)\s*\{(.*?)\n\}", text, re.S)
    assert body, "找不到 pickProvider"
    assert "st.providerId===provId&&st.modelStr" in body.group(1)


def test_frontend_and_backend_reasoning_levels_match():
    """前后端档位集合必须一致：界面给后端不认的值，保存会直接 400。"""
    from omni.config import REASONING_LEVELS, REASONING_SLOTS
    from omni.system.config_io import EDITABLE

    text = _hub_models_text()
    block = re.search(r"const REASONING_LEVELS=\[(.*?)\];", text, re.S)
    assert block, "hub-models.js 里找不到 REASONING_LEVELS"
    front = re.findall(r"\{id:'([a-z]+)'", block.group(1))
    assert tuple(front) == REASONING_LEVELS, (front, REASONING_LEVELS)

    # 每个槽位都必须可写：漏一个键 = 那一档的开关存不下去
    for slot in REASONING_SLOTS:
        assert ("reasoning", slot) in EDITABLE, f"config_io.EDITABLE 缺少 reasoning.{slot}"


def test_save_scope_covers_reasoning():
    """「保存模型设置」必须把 reasoning 一起提交，否则档位只在页面上热闹。"""
    text = (APP / "hub.js").read_text(encoding="utf-8")
    scope = re.search(r"models:\[([^\]]+)\]", text)
    assert scope and "'reasoning'" in scope.group(1)
    assert "getReasoningSlotValue(" in text
