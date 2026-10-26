"""MemoryStore 单测：会话 / 消息 / 事实的增查与自动标题。"""
from omni.memory.store import MemoryStore


def test_message_roundtrip_and_auto_title(tmp_path):
    store = MemoryStore(tmp_path / "m.db")
    sid = store.new_session()
    store.append_message(sid, "user", "帮我看看这个B站视频")
    store.append_message(sid, "assistant", "好的。", auto_title=False)
    msgs = store.recent_messages(sid, 10)
    assert msgs == [("user", "帮我看看这个B站视频"), ("assistant", "好的。")]
    # 首条用户消息自动成为会话标题
    assert store.list_sessions()[0][1] == "帮我看看这个B站视频"
    store.close()


def test_session_navigation(tmp_path):
    store = MemoryStore(tmp_path / "m.db")
    first = store.new_session()
    second = store.new_session()
    assert store.latest_session_id() == second
    assert store.session_exists(first)
    assert not store.session_exists(99999)
    store.close()


def test_facts_search(tmp_path):
    store = MemoryStore(tmp_path / "m.db")
    store.save_fact("用户喜欢喝拿铁")
    store.save_fact("用户养了一只猫叫煤球")
    assert any("拿铁" in f for f in store.search_facts("拿铁"))
    assert any("煤球" in f for f in store.search_facts("用户 猫"))
    assert store.search_facts("完全不存在的东西xyz") == []
    # 空查询返回最近记忆
    assert len(store.search_facts("")) == 2
    store.close()
