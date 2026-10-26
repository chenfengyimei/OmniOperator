"""Web 工作台接口单测：TestClient + 临时库（不真调 LLM）。"""
from fastapi.testclient import TestClient

from omni.bootstrap import build_runtime
from omni.channels.web import create_app
from omni.safety.gates import DenyApprover


def _client(tmp_path):
    runtime = build_runtime(approver=DenyApprover(), db_path=tmp_path / "m.db")
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
    # 会话列表里能看到，且带 active 标记
    sessions = client.get("/api/sessions").json()["sessions"]
    assert any(s["id"] == created for s in sessions)


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


def test_index_served(tmp_path):
    client, _rt = _client(tmp_path)
    resp = client.get("/")
    assert resp.status_code == 200
    assert "OmniOperator" in resp.text


def test_chat_with_bad_session_404(tmp_path):
    client, _rt = _client(tmp_path)
    resp = client.post(
        "/api/chat", json={"message": "hi", "session_id": 999}
    )
    assert resp.status_code == 404
