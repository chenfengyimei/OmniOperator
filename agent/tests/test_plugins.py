"""插件系统单测：隔离安装 → 人工确认激活 → 装载注册 → 启停/卸载全生命周期。

覆盖契约：
- 校验零执行：安装只进隔离区，注册表不出现任何插件工具；
- 激活即装载：工具经 PluginContext 注册（id- 前缀、toolset=plugin:<id>），
  风险等级照常走既有审批链（无人值守拒绝 CONFIRM 级）；
- 防篡改：激活前对隔离区逐文件 sha256 比对；启用前对包内容指纹复核；
- 装载失败被关在插件内：部分注册回滚、运行时不崩、错误如实可查；
- 上下文契约：无前缀名/未知风险/重名注册一律拒绝装载；
- 重启恢复：新宿主 load_installed 把已启用插件工具注册回来。
"""
import json
import zipfile
from pathlib import Path

import pytest

from omni.plugins import PluginHost
from omni.plugins.manifest import PluginManifest
from omni.safety.gates import AllowAllApprover, DenyApprover, Risk
from omni.tools.registry import Tool, ToolRegistry
from omni.tools.skill_tools import register_skill_inbox_tools  # noqa: F401 —— 确认无循环依赖


MANIFEST = {
    "id": "demo-tools",
    "name": "演示插件",
    "version": "1.0.0",
    "description": "插件系统单测用的演示插件",
    "entry": "plugin.py",
    "permissions": ["tools"],
}

PLUGIN_CODE = '''"""演示插件入口。"""


def register(ctx):
    def hello(args):
        return f"hello {args.get('who', 'world')}"

    ctx.add_tool(
        name="demo-tools-hello",
        description="打个招呼",
        parameters={"type": "object", "properties": {}},
        func=hello,
        risk="safe",
    )
'''


def _host(tmp_path, **kwargs) -> tuple[ToolRegistry, PluginHost]:
    registry = ToolRegistry()
    registry.register(Tool("get_time", "内置工具", {}, lambda a: "now"))
    host = PluginHost(tmp_path / "plugins", registry, **kwargs)
    return registry, host


def _drop_plugin(host: PluginHost, filename="demo.zip", *, manifest=None, code=None, extra=None):
    entries = {
        "manifest.json": json.dumps(manifest or MANIFEST).encode("utf-8"),
        "plugin.py": (code or PLUGIN_CODE).encode("utf-8"),
    }
    entries.update(extra or {})
    with zipfile.ZipFile(host._inbox / filename, "w") as zf:
        for name, data in entries.items():
            zf.writestr(name, data)


# ---------------------------------------------------------------- 层边界守卫


def test_plugin_host_responsibilities_stay_split():
    """host / package_reader / quarantine 三层不再次合流：

    - host 只做生命周期编排，不含包读取与指纹实现；
    - package_reader 零执行：不 import 注册表，不 importlib 装载；
    - quarantine 只做文件状态：不 importlib、不接触注册表。
    """
    src = Path(__file__).resolve().parents[1] / "src" / "omni" / "plugins"

    host_src = (src / "host.py").read_text(encoding="utf-8")
    for gone in ("def _read_zip", "def _read_directory", "def _validate_files",
                 "def _finalize_quarantine", "def _content_sha256", "def _compare_manifests"):
        assert gone not in host_src, f"host.py 不该再承担 {gone}（归 package_reader/quarantine）"

    reader_src = (src / "package_reader.py").read_text(encoding="utf-8")
    assert "importlib" not in reader_src, "包读取层必须零执行：不得 import importlib"
    assert "ToolRegistry" not in reader_src, "包读取层不得依赖注册表"

    quarantine_src = (src / "quarantine.py").read_text(encoding="utf-8")
    assert "importlib" not in quarantine_src, "隔离状态层只做文件读写：不得 import importlib"
    assert "ToolRegistry" not in quarantine_src, "隔离状态层不得依赖注册表"


# ---------------------------------------------------------------- 安装：隔离、零执行


def test_install_quarantines_without_registering_anything(tmp_path):
    registry, host = _host(tmp_path)
    _drop_plugin(host)
    ok, msg = host.install_package("demo.zip")
    assert ok, msg
    assert "隔离区" in msg and "plugin.py" in msg
    # 隔离期：注册表与已安装列表都没有它
    assert registry.get("demo-tools-hello") is None
    assert host.installed() == []
    pending = host.pending()
    assert [p["id"] for p in pending] == ["demo-tools"]
    assert host.inbox_files()


def test_install_supports_wrapper_dir_zip(tmp_path):
    _registry, host = _host(tmp_path)
    entries = {
        "demo-tools/manifest.json": json.dumps(MANIFEST).encode("utf-8"),
        "demo-tools/plugin.py": PLUGIN_CODE.encode("utf-8"),
    }
    with zipfile.ZipFile(host._inbox / "wrapped.zip", "w") as zf:
        for name, data in entries.items():
            zf.writestr(name, data)
    ok, msg = host.install_package("wrapped.zip")
    assert ok, msg
    assert [p["id"] for p in host.pending()] == ["demo-tools"]


def test_bad_packages_rejected_whole(tmp_path):
    registry, host = _host(tmp_path)
    cases = {
        "缺 manifest": {"manifest.json": None, "evil.py": b"x"},
        "入口不匹配": None,  # 单独构造
        "两个顶层 py": None,
        "脚本附件": None,
        "白名单外文件": None,
        "路径越界": None,
        "声明未知能力": None,
    }
    # 缺 manifest
    with zipfile.ZipFile(host._inbox / "a.zip", "w") as zf:
        zf.writestr("plugin.py", PLUGIN_CODE)
    ok, msg = host.install_package("a.zip")
    assert not ok and "manifest.json" in msg
    # 入口不匹配：声明 plugin.py，包里是 other.py
    with zipfile.ZipFile(host._inbox / "b.zip", "w") as zf:
        zf.writestr("manifest.json", json.dumps(MANIFEST).encode("utf-8"))
        zf.writestr("other.py", PLUGIN_CODE.encode("utf-8"))
    ok, msg = host.install_package("b.zip")
    assert not ok and "入口" in msg
    # 两个顶层 .py
    with zipfile.ZipFile(host._inbox / "c.zip", "w") as zf:
        zf.writestr("manifest.json", json.dumps(MANIFEST).encode("utf-8"))
        zf.writestr("plugin.py", PLUGIN_CODE.encode("utf-8"))
        zf.writestr("helper.py", b"x = 1\n")
    ok, msg = host.install_package("c.zip")
    assert not ok and "唯一的顶层 .py" in msg
    # 脚本附件（references 里放 .py）
    with zipfile.ZipFile(host._inbox / "d.zip", "w") as zf:
        zf.writestr("manifest.json", json.dumps(MANIFEST).encode("utf-8"))
        zf.writestr("plugin.py", PLUGIN_CODE.encode("utf-8"))
        zf.writestr("references/run.py", b"print('boom')\n")
    ok, msg = host.install_package("d.zip")
    assert not ok and "白名单" in msg
    # 白名单外可执行
    with zipfile.ZipFile(host._inbox / "e.zip", "w") as zf:
        zf.writestr("manifest.json", json.dumps(MANIFEST).encode("utf-8"))
        zf.writestr("plugin.py", PLUGIN_CODE.encode("utf-8"))
        zf.writestr("evil.exe", b"MZ")
    ok, msg = host.install_package("e.zip")
    assert not ok and "白名单" in msg
    # zip-slip
    with zipfile.ZipFile(host._inbox / "f.zip", "w") as zf:
        zf.writestr("manifest.json", json.dumps(MANIFEST).encode("utf-8"))
        zf.writestr("plugin.py", PLUGIN_CODE.encode("utf-8"))
        zf.writestr("../evil.md", b"out")
    ok, msg = host.install_package("f.zip")
    assert not ok and "不安全" in msg
    # 声明未知能力
    bad = dict(MANIFEST, permissions=["fs", "net"])
    with zipfile.ZipFile(host._inbox / "g.zip", "w") as zf:
        zf.writestr("manifest.json", json.dumps(bad).encode("utf-8"))
        zf.writestr("plugin.py", PLUGIN_CODE.encode("utf-8"))
    ok, msg = host.install_package("g.zip")
    assert not ok and "未知能力" in msg
    # 全部被拒：隔离区干净
    assert host.pending() == []
    assert registry.get("demo-tools-hello") is None


def test_manifest_field_validation():
    cases = [
        ({}, "id"),
        (dict(MANIFEST, id="Bad Id"), "id"),
        (dict(MANIFEST, version="latest"), "version"),
        (dict(MANIFEST, description=""), "description"),
        (dict(MANIFEST, entry="sub/plugin.py"), "entry"),
        (dict(MANIFEST, permissions=[]), "permissions"),
    ]
    for data, expect in cases:
        result = PluginManifest.from_dict(data)
        assert isinstance(result, str), expect
        assert expect in result
    manifest = PluginManifest.from_dict(MANIFEST)
    assert isinstance(manifest, PluginManifest) and manifest.id == "demo-tools"


# ---------------------------------------------------------------- 激活与审批边界


def test_activate_registers_tool_and_dispatch_works(tmp_path):
    registry, host = _host(tmp_path)
    _drop_plugin(host)
    assert host.install_package("demo.zip")[0]
    ok, msg = host.activate("demo-tools")
    assert ok, msg
    assert "demo-tools-hello" in msg
    tool = registry.get("demo-tools-hello")
    assert tool is not None and tool.toolset == "plugin:demo-tools"
    assert tool.risk is Risk.SAFE
    assert "hello omni" in registry.dispatch("demo-tools-hello", {"who": "omni"})
    assert "hello world" in registry.dispatch("demo-tools-hello", {})
    # 内置工具不受影响
    assert registry.dispatch("get_time", {}) == "now"


def test_activation_and_removal_require_human_approval(tmp_path):
    from omni.plugins.tools import register_plugin_tools

    registry, host = _host(tmp_path)
    register_plugin_tools(registry, host)
    _drop_plugin(host)
    assert registry.execute("plugin_install", {"package": "demo.zip"}, approver=DenyApprover()).status == "ok"

    activate = registry.get("plugin_activate")
    assert activate.risk is Risk.CONFIRM and activate.requires_human
    remove = registry.get("plugin_remove")
    assert remove.risk is Risk.CONFIRM and remove.requires_human
    assert registry.get("plugin_list").read_only is True

    # 无人值守：激活/卸载被拒，安装/查询可用
    result = registry.execute("plugin_activate", {"id": "demo-tools"}, approver=DenyApprover())
    assert "未获批准" in result.to_message()
    result = registry.execute("plugin_remove", {"id": "demo-tools"}, approver=DenyApprover())
    assert "未获批准" in result.to_message()
    # 有人在环：激活成功
    result = registry.execute("plugin_activate", {"id": "demo-tools"}, approver=AllowAllApprover())
    assert "已激活" in result.to_message()
    result = registry.execute("plugin_remove", {"id": "demo-tools"}, approver=AllowAllApprover())
    assert "已卸载" in result.to_message()
    assert registry.get("demo-tools-hello") is None


def test_plugin_tool_risk_goes_through_approver_chain(tmp_path):
    """插件声明的 CONFIRM 工具照常被审批链拦住。"""
    code = '''
def register(ctx):
    def write(args):
        return "写了"
    ctx.add_tool(name="conf-demo-write", description="写操作演示",
                 parameters={"type": "object", "properties": {}}, func=write, risk="confirm")
'''
    registry, host = _host(tmp_path)
    _drop_plugin(host, filename="conf.zip", manifest=dict(MANIFEST, id="conf-demo"), code=code)
    assert host.install_package("conf.zip")[0]
    assert host.activate("conf-demo")[0]
    result = registry.execute("conf-demo-write", {}, approver=DenyApprover())
    assert "未获批准" in result.to_message()
    result = registry.execute("conf-demo-write", {}, approver=AllowAllApprover())
    assert "写了" in result.to_message()


# ---------------------------------------------------------------- 启停 / 卸载 / 防篡改


def test_disable_enable_roundtrip(tmp_path):
    registry, host = _host(tmp_path)
    _drop_plugin(host)
    assert host.install_package("demo.zip")[0]
    assert host.activate("demo-tools")[0]

    ok, msg = host.disable("demo-tools")
    assert ok and "已禁用" in msg and "不会卸载" in msg
    assert registry.get("demo-tools-hello") is None
    assert host.installed()[0]["enabled"] is False

    ok, msg = host.enable("demo-tools")
    assert ok and "已启用" in msg
    assert "hello world" in registry.dispatch("demo-tools-hello", {})
    assert host.installed()[0]["enabled"] is True


def test_activate_detects_quarantine_tampering(tmp_path):
    _registry, host = _host(tmp_path)
    _drop_plugin(host)
    assert host.install_package("demo.zip")[0]
    # 导入后改动 entry：激活拒绝
    (host._quarantine / "demo-tools" / "plugin.py").write_text("x = 1\n", encoding="utf-8")
    ok, msg = host.activate("demo-tools")
    assert not ok and "不一致" in msg
    # 塞进新文件：同样拒绝
    (host._quarantine / "demo-tools" / "plugin.py").write_text(PLUGIN_CODE, encoding="utf-8")
    (host._quarantine / "demo-tools" / "sneak.py").write_text("x = 2\n", encoding="utf-8")
    ok, msg = host.activate("demo-tools")
    assert not ok and "新增" in msg


def test_enable_detects_installed_tampering(tmp_path):
    _registry, host = _host(tmp_path)
    _drop_plugin(host)
    assert host.install_package("demo.zip")[0]
    assert host.activate("demo-tools")[0]
    assert host.disable("demo-tools")[0]
    # 激活后被改：启用拒绝，要求重新走安装→激活
    (host._root / "demo-tools" / "plugin.py").write_text("x = 1\n", encoding="utf-8")
    ok, msg = host.enable("demo-tools")
    assert not ok and "不一致" in msg and "重新" in msg


def test_remove_deletes_package_and_data(tmp_path):
    registry, host = _host(tmp_path)
    _drop_plugin(host)
    assert host.install_package("demo.zip")[0]
    assert host.activate("demo-tools")[0]
    data_dir = host._data / "demo-tools"
    data_dir.mkdir(parents=True, exist_ok=True)
    (data_dir / "state.json").write_text("{}", encoding="utf-8")

    ok, msg = host.remove("demo-tools")
    assert ok and "已卸载" in msg
    assert not (host._root / "demo-tools").exists()
    assert not data_dir.exists()
    assert host.installed() == []
    assert registry.get("demo-tools-hello") is None
    # 卸载后再装同名包：允许（全新周期）
    _drop_plugin(host, filename="demo2.zip")
    assert host.install_package("demo2.zip")[0]


def test_remove_quarantined_package_only(tmp_path):
    _registry, host = _host(tmp_path)
    _drop_plugin(host)
    assert host.install_package("demo.zip")[0]
    ok, msg = host.remove("demo-tools")
    assert ok and "隔离区" in msg
    assert host.pending() == []


# ---------------------------------------------------------------- 装载失败与契约


def test_import_time_crash_is_contained(tmp_path):
    registry, host = _host(tmp_path)
    code = 'raise RuntimeError("插件导入即爆炸")\n'
    _drop_plugin(host, code=code)
    assert host.install_package("demo.zip")[0]
    ok, msg = host.activate("demo-tools")
    # 激活成功（用户已确认、代码就位），装载失败如实报告
    assert ok and "装载失败" in msg and "爆炸" in msg
    item = host.installed()[0]
    assert item["enabled"] is True and "爆炸" in item["error"]
    assert registry.get("demo-tools-hello") is None
    # 修复后 enable 仍拒绝：内容指纹变了，必须重走「安装→人工确认激活」链
    (host._root / "demo-tools" / "plugin.py").write_text(PLUGIN_CODE, encoding="utf-8")
    ok, msg = host.enable("demo-tools")
    assert not ok and "不一致" in msg
    # 正规恢复：卸载 → 重装 → 重新激活（每一步都过各自的审批/确认门）
    assert host.remove("demo-tools")[0]
    _drop_plugin(host, filename="fixed.zip")
    assert host.install_package("fixed.zip")[0]
    ok, msg = host.activate("demo-tools")
    assert ok and "demo-tools-hello" in msg


def test_partial_registration_rolls_back(tmp_path):
    """注册一半再抛异常：已注册的工具必须回滚干净。"""
    registry, host = _host(tmp_path)
    code = '''
def register(ctx):
    def hello(args):
        return "hi"
    ctx.add_tool(name="demo-tools-hello", description="打招呼",
                 parameters={"type": "object", "properties": {}}, func=hello, risk="safe")
    raise ValueError("注册到一半炸了")
'''
    _drop_plugin(host, code=code)
    assert host.install_package("demo.zip")[0]
    ok, msg = host.activate("demo-tools")
    assert "装载失败" in msg
    assert registry.get("demo-tools-hello") is None


@pytest.mark.parametrize(
    "name,phrase",
    [
        ("remember", "前缀"),      # 试图影子覆盖内置工具：连前缀都没有，直接拒
        ("demo-tools", "前缀"),
        ("demo-tools_x", "前缀"),
    ],
)
def test_context_rejects_contract_violations(tmp_path, name, phrase):
    """无前缀（含试图影子覆盖内置工具 remember）/重名注册一律拒绝装载。"""
    registry, host = _host(tmp_path)
    code = f'''
def register(ctx):
    def hello(args):
        return "hi"
    ctx.add_tool(name="{name}", description="打招呼",
                 parameters={{"type": "object", "properties": {{}}}}, func=hello, risk="safe")
'''
    _drop_plugin(host, code=code)
    assert host.install_package("demo.zip")[0]
    ok, msg = host.activate("demo-tools")
    assert "装载失败" in msg
    assert registry.get(name) is None


def test_context_rejects_unknown_risk(tmp_path):
    registry, host = _host(tmp_path)
    code = '''
def register(ctx):
    def hello(args):
        return "hi"
    ctx.add_tool(name="demo-tools-hello", description="打招呼",
                 parameters={"type": "object", "properties": {}}, func=hello, risk="ultra")
'''
    _drop_plugin(host, code=code)
    assert host.install_package("demo.zip")[0]
    ok, msg = host.activate("demo-tools")
    assert "装载失败" in msg and "risk" in msg


# ---------------------------------------------------------------- 工作区目录安装与重启恢复


def test_install_from_workspace_directory(tmp_path):
    workspace = tmp_path / "ws"
    plugin_dir = workspace / "my-plugin"
    (plugin_dir / "references").mkdir(parents=True)
    (plugin_dir / "manifest.json").write_text(json.dumps(MANIFEST), encoding="utf-8")
    (plugin_dir / "plugin.py").write_text(PLUGIN_CODE, encoding="utf-8")
    (plugin_dir / "references" / "note.md").write_text("参考资料", encoding="utf-8")

    registry = ToolRegistry()
    host = PluginHost(
        tmp_path / "plugins",
        registry,
        resolve_dir=lambda rel: (workspace / rel) if (workspace / rel).is_dir() else None,
    )
    ok, msg = host.install_directory("my-plugin")
    assert ok, msg
    assert "工作区" in msg
    assert [p["id"] for p in host.pending()] == ["demo-tools"]
    assert host.activate("demo-tools")[0]
    assert "hello world" in registry.dispatch("demo-tools-hello", {})


def test_fresh_host_restores_enabled_plugins(tmp_path):
    """重启模拟：新宿主 + 新注册表，load_installed 把已启用插件装回来。"""
    registry, host = _host(tmp_path)
    _drop_plugin(host)
    assert host.install_package("demo.zip")[0]
    assert host.activate("demo-tools")[0]

    fresh_registry = ToolRegistry()
    fresh_host = PluginHost(tmp_path / "plugins", fresh_registry)
    failures = fresh_host.load_installed()
    assert failures == []
    assert "hello world" in fresh_registry.dispatch("demo-tools-hello", {})
    # 停用的插件不会被恢复
    assert fresh_host.disable("demo-tools")[0]
    third_registry = ToolRegistry()
    third_host = PluginHost(tmp_path / "plugins", third_registry)
    assert third_host.load_installed() == []
    assert third_registry.get("demo-tools-hello") is None
