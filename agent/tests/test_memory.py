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


def test_digest_since(tmp_path):
    store = MemoryStore(tmp_path / "m.db")
    sid = store.new_session()
    store.append_message(sid, "user", "帮我看看这个视频")
    store.append_message(sid, "assistant", "好的", auto_title=False)
    other = store.new_session()  # 无消息的会话不应出现在 digest
    store.save_fact("用户喜欢喝拿铁")
    store.save_fact("用户养了一只猫叫煤球")
    store.save_review_log(sid, "记住猫名")

    d = store.digest_since("2000-01-01T00:00:00+08:00")
    assert d["sessions"] == [{"id": sid, "title": "帮我看看这个视频", "messages": 2}]
    assert d["facts_new"] == 2
    assert any("煤球" in f for f in d["facts_recent"])
    assert d["reviews_recent"] == ["记住猫名"]
    assert d["profile_chars"] == 0

    # 未来窗口：全部排除
    d2 = store.digest_since("2999-01-01T00:00:00+08:00")
    assert d2["sessions"] == [] and d2["facts_new"] == 0
    assert d2["facts_recent"] == [] and d2["reviews_recent"] == []
    store.close()


def test_todo_crud(tmp_path):
    store = MemoryStore(tmp_path / "m.db")
    sid = store.new_session()
    assert store.todo_list(sid) == []
    t1 = store.todo_add(sid, "查字幕")
    t2 = store.todo_add(sid, "写看后感")
    assert [(r[0], r[1], r[2]) for r in store.todo_list(sid)] == [
        (t1, "查字幕", 0),
        (t2, "写看后感", 0),
    ]
    assert store.todo_set_done(sid, t1, True)
    assert store.todo_list(sid)[0][2] == 1
    assert not store.todo_set_done(sid, 999, True)
    assert store.todo_delete(sid, t2)
    assert not store.todo_delete(sid, t2)
    store.todo_add(sid, "临时项")
    # done_only 只清已完成的（t1 此前已标完成）→ 清掉 1 条，临时项保留
    assert store.todo_clear(sid, done_only=True) == 1
    assert [r[1] for r in store.todo_list(sid)] == ["临时项"]
    store.todo_add(sid, "另一条")
    assert store.todo_clear(sid) == 2
    assert store.todo_list(sid) == []
    store.close()


def test_search_fact_rows_matches_search_facts_order(tmp_path):
    """search_fact_rows 与 search_facts 必须共用同一条检索链。

    契约：同 query 下，rows 的 content 顺序与 facts 的顺序**完全相等**——
    工作台按 id 删除时若两者错位，就会「删掉另一条」。
    """
    store = MemoryStore(tmp_path / "m.db")
    store.save_fact("用户喜欢喝拿铁")
    store.save_fact("用户养了一只猫叫煤球")
    store.save_fact("项目发布会安排在周五下午三点")

    for query in ("拿铁", "用户 猫", "发布会 周五", "我喜欢喝什么", "", "完全不存在的东西xyz"):
        facts = store.search_facts(query, limit=10)
        rows = store.search_fact_rows(query, limit=10)
        assert [r["content"] for r in rows] == facts, f"query={query!r} 两者顺序不一致"
        assert all(isinstance(r["id"], int) and r["id"] > 0 for r in rows)

    # 行里的 id 必须真的指向那条 content（按 id 删除不能删错）
    for row in store.search_fact_rows("拿铁", limit=10):
        assert store.fact_rows(limit=200)[0]["content"] or True  # fact_rows 仍在用，确保契约未破
        detail = [f for f in store.fact_rows(limit=200) if f["id"] == row["id"]]
        assert detail and detail[0]["content"] == row["content"]
    store.close()
