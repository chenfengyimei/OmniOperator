"""Web 工作台接口单测：TestClient + 临时库（不真调 LLM）。"""
import datetime as dt
import json
import threading
import time
from types import SimpleNamespace

from fastapi.testclient import TestClient

from omni.bootstrap import build_runtime
from omni.channels.web import create_app
from omni.memory.cron import Job, to_iso
from omni.safety.gates import DenyApprover, Risk


def _client(tmp_path, skills_root=None):
    runtime = build_runtime(
        approver=DenyApprover(),
        db_path=tmp_path / "m.db",
        skills_root=skills_root,
    )
    return TestClient(create_app(runtime)), runtime


def test_state_shape(tmp_path):
    client, _rt = _client(tmp_path)
    data = client.get("/api/state").json()
    assert data["persona"]
    assert {
        "get_time",
        "remember",
        "recall",
        "search_history",
        "profile_manage",
        "todo",
        "daily_digest",
        "skills_list",
        "skill_view",
        "skill_create",
        "bilibili_video_info",
    } <= {t["name"] for t in data["tools"]}
    assert data["fts_enabled"] is True
    assert "used" in data["profile"]
    assert "bilibili" in data["toolsets"]


def test_sessions_create_select_messages(tmp_path):
    client, runtime = _client(tmp_path)
    created = client.post("/api/sessions").json()["session_id"]
    assert client.post(f"/api/sessions/{created}/select").status_code == 200
    assert client.post("/api/sessions/999/select").status_code == 404
    # 通过 store 直接塞一条消息，接口能读回
    runtime.store.append_message(created, "user", "工作台测试消息")
    msgs = client.get(f"/api/sessions/{created}/messages").json()["messages"]
    assert msgs and msgs[0]["content"] == "工作台测试消息"
    assert msgs[0]["role"] == "user"
    assert client.get("/api/sessions/999/messages").status_code == 404
    # 会话列表里能看到
    sessions = client.get("/api/sessions").json()["sessions"]
    assert any(s["id"] == created for s in sessions)


def test_todos_endpoints(tmp_path):
    client, runtime = _client(tmp_path)
    sid = client.post("/api/sessions").json()["session_id"]
    assert client.get(f"/api/sessions/{sid}/todos").json()["todos"] == []
    assert client.get("/api/sessions/999/todos").status_code == 404
    runtime.store.todo_add(sid, "写日报")
    todos = client.get(f"/api/sessions/{sid}/todos").json()["todos"]
    assert todos and todos[0]["content"] == "写日报" and todos[0]["done"] is False
    tid = todos[0]["id"]
    ok = client.post(
        "/api/todos/toggle", json={"session_id": sid, "todo_id": tid, "done": True}
    )
    assert ok.status_code == 200
    assert client.get(f"/api/sessions/{sid}/todos").json()["todos"][0]["done"] is True
    assert (
        client.post(
            "/api/todos/toggle", json={"session_id": sid, "todo_id": 999, "done": True}
        ).status_code
        == 404
    )


def test_memory_endpoints(tmp_path):
    client, runtime = _client(tmp_path)
    runtime.store.save_fact("用户喜欢喝拿铁")
    runtime.store.profile_add("用户是游戏开发者")
    sid = runtime.store.new_session()
    runtime.store.save_review_log(sid, "无需变更")

    facts = client.get("/api/facts").json()["facts"]
    assert any("拿铁" in f for f in facts)
    searched = client.get("/api/facts", params={"q": "拿铁"}).json()["facts"]
    assert searched
    profile = client.get("/api/profile").json()
    assert profile["lines"] and profile["usage"] > 0
    reviews = client.get("/api/reviews").json()["reviews"]
    assert reviews and reviews[0]["outcome"] == "无需变更"
    assert reviews[0]["session_id"] == sid


def test_skills_endpoints(tmp_path):
    client, runtime = _client(tmp_path, skills_root=tmp_path / "skills")
    # 空技能库
    assert client.get("/api/skills").json()["skills"] == []
    assert client.get("/api/skills/nope").status_code == 404
    # 建一个（走 SkillStore，与 agent 工具同一条路径）
    ok, _msg = runtime.skills.create(
        "demo-skill",
        "演示技能",
        "# 演示\n\n## When to Use\n演示。\n\n## Procedure\n按流程走。\n\n"
        "## Pitfalls\n无。\n\n## Verification\nOK。",
    )
    assert ok
    data = client.get("/api/skills").json()["skills"]
    assert data and data[0]["name"] == "demo-skill"
    view = client.get("/api/skills/demo-skill").json()["content"]
    assert "When to Use" in view


def test_index_served(tmp_path):
    client, _rt = _client(tmp_path)
    resp = client.get("/")
    assert resp.status_code == 200
    assert "OmniOperator" in resp.text


def test_chat_with_bad_session_404(tmp_path):
    client, _rt = _client(tmp_path)
    resp = client.post("/api/chat", json={"message": "hi", "session_id": 999})
    assert resp.status_code == 404


def test_approvals_endpoints(tmp_path):
    client, runtime = _client(tmp_path)
    # 空待审 + 预算信息
    data = client.get("/api/approvals").json()
    assert data["pending"] == []
    assert "browser_writes" in data and data["browser_writes"]["max"] > 0
    # 决议不存在的 id
    assert client.post(
        "/api/approvals/nope/resolve", json={"allowed": True}
    ).json()["ok"] is False
    # 发起一个待审（走 broker），端点可见 → 决议 → 线程放行
    from types import SimpleNamespace

    from omni.safety.gates import Risk

    tool = SimpleNamespace(
        name="browser_click", description="点击", risk=Risk.CONFIRM
    )
    result = {}

    def request():
        result["decision"] = runtime.approvals.request(tool, timeout=5)

    threading.Thread(target=request, daemon=True).start()
    time.sleep(0.2)
    pending = client.get("/api/approvals").json()["pending"]
    assert len(pending) == 1 and pending[0]["tool_name"] == "browser_click"
    assert client.post(
        f"/api/approvals/{pending[0]['id']}/resolve", json={"allowed": True}
    ).json()["ok"] is True
    time.sleep(0.2)
    assert result["decision"].allowed is True


def test_config_endpoints_extended_shape(tmp_path):
    client, _rt = _client(tmp_path)
    cfg = client.get("/api/system/config").json()
    # 设置中心需要的全部分组
    for group in ("persona", "models", "limits", "review", "asr", "qq", "browser"):
        assert group in cfg
    assert cfg["persona"]["name"]
    assert "system_prompt" in cfg["persona"]
    assert {"chat", "vision", "review"} <= set(cfg["models"])


def test_bili_cookies_endpoints(tmp_path, monkeypatch):
    import omni.channels.web as web_mod

    fake = tmp_path / "bili_cookies.json"
    monkeypatch.setattr(web_mod, "BILI_COOKIES_PATH", fake)
    client, _rt = _client(tmp_path)
    # 未配置
    status = client.get("/api/system/bili-cookies").json()
    assert status["configured"] is False
    # 保存
    resp = client.post(
        "/api/system/bili-cookies",
        json={"sessdata": "s1", "bili_jct": "j1", "buvid3": "b1"},
    )
    assert resp.json()["ok"] is True
    data = json.loads(fake.read_text(encoding="utf-8"))
    assert data == {"SESSDATA": "s1", "bili_jct": "j1", "buvid3": "b1"}
    status2 = client.get("/api/system/bili-cookies").json()
    assert status2["configured"] is True and status2["keys"]["SESSDATA"] is True


def test_cron_endpoints(tmp_path):
    client, runtime = _client(tmp_path)
    assert client.get("/api/system/cron").json()["jobs"] == []
    # 造一个任务（模拟 agent cron_add 的产物）
    job = Job(name="demo-job", prompt="报个到", every_minutes=30)
    job.next_run_at = to_iso(dt.datetime.now().astimezone())
    runtime.cron_jobs.upsert(job, validate=False)
    cron = client.get("/api/system/cron").json()["jobs"]
    assert cron and cron[0]["name"] == "demo-job"
    assert cron[0]["schedule"] == "每 30 分钟"
    # 启停
    resp = client.post(
        "/api/system/cron/toggle", json={"name": "demo-job", "enabled": False}
    )
    assert resp.json()["ok"] is True
    assert runtime.cron_jobs.get("demo-job").enabled is False
    resp = client.post(
        "/api/system/cron/toggle", json={"name": "demo-job", "enabled": True}
    )
    assert resp.json()["ok"] is True
    # 删除
    assert client.post("/api/system/cron/delete", json={"name": "demo-job"}).json()["ok"] is True
    assert runtime.cron_jobs.get("demo-job") is None
    # 不存在的任务：toggle 404 / delete ok:False
    assert (
        client.post("/api/system/cron/toggle", json={"name": "nope", "enabled": True}).status_code
        == 404
    )
    assert client.post("/api/system/cron/delete", json={"name": "nope"}).json()["ok"] is False


def test_model_test_endpoint_rejects_bad_model(tmp_path):
    client, _rt = _client(tmp_path)
    resp = client.post("/api/system/model-test", json={"model": "nonexistent/model-xyz"})
    assert resp.status_code == 400
