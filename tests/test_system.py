"""系统控制台单测：配置定向修改（保留注释）+ 服务管理（假目录）+ 工具 + 端点。"""
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from omni.bootstrap import build_runtime
from omni.channels.web import create_app
from omni.safety.gates import DenyApprover
from omni.system.config_io import (
    ConfigEditError,
    apply_updates,
    render_value,
    safe_write,
)
from omni.system.services import ServiceManager
from omni.tools.registry import ToolRegistry
from omni.tools.system_tools import register_system_tools

SAMPLE_YAML = """# 顶部说明注释
qq:
  enabled: false              # 开关注释要保留
  allowed_users: []           # 白名单注释
  reply_delay_s: [1.0, 3.0]

browser:
  headless: false             # 有头注释
  max_writes_per_day: 50
"""


# ---------- config_io ----------

def test_render_value():
    assert render_value("bool", True) == "true"
    assert render_value("int", 12) == "12"
    assert render_value("int_list", [1, 2]) == "[1, 2]"
    assert render_value("float_list", [0.5, 2]) == "[0.5, 2]"
    assert render_value("str", "small") == '"small"'
    for bad in [(True,), ("x",)]:
        with pytest.raises(ConfigEditError):
            render_value("int", bad[0])


def test_apply_updates_preserves_comments():
    out = apply_updates(
        SAMPLE_YAML,
        {
            "qq": {"enabled": True, "allowed_users": [10000, 20000]},
            "browser": {"headless": True, "max_writes_per_day": 5},
        },
    )
    assert "enabled: true              # 开关注释要保留" in out
    assert "allowed_users: [10000, 20000]           # 白名单注释" in out
    assert "headless: true             # 有头注释" in out
    assert "max_writes_per_day: 5" in out
    assert out.startswith("# 顶部说明注释")


def test_apply_updates_rejects_unknown_and_missing():
    # 未知 section/键：直接拒绝
    with pytest.raises(ConfigEditError, match="不允许修改"):
        apply_updates(SAMPLE_YAML, {"bogus": {"x": 1}})
    # 白名单内但 yaml 里不存在的键 → 未做破坏性改动
    with pytest.raises(ConfigEditError, match="没找到"):
        apply_updates(SAMPLE_YAML, {"review": {"enabled": True}})


def test_safe_write_atomic(tmp_path):
    target = tmp_path / "config.yaml"
    target.write_text(SAMPLE_YAML, encoding="utf-8")
    safe_write(target, apply_updates(SAMPLE_YAML, {"qq": {"enabled": True}}))
    assert "enabled: true" in target.read_text(encoding="utf-8")
    assert not (tmp_path / "config.yaml.tmp").exists()


# ---------- ServiceManager（假目录，不真启动进程） ----------

def _fake_napcat(tmp_path: Path, with_qr: bool, uin: str | None) -> Path:
    napcat = tmp_path / "napcat"
    (napcat).mkdir(parents=True, exist_ok=True)
    (napcat / "napcat.bat").write_text("@echo off", encoding="utf-8")
    cache = napcat / "napcat" / "cache"
    cache.mkdir(parents=True, exist_ok=True)
    if with_qr:
        (cache / "qrcode.png").write_bytes(b"\x89PNG-fake")
    if uin:
        cfg = napcat / "napcat" / "config"
        cfg.mkdir(parents=True, exist_ok=True)
        (cfg / f"onebot11_{uin}.json").write_text("{}", encoding="utf-8")
    return napcat


def _isolated_manager(napcat: Path, tmp_path: Path) -> ServiceManager:
    """隔离本机真实服务状态：不存在的端口（1/2）+ 独立的 pid/日志目录。

    pid 目录不隔离会读到真机的 data/run/*.pid——"未安装"类测试会误判
    服务在运行，停止类测试甚至会杀掉真实进程（已踩坑）。
    """
    return ServiceManager(
        napcat_dir=napcat,
        napcat_ws_port=1,
        napcat_webui_port=2,
        run_dir=tmp_path / "run",
        log_dir=tmp_path / "logs",
    )


def test_napcat_not_installed(tmp_path):
    manager = _isolated_manager(tmp_path / "nope", tmp_path)
    assert manager.napcat_dir is None
    qr = manager.napcat_qr()
    assert qr["available"] is False and qr["logged_in"] is False
    status = manager.status("napcat")
    assert status["running"] is False and "未登录" in status["detail"]


def test_napcat_qr_and_uin(tmp_path):
    napcat = _fake_napcat(tmp_path, with_qr=True, uin="123456")
    manager = _isolated_manager(napcat, tmp_path)
    qr = manager.napcat_qr()
    assert qr["available"] is True
    assert qr["image"].startswith("data:image/png;base64,")
    assert qr["uin"] == "123456"
    # logged_in 需要端口在监听（注入端口 1 上没有服务 → False）
    assert qr["logged_in"] is False


def test_overview_shape(tmp_path):
    napcat = _fake_napcat(tmp_path, with_qr=False, uin=None)
    manager = _isolated_manager(napcat, tmp_path)
    overview = manager.overview()
    names = [s["name"] for s in overview["services"]]
    assert names == ["napcat", "omni-qq", "omni-heartbeat"]
    assert overview["napcat"]["installed"] is True


def test_start_without_install_errors(tmp_path):
    manager = _isolated_manager(tmp_path / "nope", tmp_path)
    from omni.system.services import ServiceError

    with pytest.raises(ServiceError, match="未找到 NapCat"):
        manager.start("napcat")
    with pytest.raises(ServiceError, match="未在运行"):
        manager.stop("omni-qq")


# ---------- agent 系统工具 ----------

def test_system_tools_dispatch(tmp_path):
    from omni.safety.gates import AllowAllApprover
    registry = ToolRegistry()
    register_system_tools(registry, _isolated_manager(tmp_path / "nope", tmp_path))
    out = registry.dispatch("system_status", "{}")
    assert "NapCat" in out and ("未安装" in out or "未登录" in out)
    # 参数校验
    assert "错误" in registry.dispatch(
        "system_service", {"action": "reboot", "service": "napcat"}, approver=AllowAllApprover()
    )
    assert "错误" in registry.dispatch(
        "system_service", {"action": "start", "service": "everything"}, approver=AllowAllApprover()
    )
    # 未安装时启动 → 友好错误
    assert "错误" in registry.dispatch(
        "system_service", {"action": "start", "service": "napcat"}, approver=AllowAllApprover()
    )


# ---------- Web 端点 ----------

def test_system_endpoints(tmp_path, monkeypatch):
    runtime = build_runtime(
        approver=DenyApprover(),
        db_path=tmp_path / "m.db",
        skills_root=tmp_path / "skills",
    )
    client = TestClient(create_app(runtime))

    overview = client.get("/api/system/overview").json()
    assert {s["name"] for s in overview["services"]} == {
        "napcat", "omni-qq", "omni-heartbeat"
    }

    cfg = client.get("/api/system/config").json()
    assert "qq" in cfg and "editable" in cfg

    # 写配置：monkeypatch 到临时 yaml，避免动真实文件
    import omni.channels.web as web_mod

    tmp_cfg = tmp_path / "config.yaml"
    tmp_cfg.write_text(SAMPLE_YAML, encoding="utf-8")
    monkeypatch.setattr(web_mod, "CONFIG_PATH", tmp_cfg)
    resp = client.post(
        "/api/system/config",
        json={"updates": {"qq": {"enabled": True, "allowed_users": [42]}}},
    )
    assert resp.status_code == 200
    body = resp.json()
    assert body["ok"] is True and "重启" in body["hint"]
    text = tmp_cfg.read_text(encoding="utf-8")
    assert "enabled: true" in text and "# 开关注释要保留" in text
    assert "allowed_users: [42]" in text
    # 非法更新被 400 拒
    resp_bad = client.post(
        "/api/system/config", json={"updates": {"models": {"chat": "x"}}}
    )
    assert resp_bad.status_code == 400
    # 启动未安装的 napcat → 409
    monkeypatch.setattr(
        runtime.services, "_napcat_dir", tmp_path / "nope", raising=False
    )
    resp_start = client.post("/api/system/services/napcat/start", json={"qq": ""})
    assert resp_start.status_code == 409
