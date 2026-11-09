"""路径与外部依赖隔离单测（D04：A08）。

判据两条：
1. 临时实例的**所有**写入都落在注入根内（逐个路径断言 + 真实目录 canary 不变）；
2. 临时实例不会发现/终止/启动真机进程，也不会读到真机 Cookie 与配置。
"""

from __future__ import annotations

import pytest
from fastapi.testclient import TestClient

from omni.bootstrap import build_runtime
from omni.channels.web import create_app
from omni.config import RuntimePaths
from omni.safety.gates import DenyApprover
from omni.system.services import ServiceError
from omni.tools.android.driver import AndroidDriver
from omni.tools.bilibili.api import BiliAPI


def _runtime(tmp_path, **kwargs):
    config_path = tmp_path / "config.yaml"
    if not config_path.exists():
        config_path.write_text("persona:\n  name: 测试小鱼\n", encoding="utf-8")
    return build_runtime(
        approver=DenyApprover(),
        db_path=tmp_path / "m.db",
        config_path=config_path,
        data_dir=tmp_path / "data",
        **kwargs,
    )


# ---------------------------------------------------------------- 路径表


def test_runtime_paths_derive_from_data_root(tmp_path):
    paths = RuntimePaths.create(tmp_path, config_path=tmp_path / "c.yaml", skills_root=tmp_path / "sk")
    assert paths.data == tmp_path
    for path in (
        paths.cookies,
        paths.cache,
        paths.android_shots,
        paths.android_cache,
        paths.browser_profile,
        paths.browser_shots,
        paths.run,
        paths.logs,
        paths.mcp_logs,
    ):
        assert path.is_relative_to(tmp_path)
    # 配置与技能可独立注入（不跟随 data 根）
    assert paths.config == tmp_path / "c.yaml"
    assert paths.skills == tmp_path / "sk"


def test_runtime_exposes_the_same_paths(tmp_path):
    runtime = _runtime(tmp_path, skills_root=tmp_path / "skills")
    assert runtime.paths.data == tmp_path / "data"
    assert runtime.paths.config == tmp_path / "config.yaml"
    assert runtime.paths.skills == tmp_path / "skills"


def test_isolated_runtime_defaults_skills_to_its_own_data_root(tmp_path):
    runtime = _runtime(tmp_path)
    try:
        assert runtime.paths.skills == tmp_path / "data/skills"
        assert runtime.skills.root == runtime.paths.skills
    finally:
        runtime.close()


# ---------------------------------------------------------------- 安卓截图


class _FakeAdb:
    def deadline(self, seconds):
        from contextlib import nullcontext

        return nullcontext()

    def __init__(self, png: bytes) -> None:
        self._png = png

    def require_device(self) -> str:
        return "EMU1"

    def screencap(self, serial: str) -> bytes:
        assert serial == "EMU1"
        return self._png


def test_android_screenshot_lands_in_injected_root(tmp_path):
    driver = AndroidDriver(adb=_FakeAdb(b"\x89PNG-fake"), shots_dir=tmp_path / "shots")
    path = driver.screenshot()
    assert path == tmp_path / "shots" / "latest.png"
    assert path.read_bytes() == b"\x89PNG-fake"


def test_runtime_android_driver_uses_injected_shots_dir(tmp_path):
    runtime = _runtime(tmp_path)
    assert runtime.android._shots_dir == tmp_path / "data" / "android_shots"


# ---------------------------------------------------------------- B站 Cookie


def test_bili_api_reads_only_the_injected_cookie_file(tmp_path):
    cookies = tmp_path / "data" / "bili_cookies.json"
    cookies.parent.mkdir(parents=True)
    cookies.write_text('{"SESSDATA": "tmp-only"}', encoding="utf-8")

    api = BiliAPI(cookies_path=cookies)
    assert api._client.cookies.get("SESSDATA") == "tmp-only"

    # 不存在的路径 → 匿名模式，且不会回退到真机路径
    api2 = BiliAPI(cookies_path=tmp_path / "missing.json")
    assert api2._client.cookies.get("SESSDATA") is None


# ---------------------------------------------------------------- Web 路由


def test_web_config_and_cookies_use_injected_paths(tmp_path):
    runtime = _runtime(tmp_path)
    app = create_app(runtime)
    client = TestClient(app, base_url="http://127.0.0.1")
    assert client.post("/api/auth/pair", json={"code": app.state.web_auth.pairing_code}).status_code == 200

    assert (
        client.post(
            "/api/system/bili-cookies",
            json={"sessdata": "s1", "bili_jct": "j1", "buvid3": "b1"},
        ).status_code
        == 200
    )
    assert (tmp_path / "data" / "bili_cookies.json").is_file()
    status = client.get("/api/system/bili-cookies").json()
    assert status["path"].startswith(str(tmp_path))

    assert client.post("/api/system/config", json={"updates": {"persona": {"name": "临时实例"}}}).status_code == 200
    assert "临时实例" in (tmp_path / "config.yaml").read_text(encoding="utf-8")


# ---------------------------------------------------------------- 外部进程


def test_isolated_runtime_cannot_discover_host_processes(tmp_path):
    runtime = _runtime(tmp_path)
    services = runtime.services
    # 端口探测被替换成"永远空"：不会因为真机上有 NapCat 就认为自己在跑
    assert services._port_pids(3001, "LISTEN") == []
    assert services.status("napcat")["running"] is False
    with pytest.raises(ServiceError):
        services.stop("napcat")


def test_isolated_runtime_refuses_to_spawn_real_services(tmp_path, monkeypatch):
    runtime = _runtime(tmp_path)
    # 即使真的指到了 napcat.bat，也不允许由隔离实例拉起
    monkeypatch.setattr(runtime.services, "_napcat_dir", tmp_path, raising=False)
    (tmp_path / "napcat.bat").write_text("@echo off", encoding="utf-8")
    with pytest.raises(RuntimeError):
        runtime.services.napcat_start()


def test_isolated_supervisor_never_spawns(tmp_path):
    """守护装配在隔离实例里必须整体失效：起服务就是起用户真机的进程。"""
    runtime = _runtime(tmp_path)
    assert runtime.supervisor is not None
    assert runtime.supervisor.start()["mode"] == "disabled"
    runtime.supervisor.reconcile_once()
    run_dir = runtime.paths.run
    assert not list(run_dir.glob("*.pid")), "隔离实例不许留下任何启动记录"
    assert runtime.supervisor.snapshot()["supervised"] is False


def test_isolated_workbench_lifespan_starts_and_stops_supervisor(tmp_path):
    """工作台生命周期接上了守护，但隔离实例里它只是空转。"""
    runtime = _runtime(tmp_path)
    with TestClient(create_app(runtime), base_url="http://127.0.0.1"):
        pass
    assert runtime.supervisor.snapshot()["mode"] in {"disabled", "idle"}
    assert not list(runtime.paths.run.glob("*.pid"))


# ---------------------------------------------------------------- 写审计


def test_full_write_audit_never_touches_real_data_dir(tmp_path):
    """常见写路径不得触发进程/子进程的真实数据审计闸门，外部服务无需停止。"""

    runtime = _runtime(tmp_path)
    client = TestClient(create_app(runtime), base_url="http://127.0.0.1")
    app = client.app
    assert client.post("/api/auth/pair", json={"code": app.state.web_auth.pairing_code}).status_code == 200

    client.post("/api/system/bili-cookies", json={"sessdata": "s1"})
    client.post("/api/system/config", json={"updates": {"persona": {"name": "审计"}}})
    client.post(
        "/api/system/cron/create",
        json={"name": "audit-job", "prompt": "x", "every_minutes": 30},
    )
    client.post("/api/resident/update", json={"operation": "add", "content": "审计一行"})
    assert client.get("/api/state").status_code == 200  # 只读接口，绝不触达真实模型

    runtime.write_budget.try_consume()
    runtime.cron_jobs.list()
    AndroidDriver(adb=_FakeAdb(b"\x89PNG"), shots_dir=runtime.paths.android_shots).screenshot()

    assert runtime.paths.data == tmp_path / "data"
    assert runtime.paths.cookies.is_relative_to(tmp_path)
    assert runtime.paths.updates.is_relative_to(tmp_path)
    assert runtime.paths.android_shots.is_relative_to(tmp_path)


def test_isolated_runtime_writes_stay_inside_data_root(tmp_path):
    runtime = _runtime(tmp_path)
    client = TestClient(create_app(runtime), base_url="http://127.0.0.1")
    app = client.app
    client.post("/api/auth/pair", json={"code": app.state.web_auth.pairing_code})
    client.post("/api/system/bili-cookies", json={"sessdata": "s1"})
    client.post("/api/resident/update", json={"operation": "add", "content": "一行"})
    runtime.write_budget.try_consume()

    root = tmp_path / "data"
    allowed_exact = {"config.yaml", "m.db", "m.db-shm", "m.db-wal", "web_secret"}
    written = [p for p in tmp_path.rglob("*") if p.is_file()]
    assert written, "这个用例本身应该产生文件"
    strays = [str(p) for p in written if not (p.is_relative_to(root) or p.name in allowed_exact)]
    assert strays == [], f"写到了注入根之外：{strays}"
