"""调用链可视化端到端：历史会话能看到当时完整的"问—想—做—答"中间过程。

种子数据按真实时序回放（run 先建、消息后落），浏览器里切到该会话后，
助手消息上方应挂出调用链卡片，头行 meta 应带步数/耗时/终态。
"""

from __future__ import annotations

from test_ui_flows import ui


def test_history_message_shows_its_tool_chain(ui):
    page, _client, runtime, errors = ui
    sid = runtime.store.new_session("带工具链的会话")

    rid = "run-chain-e2e"
    runtime.runs.create(run_id=rid, session_id=sid, channel="web", principal="test", owner="test")
    runtime.runs.append_event(rid, "run_started")
    runtime.runs.append_event(rid, "model_start", step=1)
    runtime.runs.append_event(
        rid, "model_end", step=1, text_chars=18, tool_calls=["disk_check"], prompt_tokens=10, completion_tokens=5
    )
    runtime.runs.append_event(
        rid, "tool", name="disk_check", status="ok", args='{"path":"C:"}', excerpt="剩余 11.6 GB", elapsed_ms=321.5
    )
    runtime.runs.append_event(rid, "answer")
    runtime.runs.touch(rid, status="done")

    # 真实时序：回合先开始，回答后落库
    runtime.store.append_message(sid, "user", "看看磁盘")
    runtime.store.append_message(sid, "assistant", "C 盘还剩 **11.6 GB**。")

    page.goto("http://127.0.0.1/")
    page.locator("#sessions li").filter(has_text="带工具链的会话").click()
    page.wait_for_selector(".msg.assistant .scaffolds", timeout=10000)
    chain = page.locator(".msg.assistant .scaffolds")
    # 历史链默认折叠：只显示开关行，工具行不可见
    assert "调用过程" in chain.inner_text()
    assert not chain.locator(".scaf").filter(has_text="disk_check").is_visible()
    # 点开关展开，链内容可见
    chain.locator(".scaf-toggle").click()
    chain_text = chain.inner_text()
    assert "disk_check" in chain_text
    assert '{"path":"C:"}' in chain_text or "path" in chain_text

    meta = page.locator(".msg.assistant .a-meta").first.inner_text()
    assert "1 步" in meta
    # 工具行可展开看结果摘录
    page.locator(".scaf", has_text="disk_check").click()
    out = page.locator(".scaf.open + .scaf-out").inner_text()
    assert "剩余 11.6 GB" in out

    # 收起整条链：展开过的详情块必须跟着消失（旧 bug：隐藏规则与 .scaf.open+.scaf-out 同特异性且
    # 后者在后，链收起后详情块继续露在开关行下面）
    chain.locator(".scaf-toggle").click()
    assert "open" not in (chain.get_attribute("class") or "")
    assert not page.locator(".scaf.open + .scaf-out").first.is_visible()
    assert not chain.locator(".scaf", has_text="disk_check").is_visible()

    # 再次展开：不继承上一次的内部展开态，工具行回到折叠状态
    chain.locator(".scaf-toggle").click()
    tool_row = chain.locator(".scaf", has_text="disk_check")
    assert tool_row.is_visible()
    assert "open" not in (tool_row.get_attribute("class") or "")
    assert not page.locator(".scaf.open + .scaf-out").first.is_visible()
    assert not errors
