"""发布闭环：假站点 + 真实 Chromium 拦截所有请求，绝不向真实 B站发布。"""

from __future__ import annotations

import json
import subprocess
import sys
from types import SimpleNamespace

import pytest

from omni.config import BilibiliWrite, UIFormSelectors
from omni.memory.publications import PublicationError, PublicationStore, content_hash
from omni.memory.state import StateStore
from omni.tools.bilibili.publishing import BilibiliPublisher, canonical_target
from omni.tools.bilibili.write_tools import register_bilibili_write_tools
from omni.tools.browser.budget import WriteBudget
from omni.tools.browser.driver import BrowserDriver
from omni.tools.browser.forms import BrowserForms
from omni.tools.registry import ToolRegistry, SessionRef
from omni.tools.runctx import RunRef
from omni.safety.gates import AllowAllApprover, DenyApprover, ConsoleApprover

URL = "https://www.bilibili.com/video/BV1xx411c7mD"
PROFILE = UIFormSelectors(
    account="#account",
    target="#target",
    editor="#editor",
    submit="#send",
    records=".record",
    record_text=".body",
    record_author=".author",
    challenge="#captcha",
    logged_out="#login",
)


class FakeForms:
    def __init__(self):
        self.calls = []
        self.state = "sent"

    def observe(self, kind):
        return {"url": URL, "account": "42", "target": "BV1xx411c7mD", "fingerprint": "fixed", "editor_value": ""}

    def submit(self, kind, expected, content, **kwargs):
        assert kwargs["authorized"]()
        self.calls.append(content)
        return {
            "state": self.state,
            "evidence": {"record_id": "100", "account": "42", "url": URL, "content_hash": content_hash(content)},
        }


@pytest.fixture
def env(tmp_path):
    state = StateStore(tmp_path / "state.db")
    drafts = PublicationStore(state)
    forms = FakeForms()
    config = BilibiliWrite(enabled=True, forms={"comment": PROFILE})
    publisher = BilibiliPublisher(config, drafts, forms, WriteBudget(state))
    yield SimpleNamespace(state=state, drafts=drafts, forms=forms, config=config, publisher=publisher)
    state.close()


def prepare(env, text="测试内容"):
    return env.publisher.prepare(session_id=1, kind="comment", target=URL, account="42", content=text)


def args(row):
    return dict(
        session_id=row["session_id"],
        draft_id=row["id"],
        kind=row["kind"],
        target=row["target"],
        account=row["account"],
        content=row["content"],
        expected_hash=row["content_hash"],
    )


def test_draft_only_then_single_approved_submission(env):
    row = prepare(env)
    assert env.forms.calls == []
    assert env.publisher.submit(**args(row)).side_effect_state == "applied"
    assert env.publisher.submit(**args(row)).status == "error"
    duplicate = prepare(env)
    assert env.publisher.submit(**args(duplicate)).status == "error"
    assert len(env.forms.calls) == 1 and env.drafts.get(row["id"])["status"] == "sent"


@pytest.mark.parametrize("changed", ["content", "account", "target", "expected_hash", "session_id"])
def test_approval_preview_or_scope_change_never_submits(env, changed):
    row = prepare(env)
    payload = args(row)
    payload[changed] = 2 if changed == "session_id" else "changed"
    assert env.publisher.submit(**payload).status == "error"
    assert env.forms.calls == []


def test_edit_after_approval_and_expiration_reject_before_ui(env):
    row = prepare(env)
    env.drafts.edit(row["id"], row["content_hash"], "改后的内容")
    assert env.publisher.submit(**args(row)).status == "error"
    edited = env.drafts.get(row["id"])
    env.drafts.clock = lambda: edited["created_at"] + 601
    assert env.publisher.submit(**args(edited)).status == "error"
    assert env.forms.calls == []


def test_unknown_needs_human_resolution_and_new_approval(env):
    row = prepare(env)
    env.forms.state = "unknown"
    assert env.publisher.submit(**args(row)).status == "unknown"
    duplicate = prepare(env)
    assert env.publisher.submit(**args(duplicate)).status == "error"
    assert not env.drafts.resolve(row["id"], "old-hash", "not_sent")
    assert env.drafts.resolve(row["id"], row["content_hash"], "not_sent")
    assert env.publisher.submit(**args(row)).status == "error"
    env.forms.state = "sent"
    assert env.publisher.submit(**args(duplicate)).status == "ok"


def test_budget_and_cancel_stop_before_ui(env):
    row = prepare(env)
    assert env.publisher.submit(**args(row), cancel=SimpleNamespace(cancelled=True)).status == "cancelled"
    env.publisher.budget = WriteBudget(env.state, max_per_day=1)
    assert env.publisher.submit(**args(row)).status == "denied"
    assert env.forms.calls == []


def test_registry_unattended_and_smart_cannot_approve_publishing(env):
    from omni.safety.smart import SmartApprover

    registry, session, run = ToolRegistry(), SessionRef(), RunRef()
    register_bilibili_write_tools(registry, env.publisher, session, run)
    row = prepare(env)
    payload = {key: row[key] for key in ("kind", "target", "account", "content", "content_hash")}
    payload["draft_id"] = row["id"]
    model_calls = []
    smart = SmartApprover(base=DenyApprover(), ask_model=lambda *_: model_calls.append(1) or "ALLOW")
    with session.bind(1):
        for approver in (DenyApprover(), smart):
            assert registry.execute("bilibili_submit_draft", payload, approver=approver).status == "denied"
    assert env.forms.calls == model_calls == []
    shown = []
    console = ConsoleApprover(SimpleNamespace(input=lambda prompt: shown.append(prompt) or "n"))
    payload["content"] = "x" * 600 + "完整末尾"
    console.check(registry.get("bilibili_submit_draft"), payload)
    assert "完整末尾" in shown[0]


def test_mentions_are_replaced_with_names_and_listed(env):
    """艾特：@{uid} 占位换成 @昵称，并把艾特清单留给人工确认时看。"""
    env.config.forms["comment"] = PROFILE.model_copy(
        update={"mention_options": ".mention-option", "mention_nodes": ".mention"}
    )
    row = env.publisher.prepare(
        session_id=1,
        kind="comment",
        target=URL,
        account="42",
        content="@{5566} 你说得对",
        mentions=[{"uid": "5566", "name": "老王"}],
    )
    assert row["content"] == "@老王 你说得对"
    assert row["mentions"] == [{"uid": "5566", "name": "老王"}]
    assert row["mention_mode"] == "component"


def test_mentions_without_real_component_are_explicitly_rejected(env):
    with pytest.raises(PublicationError, match="真实艾特组件"):
        env.publisher.prepare(
            session_id=1,
            kind="comment",
            target=URL,
            account="42",
            content="@{5566} 你好",
            mentions=[{"uid": "5566", "name": "老王"}],
        )
    assert not env.forms.calls


def test_mention_without_name_is_rejected(env):
    with pytest.raises(PublicationError):
        env.publisher.prepare(
            session_id=1,
            kind="comment",
            target=URL,
            account="42",
            content="@{5566} 嘿",
            mentions=[{"uid": "5566", "name": ""}],
        )


def test_mention_placeholder_without_list_is_rejected(env):
    """正文里有 @{uid} 却没给昵称：宁可不发，也不发一个裸 UID。"""
    with pytest.raises(PublicationError):
        env.publisher.prepare(session_id=1, kind="comment", target=URL, account="42", content="@{5566} 嘿")


def test_mention_expanding_body_is_still_capped(env):
    with pytest.raises(PublicationError):
        env.publisher.prepare(
            session_id=1,
            kind="comment",
            target=URL,
            account="42",
            content="@{5566} " + "字" * 1000,
            mentions=[{"uid": "5566", "name": "老王" * 40}],
        )


def test_prepare_tool_lists_mentions_for_human_review(env):
    env.config.forms["comment"] = PROFILE.model_copy(
        update={"mention_options": ".mention-option", "mention_nodes": ".mention"}
    )
    registry, session, run = ToolRegistry(), SessionRef(), RunRef()
    register_bilibili_write_tools(registry, env.publisher, session, run)
    with session.bind(1):
        result = registry.execute(
            "bilibili_prepare_write",
            {
                "kind": "comment",
                "target": URL,
                "account": "42",
                "content": "@{5566} 在吗",
                "mentions": [{"uid": "5566", "name": "老王"}],
            },
        )
    assert result.status == "ok"
    assert "老王(5566)" in result.text
    assert "@老王 在吗" in result.text


def test_reply_comment_target_keeps_reply_id():
    url, key = canonical_target("comment", URL + "?reply_id=88001")
    assert url == URL + "?reply_id=88001"
    assert key == "BV1xx411c7mD"


def test_unknown_query_parameter_is_rejected():
    with pytest.raises(PublicationError):
        canonical_target("comment", URL + "?spm=from搜索")


def test_cross_process_only_one_draft_claim(env, tmp_path):
    row = prepare(env)
    code = """
import sys
from omni.memory.state import StateStore
from omni.memory.publications import PublicationStore, PublicationError
s=StateStore(sys.argv[1])
try:
 PublicationStore(s).claim(sys.argv[2],session_id=1,expected_hash=sys.argv[3],ttl_s=600)
 print(1)
except PublicationError:
 print(0)
finally:
 s.close()
"""
    children = [
        subprocess.Popen(
            [sys.executable, "-c", code, str(env.state.path), row["id"], row["content_hash"]],
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
        )
        for _ in range(4)
    ]
    claims = []
    for child in children:
        out, err = child.communicate(timeout=30)
        assert child.returncode == 0, err
        claims.append(int(out.strip()))
    assert sum(claims) == 1


HTML = """<!doctype html><meta charset="utf-8"><title>离线发布表单</title>
<a id="account" data-mid="42">当前账号42</a><span id="target" data-id="BV1xx411c7mD">测试视频</span>
<div id="captcha" style="display:none">验证码</div><div id="login" style="display:none">登录已失效</div>
<textarea id="editor"></textarea><button id="send">发布</button><section id="records"></section>
<script>window.sent=0;window.mode='normal';document.querySelector('#send').onclick=()=>{
 window.sent++;const editor=document.querySelector('#editor');
 if(window.mode!=='lost') {const row=document.createElement('div');row.className='record';row.dataset.id=String(window.sent);
 const author=document.createElement('span');author.className='author';author.dataset.mid='42';author.textContent='账号42';
 const body=document.createElement('div');body.className='body';body.textContent=editor.value;
 row.append(author,body);document.querySelector('#records').prepend(row);}
 editor.value='';};</script>"""


@pytest.fixture
def browser_env(tmp_path):
    driver = BrowserDriver(
        headless=True, slow_mo_ms=0, profile_dir=tmp_path / "profile", shots_dir=tmp_path / "shots", action_timeout_s=20
    )
    # 所有请求都由测试直接回填，真实 Chromium 不访问 B站或第三方。
    driver._run(
        lambda: driver._ensure_page().route(
            "**/*", lambda route: route.fulfill(status=200, body=HTML, content_type="text/html")
        )
    )
    driver.navigate(URL)
    forms = BrowserForms(driver, {"comment": PROFILE, "private_message": PROFILE})
    yield driver, forms
    assert "已关闭" in driver.close(wait_s=5)


@pytest.mark.parametrize("kind", ["comment", "private_message"])
def test_real_browser_ui_edit_click_and_verified_record(browser_env, kind):
    driver, forms = browser_env
    if kind == "private_message":
        driver.navigate("https://message.bilibili.com/#/whisper/mid123")
        driver._run(lambda: driver._page.locator("#target").evaluate("e=>e.dataset.id='123'"))
    observation = forms.observe(kind)
    assert driver._run(lambda: driver._page.evaluate("window.sent")) == 0
    result = forms.submit(kind, observation, "离线中文测试[不是CQ指令]")
    assert result["state"] == "sent", result
    assert result["evidence"]["account"] == "42" and result["evidence"]["record_id"] == "1"
    # 旧 observation 包含旧输入/发布记录，重复用会被拒绝。
    assert forms.submit(kind, observation, "离线中文测试[不是CQ指令]")["state"] == "blocked"
    assert driver._run(lambda: driver._page.evaluate("window.sent")) == 1


@pytest.mark.parametrize("change", ["account", "target", "captcha", "login", "editor"])
def test_real_browser_changed_context_blocks_submission(browser_env, change):
    driver, forms = browser_env
    observed = forms.observe("comment")
    script = {
        "account": "document.querySelector('#account').dataset.mid='43'",
        "target": "document.querySelector('#target').dataset.id='other'",
        "captcha": "document.querySelector('#captcha').style.display='block'",
        "login": "document.querySelector('#login').style.display='block'",
        "editor": "document.querySelector('#editor').value='用户正在编辑'",
    }[change]
    driver._run(lambda: driver._page.evaluate(script))
    assert forms.submit("comment", observed, "不应该发布")["state"] == "blocked"
    assert driver._run(lambda: driver._page.evaluate("window.sent")) == 0


def test_real_browser_missing_confirmation_is_unknown(browser_env):
    driver, forms = browser_env
    driver._run(lambda: driver._page.evaluate("window.mode='lost'"))
    result = forms.submit("comment", forms.observe("comment"), "仅点击一次")
    assert result["state"] == "unknown"
    assert driver._run(lambda: driver._page.evaluate("window.sent")) == 1


def test_identity_changes_after_capture_before_atomic_click(browser_env, monkeypatch):
    driver, forms = browser_env
    observed = forms.observe("comment")
    original, calls = forms._capture, []

    def changed(kind):
        value = original(kind)
        calls.append(1)
        if len(calls) == 2:
            value[1]["page"].evaluate("document.querySelector('#account').dataset.mid='43'")
        return value

    monkeypatch.setattr(forms, "_capture", changed)
    assert forms.submit("comment", observed, "不能发错账号")["state"] == "unknown"
    assert driver._run(lambda: driver._page.evaluate("window.sent")) == 0


def test_abandoned_queue_action_never_clicks(browser_env, monkeypatch):
    from omni.tools.browser.driver import BrowserUnknown

    driver, forms = browser_env
    observed, queued = forms.observe("comment"), []
    original = driver._run

    def timeout(fn):
        queued.append(fn)
        raise BrowserUnknown("test queue timeout")

    monkeypatch.setattr(driver, "_run", timeout)
    assert forms.submit("comment", observed, "排队超时不迟发")["state"] == "unknown"
    monkeypatch.setattr(driver, "_run", original)
    assert original(queued[0])["state"] == "blocked"
    assert original(lambda: driver._page.evaluate("window.sent")) == 0


@pytest.mark.parametrize(
    "mode,expected",
    [("valid", "sent"), ("wrong-uid", "unknown"), ("duplicate", "unknown"), ("account-drift", "unknown")],
)
def test_real_mention_component_requires_unique_uid_and_final_identity(browser_env, mode, expected):
    driver, forms = browser_env
    profile = PROFILE.model_copy(update={"mention_options": ".mention-option", "mention_nodes": ".mention"})
    forms.profiles["comment"] = profile
    driver._run(
        lambda: driver._page.evaluate(
            """mode => {
      const old=document.querySelector('#editor'), editor=document.createElement('div');
      editor.id='editor'; editor.contentEditable='true'; old.replaceWith(editor);
      for(let i=0;i<(mode==='duplicate'?2:1);i++) {
        const option=document.createElement('button'); option.className='mention-option'; option.dataset.mid=mode==='wrong-uid'?'999':'123'; option.textContent='测试用户';
        option.onclick=()=>{
          editor.textContent=editor.textContent.slice(0,-5);
          const node=document.createElement('span'); node.className='mention'; node.dataset.mid='123'; node.contentEditable='false'; node.textContent='@测试用户'; editor.append(node);
          editor.focus(); const range=document.createRange(); range.selectNodeContents(editor); range.collapse(false); const selection=getSelection();selection.removeAllRanges();selection.addRange(range);
          if(mode==='account-drift')document.querySelector('#account').dataset.mid='43';
        };
        document.body.append(option);
      }
      document.querySelector('#send').onclick=()=>{
        window.sent++;const row=document.createElement('div'); row.className='record';row.dataset.id=String(window.sent);
        const author=document.createElement('span');author.className='author';author.dataset.mid='42';author.textContent='账号42';
        const body=document.createElement('div');body.className='body';body.textContent=editor.textContent;row.append(author,body);document.querySelector('#records').append(row);
      };
    }""",
            mode,
        )
    )
    observation = forms.observe("comment")
    observation["mentions"] = [{"name": "测试用户", "uid": "123"}]
    result = forms.submit("comment", observation, "你好 @测试用户，离线测试")
    assert result["state"] == expected, result
    assert driver._run(lambda: driver._page.evaluate("window.sent")) == (1 if mode == "valid" else 0)
