"""统一整改的可靠性回归；所有运行数据隔离，不调用真实模型。"""

from __future__ import annotations

from types import SimpleNamespace

import pytest

from omni.core.run import RunContext, RunIdentity
from omni.core.vision import attach_images
from omni.memory import state as state_module
from omni.tools.pathpolicy import protected_reason


def test_missing_migration_rejects_before_touching_database(tmp_path, monkeypatch):
    migrations = dict(state_module._MIGRATIONS)
    migrations.pop(max(migrations))
    monkeypatch.setattr(state_module, "_MIGRATIONS", migrations)
    path = tmp_path / "new" / "state.db"
    with pytest.raises(state_module.StateUnavailable, match="未打开或修改"):
        state_module.StateStore(path)
    assert not path.parent.exists()


def test_private_archive_is_not_a_public_workspace():
    assert protected_reason(("temp", "private", "snapshot"), "notes.txt")


def test_image_gate_reports_reason_once_and_compression_can_reanchor(tmp_path):
    from test_artifacts import _make_store, _png, _vision_config

    memory, state, artifacts, sid = _make_store(tmp_path)
    events = []
    run = RunContext(
        RunIdentity.new(session_id=sid), SimpleNamespace(append_event=lambda *args: events.append(args)), max_steps=8
    )
    config = _vision_config(send=False, per_turn=1)
    gateway = SimpleNamespace(resolve=lambda route: "fake/vision")
    artifact = artifacts.save_image(
        _png(), "image/png", session_id=sid, run_id=run.run_id, kind="screenshot", source="test", width=32, height=20
    )
    tool = {"role": "tool", "content": f"[artifact:{artifact.artifact_id}]"}
    messages = [tool]
    try:
        kwargs = dict(config=config, gateway=gateway, model="fake/chat", artifacts=artifacts, run=run)
        attach_images(messages, sid, **kwargs)
        assert "未开启 vision.send_to_cloud" in str(messages[-1])
        count = len(events)
        attach_images(messages, sid, **kwargs)
        assert len(events) == count
        config.vision.send_to_cloud = True
        attach_images(messages, sid, **kwargs)
        assert len(run.attached_image_ids) == 1
        compressed = [{"role": "user", "content": "压缩后的当前请求"}]
        attach_images(compressed, sid, **kwargs)
        assert compressed[-1]["content"][-1]["type"] == "image_url"
        assert len(run.attached_image_ids) == 1
    finally:
        state.close()
        memory.close()


def test_persona_presets_survive_reload_and_stale_editor_cannot_overwrite(tmp_path):
    from omni.system.persona_presets import read_presets, save_presets

    path = tmp_path / "private" / "presets.json"
    item = {
        "id": "local-one",
        "name": "小鱼",
        "desc": "本地预设",
        "prompt": "你是一个细心的中文助手，回答简洁清晰，遇到不确定的事情会先核实。",
    }
    first = save_presets(path, [item], "")
    assert first == read_presets(path)
    with pytest.raises(FileExistsError):
        save_presets(path, [], "")
    assert read_presets(path)["items"] == [item]
    with pytest.raises(ValueError, match="重复"):
        save_presets(path, [item, item], first["revision"])
    with pytest.raises(ValueError):
        save_presets(path, [{**item, "prompt": "密码是 abcdefghijk12345，帮我记住这个密码。"}], first["revision"])
    assert read_presets(path) == first


def test_fact_evidence_is_verified_and_historical_rows_are_not_given_invented_sources(tmp_path):
    from omni.memory.anchor import AnchorError, anchor_scope
    from omni.memory.store import MemoryStore

    store = MemoryStore(tmp_path / "memory.db")
    try:
        store.save_fact("历史记忆", source="legacy")
        with anchor_scope("[user] 我下周要去成都出差", source="review", source_ref="session:7:12-14"):
            with pytest.raises(AnchorError):
                store.save_fact("下周去北京", source="review", evidence="我下周要去北京出差")
            store.save_fact("下周去成都", source="review", evidence="我下周要去成都出差")
        rows = {row["content"]: row for row in store.fact_rows()}
        assert rows["历史记忆"]["evidence"] == rows["历史记忆"]["source_ref"] == ""
        assert rows["下周去成都"]["source_ref"] == "session:7:12-14"
        assert rows["下周去成都"]["evidence"] == "我下周要去成都出差"
        assert "下周去北京" not in rows
    finally:
        store.close()


def test_windows_credential_file_round_trip_never_contains_plaintext(tmp_path):
    from omni.system.protected_json import read_protected, write_protected

    path = tmp_path / "oauth.credential"
    secret = {"tokens": {"access_token": "fixture-unique-credential", "refresh_token": "fixture-refresh"}}
    write_protected(path, secret)
    assert b"fixture" not in path.read_bytes()
    assert read_protected(path) == secret
    path.write_bytes(b"invalid-payload")
    with pytest.raises((ValueError, OSError)):
        read_protected(path)


def test_visual_and_foreground_settings_have_one_editor_and_require_restart(tmp_path):
    from test_web import _client

    client, runtime = _client(tmp_path)
    try:
        updates = {
            "vision": {"send_to_cloud": True},
            "desktop": {"enabled": True, "foreground_enabled": True, "allowed_apps": [str(tmp_path / "notepad.exe")]},
        }
        response = client.post("/api/system/config", json={"updates": updates})
        assert response.status_code == 200, response.text
        assert response.json()["pending_restart"]
        saved = client.get("/api/system/config").json()
        assert saved["vision"]["send_to_cloud"] and saved["desktop"]["foreground_enabled"]
        assert not runtime.config.desktop.foreground_enabled
        assert not runtime.config.vision.send_to_cloud
        assert (
            client.post(
                "/api/system/config", json={"updates": {"desktop": {"allowed_apps": ["relative.exe"]}}}
            ).status_code
            == 400
        )
    finally:
        client.close()
        assert runtime.close()["complete"]


def test_forget_finds_normalized_exact_fact_without_fuzzy_ranking(tmp_path, monkeypatch):
    from omni.memory.store import MemoryStore
    from omni.tools.builtin import register_memory_tools
    from omni.tools.registry import ToolRegistry
    import json

    store = MemoryStore(tmp_path / "memory.db")
    try:
        target = "喜欢 Python，使用 Windows。"
        store.save_fact(target)
        store.save_fact(target, scope="group")
        for index in range(20):
            store.save_fact(f"喜欢 Python 的项目 {index}，使用 Windows。")
        monkeypatch.setattr(store, "search_facts", lambda *_args, **_kwargs: pytest.fail("遗忘不能依赖模糊检索排名"))
        registry = ToolRegistry()
        register_memory_tools(registry, store)
        from omni.safety.gates import AllowAllApprover

        result = registry.dispatch("forget", json.dumps({"text": "喜欢python使用windows"}), approver=AllowAllApprover())
        assert "已删除 1 条" in result
        assert not store.matching_fact_ids(target, scope="private")
        assert store.matching_fact_ids(target, scope="group")
        assert len(store.fact_rows()) == 21
    finally:
        store.close()


def test_background_recall_uses_injected_hybrid_search_without_counting_hits(tmp_path, monkeypatch):
    import json
    import yaml
    from omni.bootstrap import build_runtime
    from omni.config import AppConfig
    from omni.core.retrieval import HybridMemory
    from omni.memory.store import MemoryStore

    calls = []

    def semantic(self, query, limit=8, *, scope="private"):
        calls.append((query, scope))
        return ["喜欢无糖拿铁"]

    monkeypatch.setattr(HybridMemory, "search", semantic)
    monkeypatch.setattr(HybridMemory, "start", lambda self: None)
    config = AppConfig()
    config.embedding.enabled = True
    config.embedding.model = "fixture-embedding"
    config.embedding.dimensions = 2
    config.review.enabled = config.journal.enabled = True
    path = tmp_path / "config.yaml"
    path.write_text(yaml.safe_dump(config.model_dump(), allow_unicode=True), encoding="utf-8")
    runtime = build_runtime(
        config_path=path, data_dir=tmp_path / "data", skills_root=tmp_path / "skills", start_background_workers=True
    )
    runtime.store.save_fact("喜欢无糖拿铁")
    try:
        for factory in (runtime.reviewer._make_registry, runtime.journal_consolidator._make_registry):
            store = MemoryStore(runtime.paths.memory)
            try:
                result = factory(store).dispatch("recall", json.dumps({"query": "咖啡"}))
                assert "喜欢无糖拿铁" in result
            finally:
                store.close()
        assert calls == [("咖啡", "private"), ("咖啡", "private")]
        assert runtime.store.fact_rows()[0]["hits"] == 0
    finally:
        assert runtime.close()["complete"]
