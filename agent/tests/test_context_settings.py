"""设置中心上下文：统计只读、真实缓存字段、配置 CAS 与 Chromium 操作。"""

import json
from pathlib import Path

import pytest

from test_web import _client
from test_ui_flows import ui, delay_script
from omni.core.context import TurnContext


@pytest.fixture
def web(tmp_path):
    client, runtime = _client(tmp_path, config_text="# 保留注释\nreview:\n  enabled: false\n")
    try:
        yield client, runtime
    finally:
        client.close()
        assert runtime.close()["complete"]


def test_context_overview_is_read_only_and_never_calls_model_or_clock(web, monkeypatch):
    client, runtime = web
    sid = runtime.agent.session_id
    runtime.store.append_message(
        sid,
        "user",
        "原始消息",
        prompt_context=TurnContext("2026-10-30T12:00:00+08:00", True, "不得公开的笔记", "不得公开的档案").payload(),
    )

    def forbidden(*args, **kwargs):
        raise AssertionError("统计不能执行回合或调用模型")

    monkeypatch.setattr(runtime.agent, "_clock", forbidden)
    monkeypatch.setattr(runtime.agent.gateway, "complete", forbidden)
    before = runtime.store.message_count(sid)
    result = client.get("/api/context", params={"session_id": sid})
    assert result.status_code == 200, result.text
    context = result.json()["context"]
    assert context["snapshot_count"] == 1 and context["history_count"] == 1
    assert context["segments"][2]["tokens"] > 0
    assert sum(segment["tokens"] for segment in context["segments"]) == context["used_tokens"]
    assert "不得公开" not in result.text and context["summary"] is None
    assert "2026-10-30T12:00:00" not in context["system_prompt"]
    assert client.get("/api/context").json()["context"] == context
    assert client.get("/api/state").json()["context"] == {
        key: value for key, value in context.items() if key != "system_prompt"
    }
    assert runtime.store.message_count(sid) == before and runtime.store.active_summary(sid) is None


def test_context_settings_save_is_pending_and_keeps_comments(web):
    client, runtime = web
    before = client.get("/api/context/settings").json()
    assert not before["restart_required"]
    response = client.put(
        "/api/context/settings",
        json={
            "revision": before["revision"],
            "updates": {
                "limits": {"max_history_messages": 6},
                "compression": {"enabled": False, "keep_recent": 0},
                "memory": {"max_writes_per_day": 0},
            },
        },
    )
    assert response.status_code == 200, response.text
    data = response.json()
    assert data["saved"]["limits"]["max_history_messages"] == 6
    assert data["running"]["limits"]["max_history_messages"] == 40 and data["restart_required"]
    assert data["saved"]["compression"]["keep_recent"] == 0 and data["saved"]["memory"]["max_writes_per_day"] == 0
    assert "# 保留注释" in runtime.paths.config.read_text(encoding="utf-8")
    assert runtime.config.compression.enabled and runtime.config.limits.max_history_messages == 40
    assert client.get("/api/context/settings").json() == data


def test_context_settings_cas_sees_other_settings_updates(web):
    client, runtime = web
    revision = client.get("/api/context/settings").json()["revision"]
    assert client.post("/api/system/config", json={"updates": {"persona": {"name": "另一个页面"}}}).status_code == 200
    before = runtime.paths.config.read_bytes()
    response = client.put(
        "/api/context/settings", json={"revision": revision, "updates": {"limits": {"max_history_messages": 5}}}
    )
    assert response.status_code == 409 and "重新读取" in response.json()["detail"]
    assert runtime.paths.config.read_bytes() == before


def test_general_settings_can_repair_invalid_saved_values(web):
    client, runtime = web
    runtime.paths.config.write_text("limits:\n  max_history_messages: 0\n", encoding="utf-8")
    assert client.get("/api/context/settings").status_code == 409
    response = client.post("/api/system/config", json={"updates": {"limits": {"max_history_messages": 8}}})
    assert response.status_code == 200, response.text
    assert client.get("/api/context/settings").json()["saved"]["limits"]["max_history_messages"] == 8


@pytest.mark.parametrize(
    "updates",
    [
        {"limits": {"max_history_messages": 0}},
        {"limits": {"max_agent_steps": 65}},
        {"compression": {"trigger_chars": -1}},
        {"compression": {"min_messages": 0}},
        {"memory": {"max_writes_per_day": -1}},
        {"review": {"queue_depth": 0}},
        {"persona": {"system_prompt": "不准越界写入"}},
        {"compression": {"made_up": True}},
        {},
    ],
)
def test_context_settings_reject_invalid_updates_without_writing(web, updates):
    client, runtime = web
    revision = client.get("/api/context/settings").json()["revision"]
    before = runtime.paths.config.read_bytes()
    response = client.put("/api/context/settings", json={"revision": revision, "updates": updates})
    assert response.status_code == 422, response.text
    assert runtime.paths.config.read_bytes() == before


def test_context_cache_is_scoped_and_unknown_samples_do_not_count_as_misses(web):
    client, runtime = web
    sid = runtime.agent.session_id
    other = runtime.store.new_session()
    for name, session in [("current", sid), ("other", other)]:
        runtime.runs.create(run_id=name, session_id=session, channel="web", principal="owner", owner="fixture")
    for payload in [
        {"prompt_tokens": 100, "cached_prompt_tokens": 80, "cache_miss_tokens": 20},
        {"prompt_tokens": 50},
        {"prompt_tokens": 20, "cached_prompt_tokens": 0},
    ]:
        runtime.runs.append_event("current", "model_end", payload)
    runtime.runs.append_event("other", "model_end", {"prompt_tokens": 100, "cached_prompt_tokens": 100})
    cache = client.get("/api/context", params={"session_id": sid}).json()["cache"]
    assert cache["sample_count"] == 3 and cache["reported_count"] == 2
    assert cache["cached_tokens"] == 80 and cache["hit_percent"] == 66.7
    assert cache["reported_prompt_tokens"] == 120 and cache["creation_tokens"] is None
    empty = client.get("/api/context", params={"session_id": runtime.store.new_session()}).json()["cache"]
    assert empty["sample_count"] == 0 and empty["cached_tokens"] is None and empty["hit_percent"] is None


def test_context_routes_require_owner_pairing_and_reject_missing_session(web):
    client, runtime = web
    assert client.get("/api/context?session_id=99999").status_code == 404
    revision = client.get("/api/context/settings").json()["revision"]
    body = {"revision": revision, "updates": {"compression": {"enabled": False}}}
    assert (
        client.put("/api/context/settings", json=body, headers={"Origin": "https://outside.invalid"}).status_code == 403
    )
    assert client.get("/api/context", headers={"Host": "outside.invalid"}).status_code == 403
    client.cookies.clear()
    assert client.get("/api/context").status_code == 401
    assert client.get("/api/context/settings").status_code == 401
    assert client.put("/api/context/settings", json=body).status_code == 401


def open_context(page):
    page.goto("http://127.0.0.1/")
    page.locator("#chips").wait_for(state="attached")
    page.locator('[data-action="hub:open"][data-section="sec-context"]').click()
    page.locator("#sec-context:not(.hidden)").wait_for()
    page.wait_for_function('!document.querySelector("#context-controls").disabled')
    page.wait_for_function("document.querySelector('#hub').getAttribute('aria-busy') === 'false'")
    page.locator("#context-metrics .metric-card").first.wait_for()


def test_settings_context_stats_form_and_pending_configuration(ui):
    page, client, runtime, errors = ui
    open_context(page)
    assert page.locator("#sec-context").is_visible()
    assert "还没有模型调用样本" in page.locator("#context-cache").inner_text()
    page.locator("#cfg-context-history").fill("6")
    page.locator("label:has(#cfg-context-enabled)").click()
    page.locator("#context-save").click()
    page.locator("#context-save-msg").get_by_text("已保存", exact=False).wait_for()
    assert "重启" in page.locator("#context-save-msg").inner_text()
    page.locator("details:has(#context-running-settings) > summary").click()
    assert "运行 40" in page.locator("#context-running-settings").inner_text()
    assert client.get("/api/context/settings").json()["saved"]["limits"]["max_history_messages"] == 6
    assert runtime.config.limits.max_history_messages == 40
    assert not errors


def test_context_settings_only_one_concurrent_save_wins(web):
    from concurrent.futures import ThreadPoolExecutor

    client, runtime = web
    revision = client.get("/api/context/settings").json()["revision"]

    def save(value):
        return client.put(
            "/api/context/settings", json={"revision": revision, "updates": {"limits": {"max_history_messages": value}}}
        ).status_code

    with ThreadPoolExecutor(max_workers=2) as pool:
        statuses = list(pool.map(save, [5, 8]))
    assert sorted(statuses) == [200, 409]
    assert client.get("/api/context/settings").json()["saved"]["limits"]["max_history_messages"] in [5, 8]


def test_settings_context_distinguishes_reported_zero_from_missing_cache_usage(ui):
    page, client, runtime, errors = ui
    sid = runtime.agent.session_id
    runtime.runs.create(run_id="cache-ui", session_id=sid, channel="web", principal="owner", owner="fixture")
    runtime.runs.append_event("cache-ui", "model_end", {"prompt_tokens": 100})
    open_context(page)
    assert "未知" in page.locator("#context-cache").inner_text()
    runtime.runs.append_event("cache-ui", "model_end", {"prompt_tokens": 100, "cached_prompt_tokens": 0})
    page.locator("#context-refresh").click()
    page.locator("#context-cache").get_by_text("0.0%", exact=True).wait_for()
    assert not errors


def test_settings_context_late_stats_do_not_replace_new_session(ui):
    page, client, runtime, errors = ui
    first = runtime.agent.session_id
    second = runtime.store.new_session("另一个上下文")
    page.add_init_script(delay_script("String(url).startsWith('/api/context?')"))
    page.goto("http://127.0.0.1/")
    # Delay the explicitly selected session's request, after initial state has
    # populated S.currentSession. An early click legitimately uses /api/context
    # without a query and would bypass this test's delayed-response injection.
    page.locator(f'#sessions li.active[data-id="{first}"]').wait_for()
    page.locator('[data-action="hub:open"][data-section="sec-context"]').click()
    page.wait_for_function("window.__pending.length===1")
    page.evaluate(
        """async sid=>{const version=new URL(document.querySelector('script[src*="/ui/app/main.js"]').src).searchParams.get('v');
      const {S}=await import('/ui/app/state.js?v='+version);S.currentSession=sid;S.selectionVersion++}""",
        second,
    )
    page.locator('.hub-nav a[data-go="sec-persona"]').click()
    page.locator('.hub-nav a[data-go="sec-context"]').click()
    page.wait_for_function("window.__pending.length===2")
    page.evaluate(
        "(data)=>window.__pending[1].resolve(data)", client.get("/api/context", params={"session_id": second}).json()
    )
    page.locator("#context-session").get_by_text("会话 #" + str(second), exact=False).wait_for()
    page.evaluate(
        "(data)=>window.__pending[0].resolve(data)", client.get("/api/context", params={"session_id": first}).json()
    )
    assert "会话 #" + str(second) in page.locator("#context-session").inner_text()
    assert not errors


def test_settings_context_rejects_stale_save_and_preserves_draft(ui):
    page, client, runtime, errors = ui
    open_context(page)
    page.locator("#cfg-context-history").fill("9")
    assert client.post("/api/system/config", json={"updates": {"persona": {"name": "其它设置页面"}}}).status_code == 200
    page.locator("#context-save").click()
    page.locator("#context-save-msg").get_by_text("重新读取", exact=False).wait_for()
    assert page.locator("#cfg-context-history").input_value() == "9"
    page.locator("#context-reload-settings").click()
    page.locator("#ui-dialog").get_by_role("button", name="重新读取", exact=True).click()
    page.wait_for_function('document.querySelector("#cfg-context-history").value==="40"')
    assert not errors


def test_settings_context_loading_failure_has_retry(ui):
    page, _, _, errors = ui
    page.add_init_script("""const originalFetch=window.fetch;let failed=false;window.fetch=(url,opts)=>{
      if(String(url)==='/api/context/settings'&&!failed){failed=true;return Promise.resolve(new Response(JSON.stringify({detail:'临时配置故障'}),{status:503,headers:{'Content-Type':'application/json'}}))}
      return originalFetch(url,opts)};""")
    page.goto("http://127.0.0.1/")
    page.locator('[data-action="hub:open"][data-section="sec-context"]').click()
    page.locator("#context-save-msg").get_by_text("读取失败", exact=False).wait_for()
    assert page.locator("#context-save").is_disabled()
    page.locator("#context-reload-settings").click()
    page.wait_for_function('!document.querySelector("#context-save").disabled')
    assert not errors


@pytest.mark.parametrize(
    "status,detail,expected",
    [
        (404, "Not Found", "重启工作台服务"),
        (404, "会话不存在", "会话不存在"),
        (503, "临时统计故障", "临时统计故障"),
    ],
)
def test_context_stats_failure_clears_loaders_and_retry_recovers(ui, status, detail, expected):
    page, _, _, errors = ui
    page.add_init_script(
        """const originalFetch=window.fetch;let failed=false;window.fetch=(url,opts)=>{
      if(String(url).split('?')[0]==='/api/context'&&!failed){failed=true;return Promise.resolve(new Response(JSON.stringify({detail:%s}),{status:%s,headers:{'Content-Type':'application/json'}}))}
      return originalFetch(url,opts)};"""
        % (json.dumps(detail), status)
    )
    page.goto("http://127.0.0.1/")
    page.locator('[data-action="hub:open"][data-section="sec-context"]').click()
    page.locator("#context-stats-status").get_by_text("统计读取失败", exact=False).wait_for()
    assert expected in page.locator("#context-stats-status").inner_text()
    for selector in ["#context-session", "#context-metrics", "#context-cache"]:
        assert "正在读取" not in page.locator(selector).inner_text()
    assert "暂不可用" in page.locator("#context-cache").inner_text()
    assert page.locator("#context-refresh").is_enabled()
    page.get_by_role("button", name="重试统计", exact=True).click()
    page.locator("#context-metrics .metric-card").first.wait_for()
    assert "已更新" in page.locator("#context-stats-status").inner_text()
    assert "还没有模型调用样本" in page.locator("#context-cache").inner_text()
    assert not errors


@pytest.mark.parametrize("width", [390, 760, 1024, 1440])
def test_settings_context_mobile_layout_and_unique_controls(ui, width):
    page, _, _, errors = ui
    page.set_viewport_size({"width": width, "height": 900})
    # 窄屏中侧栏不可见，使用设置中心主入口。
    page.goto("http://127.0.0.1/")
    page.locator("body.ui-ready").wait_for()
    page.locator('.ui-settings:visible,[data-action="hub:open"]:visible').first.click()
    page.locator('.hub-nav a[data-go="sec-context"]').click()
    page.locator("#context-metrics .metric-card").first.wait_for()
    assert page.evaluate("document.documentElement.scrollWidth<=innerWidth")
    assert page.locator("#cfg-context-history").count() == 1
    assert page.locator("#cfg-limits-history").count() == 0
    assert page.evaluate(
        "document.querySelector('#sec-context').getBoundingClientRect().bottom+1 >= document.querySelector('#sec-context .context-related').getBoundingClientRect().bottom"
    )
    assert not errors
