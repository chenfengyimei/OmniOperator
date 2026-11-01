"""系统控制台单测：配置定向修改（保留注释）+ 服务管理（假目录）+ 工具 + 端点。"""
from pathlib import Path

import pytest
import yaml
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
    assert render_value("float_list", [0.5, 2]) == "[0.5, 2.0]"  # 小数形态要保持
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


def test_apply_updates_rejects_unknown_key():
    # 未知 section/键：直接拒绝（白名单是唯一入口，拼错的键进不来）
    with pytest.raises(ConfigEditError, match="不允许修改"):
        apply_updates(SAMPLE_YAML, {"bogus": {"x": 1}})
    with pytest.raises(ConfigEditError, match="不允许修改"):
        apply_updates(SAMPLE_YAML, {"qq": {"no_such_key": 1}})


def test_apply_updates_appends_missing_key():
    """白名单内但 yaml 里没有的键要**补进去**，不能整次保存失败。

    真实事故：config.yaml 缺 approval 节，而界面每次都提交 approval.mode，
    于是"保存全部设置"永远 400，用户只看到一句与原因无关的脱敏报错。
    """
    out = apply_updates(SAMPLE_YAML, {"qq": {"timezone": "Asia/Shanghai"}})
    assert 'timezone: "Asia/Shanghai"' in out
    # 补在 qq 节里，不能跑到 browser 节下面
    assert out.index("timezone:") < out.index("browser:")
    # 原有注释与值不受影响
    assert "enabled: false              # 开关注释要保留" in out
    # 再存一次不该再多写一行
    assert apply_updates(out, {"qq": {"timezone": "Asia/Shanghai"}}) == out


def test_apply_updates_creates_missing_section():
    """整节都不存在时新建一节（按顶层缩进 2 空格），且幂等。"""
    out = apply_updates(SAMPLE_YAML, {"approval": {"mode": "smart"}})
    assert "approval:" in out and 'mode: "smart"' in out
    assert out.rstrip().endswith('mode: "smart"')
    assert apply_updates(out, {"approval": {"mode": "smart"}}) == out


def test_apply_updates_multiline_does_not_duplicate_key():
    """多行块替换曾把 `key: |` 写两遍——每保存一次人设就多一行空块。

    判据：替换后 YAML 只有一处 system_prompt，且二次保存字节不变。
    """
    src = "persona:\n  name: x\n  system_prompt: |\n    旧一\n    旧二\n\nmodels:\n  chat: a\n"
    once = apply_updates(src, {"persona": {"system_prompt": ["新一", "新二"]}})
    yaml.safe_load(once)  # 必须仍是合法 YAML（重复键会让结构悄悄变形）
    assert once.count("system_prompt:") == 1
    assert "新一" in once and "旧一" not in once
    assert apply_updates(once, {"persona": {"system_prompt": ["新一", "新二"]}}) == once


def test_apply_updates_multiline_appends_when_block_missing():
    """多行块不存在时不能静默丢内容，要补一块。"""
    out = apply_updates(SAMPLE_YAML, {"persona": {"system_prompt": ["只有一行"]}})
    assert "persona:" in out and "system_prompt: |" in out
    assert "    只有一行" in out
    assert yaml.safe_load(out)["persona"]["system_prompt"].strip() == "只有一行"


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


# ---------- 记录收敛（旧版裸 PID / 死进程 / PID 复用） ----------

def _manager_with_probe(tmp_path: Path, inspector):
    """只有 inspector 可注入：端口探测固定为空，避免读到真机 NapCat。"""
    return ServiceManager(
        napcat_dir=None,
        napcat_ws_port=1,
        napcat_webui_port=2,
        run_dir=tmp_path / "run",
        log_dir=tmp_path / "logs",
        port_prober=lambda _port, _state: [],
        tcp_probe=lambda _port: False,
        inspector=inspector,
    )


def _pid_file(tmp_path: Path, name: str) -> Path:
    return tmp_path / "run" / f"{name}.pid"


def test_reconcile_clears_dead_legacy_pid(tmp_path):
    """旧版裸 PID 指向已退出的进程：记录没有意义，清掉才能自动拉起。"""
    path = _pid_file(tmp_path, "omni-qq")
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("12345", encoding="utf-8")
    manager = _manager_with_probe(tmp_path, lambda _pid: None)
    verdict = manager.reconcile("omni-qq")
    assert verdict["state"] == "cleared_dead" and verdict["pid"] == 12345
    assert not path.exists()
    # 收敛只动记录，不碰进程：没有 inspector 返回身份就不可能有终止动作
    assert manager.status("omni-qq")["state"] == "absent"


def test_reconcile_quarantines_corrupt_record(tmp_path):
    """既不是记录也不是数字：移进 quarantine 留证，不静默删除。"""
    path = _pid_file(tmp_path, "omni-heartbeat")
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("{半截 JSON", encoding="utf-8")
    manager = _manager_with_probe(tmp_path, lambda _pid: None)
    verdict = manager.reconcile("omni-heartbeat")
    assert verdict["state"] == "quarantined"
    assert not path.exists()
    quarantined = list((tmp_path / "run" / "quarantine").iterdir())
    assert len(quarantined) == 1
    assert quarantined[0].read_text(encoding="utf-8") == "{半截 JSON"


def test_reconcile_clears_recycled_pid(tmp_path):
    """裸 PID 被无关进程复用了：镜像名对不上，记录已失效。"""
    from omni.system.process_identity import ProcessIdentity

    path = _pid_file(tmp_path, "omni-qq")
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("4242", encoding="utf-8")
    notepad = ProcessIdentity(4242, "1", str(tmp_path / "notepad.exe"))
    manager = _manager_with_probe(tmp_path, lambda pid: notepad if pid == 4242 else None)
    verdict = manager.reconcile("omni-qq")
    assert verdict["state"] == "cleared_reused"
    assert not path.exists()


def test_reconcile_blocks_live_unverified(tmp_path):
    """裸 PID 指向的还真是 python 进程：不认领、不启动、不杀，只标记。"""
    from omni.system.process_identity import ProcessIdentity

    path = _pid_file(tmp_path, "omni-qq")
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("4242", encoding="utf-8")
    pythonish = ProcessIdentity(4242, "1", str(tmp_path / "python.exe"))
    manager = _manager_with_probe(tmp_path, lambda pid: pythonish if pid == 4242 else None)
    verdict = manager.reconcile("omni-qq")
    assert verdict["state"] == "blocked_unverified" and verdict["pid"] == 4242
    assert path.exists(), "不可核验的记录必须留着，否则下次会重复撞同一个进程"
    assert manager.status("omni-qq")["state"] == "blocked_unverified"
    from omni.system.services import ServiceError

    with pytest.raises(ServiceError, match="不可核验"):
        manager.start("omni-qq")


def test_reconcile_keeps_verified_record(tmp_path):
    """记录与进程身份完全一致时什么都不做。"""
    import json

    from omni.system.process_identity import ProcessIdentity

    executable = str(tmp_path / "python.exe")
    identity = ProcessIdentity(4242, "100", executable)
    path = _pid_file(tmp_path, "omni-qq")
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps({
        "version": 1, "service": "omni-qq", "launch_id": "a" * 32,
        "pid": 4242, "created": "100", "executable": executable,
    }), encoding="utf-8")
    manager = _manager_with_probe(tmp_path, lambda pid: identity if pid == 4242 else None)
    verdict = manager.reconcile("omni-qq")
    assert verdict["state"] == "verified"
    assert path.exists()
    status = manager.status("omni-qq")
    assert status["running"] is True and status["can_stop"] is True


def test_reconcile_clears_stale_verified_record(tmp_path):
    """记录合法但进程已经退出：清掉，别让"记录存在"被当成"还在跑"。"""
    import json

    executable = str(tmp_path / "python.exe")
    path = _pid_file(tmp_path, "omni-heartbeat")
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps({
        "version": 1, "service": "omni-heartbeat", "launch_id": "b" * 32,
        "pid": 5252, "created": "100", "executable": executable,
    }), encoding="utf-8")
    manager = _manager_with_probe(tmp_path, lambda _pid: None)
    assert manager.status("omni-heartbeat")["state"] == "stale"
    assert manager.reconcile("omni-heartbeat")["state"] == "cleared_dead"
    assert not path.exists()


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


def test_system_service_structured_results():
    """启停成功=applied；ServiceError.uncertain=副作用未知；其余错误=确定未生效。"""
    from omni.safety.gates import AllowAllApprover
    from omni.system.services import ServiceError

    class FakeServices:
        def __init__(self, exc=None):
            self._exc = exc

        def overview(self):  # 结构化测试不触达真实管理器
            return {"services": [], "napcat": {"installed": False, "logged_in": False, "uin": None,
                                               "qr_available": False}}

        def start(self, name, qq=""):
            if self._exc is not None:
                raise self._exc
            return {"name": name, "pid": 4321, "started": True}

        def stop(self, name):
            if self._exc is not None:
                raise self._exc
            return {"name": name, "stopped": True, "graceful": True}

    def build(exc=None):
        registry = ToolRegistry()
        register_system_tools(registry, FakeServices(exc))
        return registry

    ok = build().execute(
        "system_service", {"action": "start", "service": "omni-qq"}, approver=AllowAllApprover()
    )
    assert ok.status == "ok" and ok.side_effect_state == "applied"
    assert ok.text.startswith("已启动 omni-qq") and ok.legacy is False

    uncertain = build(ServiceError("omni-qq 停止未确认：x", uncertain=True)).execute(
        "system_service", {"action": "stop", "service": "omni-qq"}, approver=AllowAllApprover()
    )
    assert uncertain.status == "error" and uncertain.side_effect_state == "unknown"
    assert uncertain.error_code == "service_error"

    definite = build(ServiceError("未找到 NapCat（检查 config.yaml system.napcat_dir 指向 napcat.bat 所在目录）")).execute(
        "system_service", {"action": "start", "service": "omni-qq"}, approver=AllowAllApprover()
    )
    assert definite.status == "error" and definite.side_effect_state == "none"

    bad_action = build().execute(
        "system_service", {"action": "reboot", "service": "omni-qq"}, approver=AllowAllApprover()
    )
    assert bad_action.error_code == "bad_arguments"


# ---------- Web 端点 ----------

def test_system_endpoints(tmp_path, monkeypatch):
    # 用带注释的样例配置启动：写回要验"保留注释"，且必须落在临时文件上
    config_path = tmp_path / "boot.yaml"
    config_path.write_text(SAMPLE_YAML, encoding="utf-8")
    runtime = build_runtime(
        approver=DenyApprover(),
        db_path=tmp_path / "m.db",
        config_path=config_path,
        skills_root=tmp_path / "skills",
        data_dir=tmp_path / "data",
    )
    app = create_app(runtime)
    client = TestClient(app, base_url="http://127.0.0.1")
    # 工作台身份：先配对（Host 必须是本地主机名）
    assert client.post(
        "/api/auth/pair", json={"code": app.state.web_auth.pairing_code}
    ).status_code == 200

    overview = client.get("/api/system/overview").json()
    assert {s["name"] for s in overview["services"]} == {
        "napcat", "omni-qq", "omni-heartbeat"
    }

    cfg = client.get("/api/system/config").json()
    assert "qq" in cfg and "editable" in cfg

    # 写配置：必须落在注入的配置源上（runtime.paths.config），不碰真实 config.yaml
    tmp_cfg = runtime.paths.config
    assert tmp_cfg == config_path
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
    # 白名单外的键被 400 拒（白名单是唯一入口）
    resp_bad = client.post(
        "/api/system/config", json={"updates": {"models": {"nope": "x"}}}
    )
    assert resp_bad.status_code == 400
    # 白名单内但 yaml 里缺这一节：补进去并成功（以前这里会 400，把整次保存打死）
    resp_fill = client.post(
        "/api/system/config", json={"updates": {"approval": {"mode": "smart"}}}
    )
    assert resp_fill.status_code == 200
    assert 'mode: "smart"' in tmp_cfg.read_text(encoding="utf-8")
    # 启动未安装的 napcat → 409
    monkeypatch.setattr(
        runtime.services, "_napcat_dir", tmp_path / "nope", raising=False
    )
    resp_start = client.post("/api/system/services/napcat/start", json={"qq": ""})
    assert resp_start.status_code == 409


def test_apply_updates_preserves_crlf():
    """Windows 上 config.yaml 是 CRLF；保存不能把整个文件改成 LF。

    否则每存一次 git diff 就显示"全文件都改了"，等于把版本管理搞废。
    """
    src = SAMPLE_YAML.replace("\n", "\r\n")
    out = apply_updates(src, {"qq": {"enabled": True}})
    assert "\r\n" in out
    assert out.count("\r\n") == out.count("\n")  # 没有混入裸 LF
    assert "enabled: true" in out
    # 原本是 LF 的文件不该被改成 CRLF
    assert "\r\n" not in apply_updates(SAMPLE_YAML, {"qq": {"enabled": True}})


def test_read_config_text_preserves_crlf(tmp_path):
    """read_config_text 必须保住 CRLF。

    `Path.read_text()` 的通用换行模式会在读的时候就把 CRLF 变成 LF，
    于是"行尾还原"逻辑永远不触发——这条锁住真正的入口。
    """
    from omni.system.config_io import read_config_text

    target = tmp_path / "config.yaml"
    target.write_bytes("a: 1\r\nb: 2\r\n".encode("utf-8"))
    assert read_config_text(target) == "a: 1\r\nb: 2\r\n"
    assert "\r\n" in read_config_text(target)
