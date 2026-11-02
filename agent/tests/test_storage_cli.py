"""运维 CLI 必须选择与运行入口相同的数据，且解析/诊断不能创建空间。"""

from __future__ import annotations

import json
import runpy
import sqlite3
from pathlib import Path
from types import SimpleNamespace

import pytest

from omni.channels.doctor import main as doctor_main
from omni.system.backup import main as backup_main, restore_into
from omni.system.agent_spaces.inspection import load_runtime_paths


def seed_memory(path: Path, texts: list[str]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with sqlite3.connect(path) as db:
        db.execute("CREATE TABLE facts(content TEXT)")
        db.executemany("INSERT INTO facts VALUES (?)", [(text,) for text in texts])


@pytest.fixture
def configured(tmp_path, monkeypatch):
    control = tmp_path / "data"
    config = tmp_path / "config" / "config.yaml"
    config.parent.mkdir()
    config.write_text("{}\n", encoding="utf-8")
    registry = config.with_name("agents.json")
    registry.write_text(
        json.dumps(
            {
                "version": 1,
                "active": "writer",
                "application": {"data_root": str(control / "organized")},
                "agents": [
                    {"id": "default", "name": "默认"},
                    {"id": "writer", "name": "写作"},
                    {"id": "research", "name": "研究"},
                ],
            }
        ),
        encoding="utf-8",
    )
    monkeypatch.setenv("OMNI_STORAGE_BASE", str(control))
    monkeypatch.setenv("OMNI_CONFIG_PATH", str(config))
    monkeypatch.setenv("OMNI_AGENT_CATALOG", str(registry))
    monkeypatch.delenv("OMNI_AGENT_ID", raising=False)
    return control, config, registry


def test_doctor_reads_configured_space_without_creating_runtime(configured, capsys):
    control, config, registry = configured
    paths = load_runtime_paths()
    seed_memory(paths.memory, ["写作事实", "写作档案"])
    seed_memory(control / "memory.db", ["错误的旧库"])
    before = sorted(str(p) for p in control.rglob("*"))
    assert doctor_main([]) == 0
    report = json.loads(capsys.readouterr().out)
    memory = next(row for row in report["checks"] if row["name"] == "memory.db")
    assert memory["counts"]["facts"] == 2
    assert sorted(str(p) for p in control.rglob("*")) == before
    assert not paths.state.exists()
    assert not paths.skills.exists()


def test_backup_follows_configured_memory_resident_and_documents(configured, tmp_path):
    control, _config, _registry = configured
    paths = load_runtime_paths()
    seed_memory(paths.memory, ["当前 Agent 的长期事实"])
    seed_memory(control / "memory.db", ["旧目录内容不可混入"])
    paths.resident.write_text("当前常驻笔记", encoding="utf-8")
    paths.documents.mkdir(parents=True)
    (paths.documents / "资料.txt").write_text("独立资料", encoding="utf-8")
    target = tmp_path / "backup"
    assert backup_main(["--to", str(target)]) == 0
    with sqlite3.connect(target / "memory.db") as db:
        assert db.execute("SELECT content FROM facts").fetchall() == [("当前 Agent 的长期事实",)]
    restored = tmp_path / "restored"
    restore_into(target, restored)
    assert (restored / "MEMORY.md").read_text(encoding="utf-8") == "当前常驻笔记"
    assert (restored / "documents" / "资料.txt").read_text(encoding="utf-8") == "独立资料"


def test_explicit_agent_overrides_environment_and_active(configured, monkeypatch, capsys):
    monkeypatch.setenv("OMNI_AGENT_ID", "research")
    selected = load_runtime_paths(agent_id="writer")
    other = load_runtime_paths()
    seed_memory(selected.memory, ["一", "二"])
    seed_memory(other.memory, ["三"])
    assert doctor_main(["--agent", "writer"]) == 0
    report = json.loads(capsys.readouterr().out)
    assert next(c for c in report["checks"] if c["name"] == "memory.db")["counts"]["facts"] == 2


def test_explicit_data_root_ignores_agent_catalog_environment(configured, tmp_path, monkeypatch):
    _, config, _ = configured
    monkeypatch.setenv("OMNI_AGENT_ID", "nonexistent")
    isolated = tmp_path / "isolated"
    paths = load_runtime_paths(data_dir=isolated, config_path=config, skills_root=isolated / "skills")
    assert paths.agent_id == "default"
    assert paths.memory == isolated / "memory.db"
    assert not isolated.exists()


def test_corrupt_catalog_does_not_backup_or_diagnose_another_space(configured, tmp_path, capsys):
    control, _, registry = configured
    seed_memory(control / "memory.db", ["不可回退"])
    registry.write_text("{broken", encoding="utf-8")
    assert doctor_main([]) == 2
    assert json.loads(capsys.readouterr().out)["status"] == "error"
    target = tmp_path / "not-created"
    assert backup_main(["--to", str(target)]) == 2
    assert not target.exists()


def test_missing_agent_is_error_without_directory_creation(configured, capsys):
    control, _, _ = configured
    assert doctor_main(["--agent", "missing"]) == 2
    assert not control.exists()
    assert "未检查其它数据目录" in capsys.readouterr().out


def test_explicit_memory_and_skills_override_selected_space(configured, tmp_path):
    memory = tmp_path / "custom" / "memory.db"
    skills = tmp_path / "custom-skills"
    paths = load_runtime_paths(memory_path=memory, skills_root=skills)
    assert paths.memory == memory and paths.skills == skills
    assert paths.agent_id == "writer"
    assert not memory.parent.exists() and not skills.exists()


def test_restore_does_not_require_current_catalog(configured, tmp_path):
    _, _, registry = configured
    paths = load_runtime_paths()
    seed_memory(paths.memory, ["可恢复"])
    target = tmp_path / "backup"
    assert backup_main(["--to", str(target)]) == 0
    registry.write_text("{broken", encoding="utf-8")
    restored = tmp_path / "restored"
    assert backup_main(["--restore", str(target), "--into", str(restored)]) == 0
    assert (restored / "memory.db").is_file()


def script_main(name: str):
    script = Path(__file__).resolve().parents[1] / "scripts" / name
    return runpy.run_path(str(script))["main"]


def test_upgrade_rehearsal_selects_registered_space(configured, tmp_path, monkeypatch):
    main = script_main("rehearse_upgrade.py")
    captured = []

    def rehearse(paths, output):
        captured.append(paths)
        return {"passed": True}

    monkeypatch.setitem(main.__globals__, "rehearse", rehearse)
    with pytest.raises(SystemExit) as result:
        main(["--run", "--output", str(tmp_path / "evidence"), "--agent", "research"])
    assert result.value.code == 0
    assert captured[0].agent_id == "research"
    assert captured[0].state == load_runtime_paths(agent_id="research").state
    assert not captured[0].state.parent.exists()


def test_soak_campaign_identity_and_sampling_use_same_frozen_space(configured, tmp_path, monkeypatch):
    main = script_main("soak_monitor.py")
    namespace = main.__globals__
    captured = {}

    def start(output, limits, **kwargs):
        captured["data"] = kwargs["data_dir"]
        return {"source": "test", "services": [], "interval_s": 60}

    def collect(paths, *args, **kwargs):
        captured["paths"] = paths
        return {"sample": True}

    def append(*args):
        return {"samples": 1, "elapsed_hours": 0, "soak_passed": False, "blockers": ["不足 72 小时"]}

    clock = iter([0, 1])
    for key, value in {
        "start": start,
        "collect": collect,
        "append": append,
        "time": SimpleNamespace(monotonic=lambda: next(clock)),
    }.items():
        monkeypatch.setitem(namespace, key, value)
    limits = tmp_path / "limits.json"
    limits.write_text("{}", encoding="utf-8")
    main(["run", "--output", str(tmp_path / "evidence"), "--limits", str(limits), "--hours", "0.0001"])
    assert captured["paths"].agent_id == "writer"
    assert captured["data"] == captured["paths"].data
    assert captured["paths"].run == configured[0] / "run"
    assert not captured["data"].exists()
