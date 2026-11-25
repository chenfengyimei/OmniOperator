"""独立 Agent 的数据、路径、设置 CAS 和公开文件区，全部落临时目录。"""

from __future__ import annotations
from pathlib import Path
from dataclasses import replace

import pytest
from fastapi.testclient import TestClient

from omni.bootstrap import build_runtime
from omni.channels.web import create_app
from omni.config import RuntimePaths
from omni.system.agent_spaces import AgentDirectoryStore, AgentProfile, DirectoryConflict
from omni.system.agent_spaces.layout import local_path


@pytest.fixture
def spaces(tmp_path):
    config = tmp_path / "config" / "config.yaml"
    config.parent.mkdir()
    config.write_text("review:\n  enabled: false\n", encoding="utf-8")
    legacy = RuntimePaths.create(tmp_path / "data", config_path=config, skills_root=tmp_path / "skills")
    return AgentDirectoryStore(tmp_path / "data" / "control" / "agents.json", legacy, isolated=True)


def config_for(spaces, active="writer"):
    return {
        "version": 1,
        "active": active,
        "agents": [
            spaces.snapshot()["agents"][0],
            {"id": "writer", "name": "写作 Agent"},
            {"id": "research", "name": "研究 Agent"},
        ],
    }


def test_preview_is_read_only_and_paths_independent(spaces):
    before = list(spaces.legacy.data.rglob("*"))
    data = config_for(spaces)
    resolved = spaces.preview(data)
    assert list(spaces.legacy.data.rglob("*")) == before
    assert resolved[1]["directories"]["memory"].endswith("writer\\memory")
    spaces.save(data, revision="", acknowledge=True)
    a, b = spaces.selected("writer"), spaces.selected("research")
    assert a.paths.memory != b.paths.memory and a.paths.state != b.paths.state
    assert a.paths.run == b.paths.run == spaces.legacy.run
    for layout in (a, b):
        for name in (
            "memory",
            "resident",
            "history",
            "state",
            "skills",
            "cookies",
            "browser_profile",
            "command_logs",
            "mcp_logs",
            "backups",
            "files",
            "documents",
            "workspace",
        ):
            assert getattr(layout.paths, name).is_relative_to(layout.home)


def test_catalog_cas_confirmation_and_corruption_fail_closed(spaces):
    data = config_for(spaces)
    with pytest.raises(ValueError, match="确认"):
        spaces.save(data, revision="")
    snapshot = spaces.save(data, revision="", acknowledge=True)
    with pytest.raises(DirectoryConflict):
        spaces.save(data, revision="", acknowledge=True)
    assert spaces.snapshot() == snapshot
    spaces.path.write_text("{broken", encoding="utf-8")
    with pytest.raises(ValueError):
        spaces.selected()


@pytest.mark.parametrize(
    "path",
    [
        "../outside",
        "C:relative",
        "//server/share",
        "D:/safe/a:stream",
        "nul",
        "bad.",
        "x/../y",
        "/../x",
        "D:/",
        "bad?name",
    ],
)
def test_invalid_local_paths_are_rejected(tmp_path, path):
    with pytest.raises(ValueError):
        local_path(path, tmp_path)


def test_reject_overlaps_cross_agent_and_isolation_escape(spaces):
    data = config_for(spaces)
    data["agents"][1]["directories"] = {"memory": "workspace"}
    with pytest.raises(ValueError, match="覆盖"):
        spaces.preview(data)
    data = config_for(spaces)
    data["agents"][1]["home"] = str(spaces.legacy.data.parent / "escape")
    with pytest.raises(ValueError, match="隔离"):
        spaces.preview(data)
    data = config_for(spaces)
    data["agents"][2]["directories"] = {"files": str(spaces.legacy.data / "agents" / "writer" / "workspace")}
    with pytest.raises(ValueError, match="覆盖"):
        spaces.preview(data)


def test_legacy_memory_directory_can_change_inside_protected_data(spaces):
    spaces.legacy = replace(spaces.legacy, workspace=spaces.legacy.data.parent)
    data = config_for(spaces)
    data["agents"][0]["directories"] = {"memory": "organized-memory"}
    resolved = spaces.preview(data)
    assert Path(resolved[0]["directories"]["memory"]) == spaces.legacy.data / "organized-memory"


def test_directory_junction_is_rejected_without_writing(tmp_path, monkeypatch):
    monkeypatch.setattr(Path, "is_junction", lambda self: self.name == "linked")
    with pytest.raises(ValueError, match="junction"):
        local_path("linked/workspace", tmp_path)
    assert not (tmp_path / "linked").exists()


def test_two_runtimes_do_not_share_memory_state_files_or_skills(spaces):
    spaces.save(config_for(spaces), revision="", acknowledge=True)
    runtimes = []
    try:
        for identifier in ("writer", "research"):
            runtime = build_runtime(
                config_path=spaces.legacy.config,
                data_dir=spaces.legacy.data,
                directory_catalog=spaces.path,
                agent_id=identifier,
                start_background_workers=False,
            )
            runtimes.append(runtime)
        a, b = runtimes
        a.store.save_fact("只属于写作空间的事实")
        assert not b.store.fact_rows()
        assert a.agent.session_id == b.agent.session_id  # 相同数值 ID 也不会共库。
        a.public_workspaces["files"].root.joinpath("writer.txt").write_text("独立文件", encoding="utf-8")
        assert not b.public_workspaces["files"].root.joinpath("writer.txt").exists()
        assert a.command_jobs.root == a.paths.workspace
        assert a.config.terminal.roots == [str(a.paths.workspace)]
        assert a.services._run_dir == b.services._run_dir
        assert a.paths.logs != b.paths.logs
        with pytest.raises(ValueError):
            a.workspace.resolve(str(a.paths.documents / "资料.txt"), for_write=True)
        with pytest.raises(ValueError):
            a.workspace.resolve(str(b.paths.files / "writer.txt"))
    finally:
        for runtime in runtimes:
            assert runtime.close()["complete"]


@pytest.fixture
def client(spaces):
    spaces.save(config_for(spaces), revision="", acknowledge=True)
    runtime = build_runtime(
        config_path=spaces.legacy.config,
        data_dir=spaces.legacy.data,
        directory_catalog=spaces.path,
        start_background_workers=False,
    )
    app = create_app(runtime)
    client = TestClient(app, base_url="http://127.0.0.1")
    client.post("/api/auth/pair", json={"code": app.state.web_auth.pairing_code})
    try:
        yield client, runtime
    finally:
        client.close()
        assert runtime.close()["complete"]


def test_storage_api_cas_pending_restart_and_fixed_running_space(client):
    web, runtime = client
    snapshot = web.get("/api/storage").json()
    assert snapshot["running"]["id"] == "writer" and not snapshot["restart_required"]
    data = {key: snapshot[key] for key in ("version", "active", "agents")}
    data["active"] = "research"
    response = web.put("/api/storage", json={"revision": snapshot["revision"], "catalog": data, "acknowledge": True})
    assert response.status_code == 200, response.text
    assert response.json()["restart_required"] and response.json()["running"]["id"] == "writer"
    assert (
        web.put(
            "/api/storage", json={"revision": snapshot["revision"], "catalog": data, "acknowledge": True}
        ).status_code
        == 409
    )
    assert runtime.paths.agent_id == "writer"


def test_manual_documents_upload_preview_and_no_overwrite(client):
    web, runtime = client
    url = "/api/storage/upload?scope=documents&path=课程/资料.txt"
    assert web.post(url, content="中文资料".encode()).status_code == 200
    assert web.post(url, content=b"overwrite").status_code == 409
    listing = web.get("/api/storage/files", params={"scope": "documents", "path": "课程"}).json()
    assert listing["entries"][0]["name"] == "资料.txt"
    preview = web.get("/api/storage/preview-file", params={"scope": "documents", "path": "课程/资料.txt"}).json()
    assert preview["text"] == "中文资料"
    assert web.post("/api/storage/upload?scope=files&path=.env", content=b"secret").status_code == 403
    assert web.post("/api/storage/upload?scope=files&path=../../other.txt", content=b"escape").status_code == 403
    assert web.get("/api/storage/files?scope=memory").status_code == 404
    assert (
        web.post("/api/storage/upload?scope=files&path=large.bin", content=b"x" * (8 * 1024 * 1024 + 1)).status_code
        == 413
    )
    assert not (runtime.paths.files / "large.bin").exists()
    assert web.post("/api/storage/folders", json={"scope": "files", "path": "整理/项目"}).status_code == 200
    assert (runtime.paths.files / "整理" / "项目").is_dir()
    web.cookies.clear()
    assert web.get("/api/storage").status_code == 401
    assert web.get("/api/storage/files").status_code == 401


def test_preview_is_bounded_and_binary_not_decoded(client):
    web, runtime = client
    (runtime.paths.files / "large.txt").write_bytes(b"x" * 20000)
    (runtime.paths.files / "binary.bin").write_bytes(b"\0private")
    data = web.get("/api/storage/preview-file?scope=files&path=large.txt").json()
    assert data["truncated"] and len(data["text"]) == 16384
    data = web.get("/api/storage/preview-file?scope=files&path=binary.bin").json()
    assert data["binary"] and data["text"] is None
    (runtime.paths.files / "document.pdf").write_bytes(b"%PDF-1.7\nfixture")
    data = web.get("/api/storage/preview-file?scope=files&path=document.pdf").json()
    assert data["binary"] and data["text"] is None


def test_backup_uses_custom_memory_and_includes_public_material(client, tmp_path):
    from omni.system.backup import backup_runtime, restore_into

    web, runtime = client
    runtime.paths.resident.parent.mkdir(parents=True, exist_ok=True)
    runtime.paths.resident.write_text("独立常驻记忆", encoding="utf-8")
    (runtime.paths.documents / "样本.txt").write_text("保留资料", encoding="utf-8")
    (runtime.paths.files / "result.txt").write_text("保留产物", encoding="utf-8")
    backup = tmp_path / "snapshot"
    report = backup_runtime(runtime.paths, backup)
    assert (backup / "MEMORY.md").read_text(encoding="utf-8") == "独立常驻记忆"
    assert {"files", "documents"} <= {row.rel for row in report.entries}
    restored = tmp_path / "restored"
    restore_into(backup, restored)
    assert (restored / "documents" / "样本.txt").read_text(encoding="utf-8") == "保留资料"
    with pytest.raises(Exception, match="备份目标"):
        backup_runtime(runtime.paths, runtime.paths.documents / "backup")


def test_service_child_environment_and_safe_observation_use_same_space(client, monkeypatch):
    from omni.system import services

    web, runtime = client
    captured = []

    class Spawn:
        pid = 12345

    def fake_spawn(*args, **kwargs):
        captured.append(kwargs)
        return Spawn()

    monkeypatch.setattr(services.subprocess, "Popen", fake_spawn)
    # 只验证注入的环境构造，绝不启动真实进程。
    from functools import partial

    spawn = partial(
        services._spawn,
        environment={"OMNI_AGENT_ID": runtime.paths.agent_id, "OMNI_AGENT_CATALOG": str(runtime.directories.path)},
    )
    spawn(["fake", "omni.channels.qq"], runtime.paths.workspace, runtime.paths.logs / "fake.log")
    assert captured[0]["env"]["OMNI_AGENT_ID"] == "writer"
    assert captured[0]["env"]["OMNI_AGENT_CATALOG"] == str(runtime.directories.path)
    result = runtime.registry.get("peek_command").func({"command": "dir"})
    assert result.status == "ok", result
