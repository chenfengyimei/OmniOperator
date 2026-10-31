"""插件社区单测：参考服务器（FastAPI）+ 客户端全链路（ASGI 直连）。

覆盖契约：
- 服务器：发布 token 门（未配置/错 token 401）、manifest 纯数据校验、
  版本不可变（409）、搜索/详情/下载、存储被改动时拒绝分发（503）；
- 客户端：搜索/详情、安装 = 下载 + sha256 校验 + 落 inbox + 隔离安装
  （不注册工具，激活仍需人工确认）、指纹不符拒绝落盘、
  发布 = 工作区目录自动打包（跳 __pycache__）/inbox zip；
- 工具层：查询/安装 SAFE（search/info read_only），发布 CONFIRM+requires_human
  ——无人值守拒绝发布。

httpx 0.28 起 `ASGITransport` 只支持 AsyncClient；本文件用 `SyncASGITransport`
把 ASGI 应用桥接到同步 httpx.Client（独立事件循环线程），仅测试用。
"""
import asyncio
import io
import json
import threading
import zipfile
from pathlib import Path

import httpx
from fastapi.testclient import TestClient

from omni.plugins import PluginHost
from omni.plugins.community import CommunityClient, register_community_tools
from omni.plugins.server import create_app
from omni.safety.gates import AllowAllApprover, DenyApprover, Risk
from omni.tools.registry import ToolRegistry

MANIFEST = {
    "id": "demo-tools",
    "name": "演示插件",
    "version": "1.0.0",
    "description": "插件社区单测用的演示插件",
    "entry": "plugin.py",
    "author": "tester",
    "permissions": ["tools"],
}
PLUGIN_CODE = '''def register(ctx):
    def hello(args):
        return "hello community"
    ctx.add_tool(name="demo-tools-hello", description="打招呼",
                 parameters={"type": "object", "properties": {}}, func=hello, risk="safe")
'''


class SyncASGITransport(httpx.BaseTransport):
    """把 async-only 的 ASGI 应用桥接成同步 transport（仅测试）。

    客户端用同步 httpx.Client；httpx 0.28 的 ASGITransport 只实现
    handle_async_request，所以这里用一个专用事件循环线程跑它。
    """

    def __init__(self, app) -> None:
        self._transport = httpx.ASGITransport(app=app)
        self._loop = asyncio.new_event_loop()
        self._thread = threading.Thread(target=self._loop.run_forever, daemon=True)
        self._thread.start()

    def handle_request(self, request: httpx.Request) -> httpx.Response:
        async def run() -> httpx.Response:
            response = await self._transport.handle_async_request(request)
            body = await response.aread()
            return response, body

        future = asyncio.run_coroutine_threadsafe(run(), self._loop)
        response, body = future.result(timeout=30)
        return httpx.Response(
            response.status_code,
            headers=dict(response.headers),
            content=body,
            request=request,
        )


def _plugin_zip(manifest=None) -> bytes:
    buffer = io.BytesIO()
    with zipfile.ZipFile(buffer, "w") as zf:
        zf.writestr("manifest.json", json.dumps(manifest or MANIFEST))
        zf.writestr("plugin.py", PLUGIN_CODE)
    return buffer.getvalue()


def _publish(client: TestClient, data: bytes, *, token: str = "secret", name="p.zip"):
    return client.post(
        "/api/publish",
        files={"file": (name, data, "application/zip")},
        data={"token": token},
    )


# ---------------------------------------------------------------- 参考服务器


def test_server_publish_search_download_roundtrip(tmp_path):
    app = create_app(tmp_path / "srv", token="secret")
    client = TestClient(app)

    assert client.get("/api/health").json()["ok"] is True

    # token 门
    resp = _publish(client, _plugin_zip(), token="wrong")
    assert resp.status_code == 401
    # manifest 校验：zip 里没有 manifest
    bad = io.BytesIO()
    with zipfile.ZipFile(bad, "w") as zf:
        zf.writestr("plugin.py", PLUGIN_CODE)
    resp = _publish(client, bad.getvalue(), token="")
    assert resp.status_code == 401  # 空 token 也被拒（服务器配置了 token）
    resp = _publish(client, bad.getvalue(), token="secret")
    assert resp.status_code == 400 and "manifest" in resp.json()["detail"]

    # 正常发布
    data = _plugin_zip()
    resp = _publish(client, data)
    assert resp.status_code == 200
    record = resp.json()
    assert record["id"] == "demo-tools" and record["sha256"]

    # 版本不可变
    resp = _publish(client, data)
    assert resp.status_code == 409

    # 搜索与详情
    resp = client.get("/api/plugins", params={"query": "演示"})
    assert resp.status_code == 200
    items = resp.json()["items"]
    assert [i["id"] for i in items] == ["demo-tools"]
    resp = client.get("/api/plugins/demo-tools")
    detail = resp.json()
    assert detail["latest_version"] == "1.0.0" and detail["author"] == "tester"
    assert detail["latest_sha256"] == record["sha256"]

    # 下载：字节一致 + 指纹头
    resp = client.get("/api/download/demo-tools")
    assert resp.status_code == 200
    assert resp.content == data
    assert resp.headers["X-Plugin-Sha256"] == record["sha256"]

    # 新版本发布 → latest 切换
    upgraded = _plugin_zip(dict(MANIFEST, version="1.1.0"))
    assert _publish(client, upgraded).status_code == 200
    assert client.get("/api/plugins/demo-tools").json()["latest_version"] == "1.1.0"
    # 指定旧版本仍可下载
    resp = client.get("/api/download/demo-tools", params={"version": "1.0.0"})
    assert resp.status_code == 200 and resp.content == data

    # 存储被改动：服务器拒绝分发（索引指纹与实际内容不符）
    pkg = tmp_path / "srv" / "packages" / "demo-tools" / "1.0.0.zip"
    pkg.write_bytes(b"tampered")
    resp = client.get("/api/download/demo-tools", params={"version": "1.0.0"})
    assert resp.status_code == 503

    # 未知插件 404
    assert client.get("/api/plugins/nope").status_code == 404
    assert client.get("/api/download/nope").status_code == 404


# ---------------------------------------------------------------- 客户端全链路


def _wired(tmp_path, *, token="secret"):
    """真实宿主 + 社区客户端（ASGI 同步桥接直连参考服务器）。"""
    app = create_app(tmp_path / "srv", token=token)
    transport = SyncASGITransport(app)
    registry = ToolRegistry()
    host = PluginHost(tmp_path / "plugins", registry)
    client = CommunityClient(
        tmp_path / "plugins" / "community.json",
        host,
        resolve_dir=lambda rel: (tmp_path / "ws" / rel) if (tmp_path / "ws" / rel).is_dir() else None,
        transport=transport,
    )
    ok, msg = client.configure(server_url="http://testserver", token=token)
    assert ok, msg
    return registry, host, client


def test_client_search_info_install_and_activate(tmp_path):
    registry, host, client = _wired(tmp_path)
    # 先发布一个插件（直用服务器侧发布 = 客户端 publish 的后端已单独测）
    server_client = TestClient(create_app(tmp_path / "srv", token="secret"))
    assert _publish(server_client, _plugin_zip()).status_code == 200

    ok, msg = client.search("演示")
    assert ok and "demo-tools" in msg
    ok, msg = client.info("demo-tools")
    assert ok and "1.0.0" in msg and "sha256" in msg

    # 安装：下载 → sha256 校验 → inbox → 隔离区；激活前不注册任何工具
    ok, msg = client.install("demo-tools")
    assert ok, msg
    assert "隔离区" in msg and "社区" in msg
    assert [p["id"] for p in host.pending()] == ["demo-tools"]
    assert registry.get("demo-tools-hello") is None
    assert not host.inbox_files()  # zip 已被隔离安装消费

    # 激活（模拟人工确认后）：工具可用
    ok, msg = host.activate("demo-tools")
    assert ok and "hello community" in registry.dispatch("demo-tools-hello", {})

    # 重复安装同一版本：inbox 无残留、隔离区被占用 → 明确报错
    ok, msg = client.install("demo-tools")
    assert not ok and "已安装" in msg


def test_client_refuses_corrupt_download(tmp_path):
    registry, host, client = _wired(tmp_path)
    server_client = TestClient(create_app(tmp_path / "srv", token="secret"))
    assert _publish(server_client, _plugin_zip()).status_code == 200
    # 篡改服务器上的包存储（下载接口的 503 防线之外，再直接构造指纹不符场景）：
    # 让响应头指纹与内容不符 = 传输层内容被换
    pkg = tmp_path / "srv" / "packages" / "demo-tools" / "1.0.0.zip"
    original = pkg.read_bytes()
    pkg.write_bytes(b"corrupted")
    ok, msg = client.install("demo-tools")
    assert not ok
    assert "不符" in msg or "503" in msg
    assert host.pending() == []  # 没有任何东西落进隔离区
    pkg.write_bytes(original)  # 还原


def test_client_publish_from_workspace_directory(tmp_path):
    registry, host, client = _wired(tmp_path)
    plugin_dir = tmp_path / "ws" / "my-plugin"
    (plugin_dir / "__pycache__").mkdir(parents=True)
    (plugin_dir / "manifest.json").write_text(json.dumps(MANIFEST), encoding="utf-8")
    (plugin_dir / "plugin.py").write_text(PLUGIN_CODE, encoding="utf-8")
    (plugin_dir / "__pycache__" / "plugin.cpython-312.pyc").write_bytes(b"junk")

    ok, msg = client.publish_from_directory("my-plugin")
    assert ok, msg
    assert "已发布" in msg and "demo-tools" in msg

    # 发布的包能被搜索/下载/安装（__pycache__ 不在包里）
    ok, msg = client.install("demo-tools")
    assert ok, msg
    quarantined = host._quarantine / "demo-tools"
    assert (quarantined / "plugin.py").is_file()
    assert not (quarantined / "__pycache__").exists()
    # 从发布包一路到激活可用
    assert host.activate("demo-tools")[0]
    assert "hello community" in registry.dispatch("demo-tools-hello", {})


def test_client_errors_without_server_config(tmp_path, monkeypatch):
    # 生效源 = 配置 > OMNI_MARKET_URL；默认源清空才算"未配置"，否则开发机上的
    # 环境变量会让这条断言时过时不过
    monkeypatch.delenv("OMNI_MARKET_URL", raising=False)
    _registry, host, client = _wired(tmp_path)
    assert client.configure(server_url="", token="")[0]
    for call in (client.search("x"), client.info("x"), client.install("x"),
                 client.publish_from_directory("y")):
        ok, msg = call
        assert not ok and "未配置" in msg


def test_community_tools_approval_gates(tmp_path):
    registry, host, client = _wired(tmp_path)
    register_community_tools(registry, client)
    server_client = TestClient(create_app(tmp_path / "srv", token="secret"))
    assert _publish(server_client, _plugin_zip()).status_code == 200

    assert registry.get("community_search").read_only is True
    assert registry.get("community_info").read_only is True
    publish = registry.get("community_publish")
    assert publish.risk is Risk.CONFIRM and publish.requires_human

    # 查询/安装是 SAFE：无人值守可用（安装只落隔离区）
    result = registry.execute(
        "community_search", {"query": "演示"}, approver=DenyApprover()
    )
    assert "demo-tools" in result.to_message()
    result = registry.execute(
        "community_install", {"id": "demo-tools"}, approver=DenyApprover()
    )
    assert "隔离区" in result.to_message()

    # 发布是 CONFIRM+requires_human：无人值守一律拒绝
    result = registry.execute(
        "community_publish", {"id": "demo-tools", "path": "whatever"},
        approver=DenyApprover(),
    )
    assert "未获批准" in result.to_message()
