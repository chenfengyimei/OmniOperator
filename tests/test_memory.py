"""MemoryStore 单测：会话 / 消息 / 事实 / FTS 全文检索 / 用户档案 / review 日志。"""
from omni.memory.store import PROFILE_MAX_CHARS, MemoryStore


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


def test_facts_phrase_query_fallback(tmp_path):
    """整句式查询（无空格、无精确词命中）要靠中文 2-gram 兜底命中。"""
    store = MemoryStore(tmp_path / "m.db")
    store.save_fact("用户喜欢喝拿铁。")
    assert any("拿铁" in f for f in store.search_facts("我喜欢喝什么"))
    store.save_fact("用户养了一只猫，名字叫煤球。")
    assert any("煤球" in f for f in store.search_facts("我的猫叫什么名字"))
    store.close()


def test_fts_enabled_and_facts_full_text(tmp_path):
    """本机 SQLite 应带 FTS5；3 字以上查询走 FTS 路径也能命中。"""
    store = MemoryStore(tmp_path / "m.db")
    assert store.fts_enabled, "本机 Python 的 SQLite 应包含 FTS5"
    store.save_fact("项目发布会安排在周五下午三点")
    assert any("周五" in f for f in store.search_facts("发布会 周五"))
    store.close()


def test_search_messages_across_sessions(tmp_path):
    store = MemoryStore(tmp_path / "m.db")
    sid = store.new_session()
    store.append_message(sid, "user", "我们项目的里程碑定在下周三")
    store.append_message(sid, "assistant", "收到，记下了。", auto_title=False)
    other = store.new_session()
    store.append_message(other, "user", "今天天气不错")
    hits = store.search_messages("里程碑")
    assert hits and hits[0][2] == "我们项目的里程碑定在下周三"
    assert all(isinstance(h[0], int) for h in hits)
    assert store.search_messages("完全不相关的东西xyzzy") == []
    store.close()


def test_profile_crud_and_limit(tmp_path):
    store = MemoryStore(tmp_path / "m.db")
    ok, _msg = store.profile_add("用户是游戏开发者")
    assert ok
    ok2, _msg2 = store.profile_add("偏好简洁的沟通风格")
    assert ok2
    lines = store.profile_lines()
    assert len(lines) == 2
    assert "游戏开发者" in store.profile_text() if hasattr(store, "profile_text") else True
    assert "游戏开发者" in store.profile_block()
    # 编辑与删除
    assert store.profile_edit(lines[0][0], "用户是资深游戏开发者")
    assert not store.profile_edit(999, "不存在的行")
    assert store.profile_delete(lines[1][0])
    assert len(store.profile_lines()) == 1
    # 超限：返回错误 + 当前档案 + 剩余额度（逼合并协议）
    ok_full, msg_full = store.profile_add("x" * PROFILE_MAX_CHARS)
    assert not ok_full
    assert "资深游戏开发者" in msg_full and "剩余" in msg_full
    store.close()


def test_profile_empty_block(tmp_path):
    store = MemoryStore(tmp_path / "m.db")
    assert store.profile_block() == ""
    assert store.profile_lines() == []
    store.close()


def test_review_log_roundtrip(tmp_path):
    store = MemoryStore(tmp_path / "m.db")
    sid = store.new_session()
    store.save_review_log(sid, "记住了用户职业")
    store.save_review_log(sid, "无需变更")
    outcomes = store.list_reviews(sid)
    assert outcomes == ["无需变更", "记住了用户职业"]  # 新的在前
    store.close()
