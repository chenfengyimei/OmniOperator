"""市场接口单测：/api/market/* 全部在本机隔离环境里跑，不连真实社区。

守的几条：
- 源未配置时报"没设置源"而不是崩；配置后 /status 会真的探一次 /api/health；
- 索引/详情来自注入的 CommunityClient（httpx.MockTransport），字段逐项收窄，
  坏条目只丢那一条；上游 5xx 映射成 502，不把异常原文甩给前端；
- 安装 = 下载 + 指纹校验 + 隔离安装，**不注册任何工具**；激活才装载并注册，
  且激活前提是防篡改复核通过；丢弃只删隔离区；
- 人设卡导入是纯文本：接口只回卡片，不写配置。
"""

from __future__ import annotations

import io
import json
import zipfile

import httpx
import pytest
from test_web import _client

from omni.plugins.community import CommunityClient

MANIFEST = {
    "id": "demo-tools",
    "name": "演示插件",
    "version": "1.0.0",
    "description": "市场接口单测用的演示插件",
    "entry": "plugin.py",
    "author": "tester",
    "permissions": ["tools"],
}
PLUGIN_CODE = """def register(ctx):
    def hello(args):
        return "hello market"
    ctx.add_tool(name="demo-tools-hello", description="打招呼",
                 parameters={"type": "object", "properties": {}}, func=hello, risk="safe")
"""


def _zip() -> bytes:
    buffer = io.BytesIO()
    with zipfile.ZipFile(buffer, "w") as archive:
        archive.writestr("manifest.json", json.dumps(MANIFEST, ensure_ascii=False))
        archive.writestr("plugin.py", PLUGIN_CODE)
    return buffer.getvalue()


PLUGIN_ITEM = {
    "id": "demo-tools",
    "kind": "plugin",
    "name": "演示插件",
    "description": "d" * 900,
    "author": "tester",
    "homepage": "javascript:alert(1)",
    "tags": ["工具", "工具", "演示", "t" * 40],
    "latest_version": "1.0.0",
    "versions": ["1.0.0"],
    "downloads": 12,
    "updated_at": "2026-11-01T00:00:00+08:00",
    "sha256": "a" * 64,
    "size": 2048,
    "files": [{"path": "manifest.json", "size": 120}, {"path": "../evil", "size": 1}],
}

PERSONA_ITEM = {
    "id": "xiaoyu",
    "kind": "persona",
    "name": "小鱼",
    "description": "默认人设",
    "system_prompt": "你是小鱼",
    "greeting": "在。",
    "tags": ["默认"],
    "author": "official",
    "latest_version": "1.0",
    "sha256": "b" * 64,
}


class Upstream:
    """假社区源：按路径回放索引/详情/健康/下载，记录被请求的路径。"""

    def __init__(self, *, fail: str = "") -> None:
        self.paths: list[str] = []
        self._fail = fail
        self._package = _zip()

    def __call__(self, request: httpx.Request) -> httpx.Response:
        path = request.url.path
        self.paths.append(path)
        if self._fail == "boom":
            return httpx.Response(503, json={"detail": "社区在维护中"})
        if path == "/api/health":
            return httpx.Response(200, json={"ok": True, "plugins": 3, "personas": 6})
        if path == "/api/plugins":
            return httpx.Response(200, json={"total": 1, "items": [PLUGIN_ITEM]})
        if path == "/api/plugins/demo-tools":
            return httpx.Response(200, json={**PLUGIN_ITEM, "latest_sha256": "a" * 64, "latest_size": 2048})
        if path == "/api/download/demo-tools":
            import hashlib

            return httpx.Response(
                200,
                content=self._package,
                headers={"X-Plugin-Sha256": hashlib.sha256(self._package).hexdigest()},
            )
        if path == "/api/personas":
            return httpx.Response(
                200,
                json={"total": 2, "items": [PERSONA_ITEM, {"id": "../evil", "name": "坏", "system_prompt": "x"}]},
            )
        if path == "/api/personas/xiaoyu":
            return httpx.Response(200, json=PERSONA_ITEM)
        if path == "/api/personas/broken":
            return httpx.Response(200, json={"id": "broken", "name": "缺提示词"})
        return httpx.Response(404, json={"detail": "没有这个接口"})


def _wired(tmp_path, monkeypatch, *, fail: str = "", source: str = "http://market.test"):
    monkeypatch.delenv("OMNI_MARKET_URL", raising=False)
    client, runtime = _client(tmp_path)
    upstream = Upstream(fail=fail)
    runtime.community = CommunityClient(
        tmp_path / "data" / "plugins" / "community.json",
        runtime.plugins,
        transport=httpx.MockTransport(upstream),
    )
    if source:
        ok, message = runtime.community.configure(server_url=source)
        assert ok, message
    return client, runtime, upstream


@pytest.mark.parametrize("installed,available", [("0.9.0", True), ("1.0.0", False), ("legacy-version", False)])
def test_market_compares_versions_without_failing_on_legacy_metadata(tmp_path, monkeypatch, installed, available):
    client, runtime, _ = _wired(tmp_path, monkeypatch)
    monkeypatch.setattr(runtime.plugins, "installed", lambda: [{"id": "demo-tools", "version": installed}])
    try:
        response = client.get("/api/market/plugins")
        assert response.status_code == 200
        card = response.json()["items"][0]
        assert card["installed_version"] == installed
        assert card["update_available"] is available
    finally:
        client.close()
        assert runtime.close()["complete"]


def test_status_explains_missing_source(tmp_path, monkeypatch):
    client, _runtime, _upstream = _wired(tmp_path, monkeypatch, source="")
    payload = client.get("/api/market/status").json()
    assert payload["reachable"] is False
    assert payload["configured"] is False
    assert "设置" in payload["error"]
    assert payload["totals"] == {"plugins": 0, "personas": 0}


def test_status_probes_health_and_reports_totals(tmp_path, monkeypatch):
    client, _runtime, upstream = _wired(tmp_path, monkeypatch)
    payload = client.get("/api/market/status").json()
    assert payload["reachable"] is True
    assert payload["totals"] == {"plugins": 3, "personas": 6}
    assert payload["server_url"] == "http://market.test"
    assert "/api/health" in upstream.paths


def test_source_save_validates_and_persists(tmp_path, monkeypatch):
    client, runtime, _upstream = _wired(tmp_path, monkeypatch)
    bad = client.put("/api/market/source", json={"server_url": "ftp://mirror"})
    assert bad.status_code == 400 and "http" in bad.json()["detail"]
    ok = client.put("/api/market/source", json={"server_url": "https://mirror.example"})
    assert ok.status_code == 200
    assert runtime.community.effective_server_url() == "https://mirror.example"


def test_personas_are_normalized_and_bad_cards_dropped(tmp_path, monkeypatch):
    client, _runtime, _upstream = _wired(tmp_path, monkeypatch)
    payload = client.get("/api/market/personas", params={"query": "小"}).json()
    assert [card["id"] for card in payload["items"]] == ["xiaoyu"]
    assert payload["total"] == 2
    card = payload["items"][0]
    assert card["system_prompt"] == "你是小鱼" and card["version"] == "1.0"

    detail = client.get("/api/market/personas/xiaoyu").json()
    assert detail["name"] == "小鱼" and detail["sha256"] == "b" * 64

    broken = client.get("/api/market/personas/broken")
    assert broken.status_code == 422 and "必要字段" in broken.json()["detail"]


def test_plugins_are_clipped(tmp_path, monkeypatch):
    client, _runtime, _upstream = _wired(tmp_path, monkeypatch)
    item = client.get("/api/market/plugins").json()["items"][0]
    assert item["id"] == "demo-tools"
    assert len(item["description"]) == 300
    assert item["homepage"] == ""  # 非 http(s) 丢弃
    assert item["tags"] == ["工具", "演示", "t" * 12]  # 去重 + 单条截断
    assert [entry["path"] for entry in item["files"]] == ["manifest.json", "../evil"]

    detail = client.get("/api/market/plugins/demo-tools").json()
    assert detail["version"] == "1.0.0" and detail["sha256"] == "a" * 64

    missing = client.get("/api/market/plugins/nope")
    assert missing.status_code == 502 and "404" in missing.json()["detail"]


def test_upstream_failure_is_502_not_a_traceback(tmp_path, monkeypatch):
    client, _runtime, _upstream = _wired(tmp_path, monkeypatch, fail="boom")
    for path in ("/api/market/plugins", "/api/market/personas", "/api/market/plugins/demo-tools"):
        response = client.get(path)
        assert response.status_code == 502
        assert "维护" in response.json()["detail"]


def test_install_quarantines_without_registering_tools(tmp_path, monkeypatch):
    client, runtime, upstream = _wired(tmp_path, monkeypatch)
    response = client.post("/api/market/plugins/demo-tools/install", json={"version": ""})
    assert response.status_code == 200, response.text
    state = response.json()
    assert [item["id"] for item in state["quarantine"]] == ["demo-tools"]
    assert state["installed"] == []
    assert runtime.registry.get("demo-tools-hello") is None  # 装了不等于跑
    assert "/api/download/demo-tools" in upstream.paths

    # 收件箱只做传输落点：装完不留下同一个 zip
    assert state["inbox"] == []

    activated = client.post("/api/market/quarantine/demo-tools/activate")
    assert activated.status_code == 200, activated.text
    after = activated.json()
    assert after["quarantine"] == []
    assert [item["id"] for item in after["installed"]] == ["demo-tools"]
    assert runtime.registry.get("demo-tools-hello") is not None
    assert "hello market" in runtime.registry.dispatch("demo-tools-hello", {})


def test_duplicate_install_is_409(tmp_path, monkeypatch):
    client, _runtime, _upstream = _wired(tmp_path, monkeypatch)
    assert client.post("/api/market/plugins/demo-tools/install").status_code == 200
    again = client.post("/api/market/plugins/demo-tools/install")
    assert again.status_code >= 400
    assert "demo-tools" in again.json()["detail"]


def test_discard_drops_only_the_quarantined_package(tmp_path, monkeypatch):
    client, runtime, _upstream = _wired(tmp_path, monkeypatch)
    client.post("/api/market/plugins/demo-tools/install")
    dropped = client.post("/api/market/quarantine/demo-tools/discard")
    assert dropped.status_code == 200
    assert dropped.json()["quarantine"] == []
    assert runtime.registry.get("demo-tools-hello") is None
    assert not (runtime.plugins._quarantine / "demo-tools").exists()


def test_enable_disable_remove_installed_plugin(tmp_path, monkeypatch):
    client, runtime, _upstream = _wired(tmp_path, monkeypatch)
    client.post("/api/market/plugins/demo-tools/install")
    client.post("/api/market/quarantine/demo-tools/activate")

    off = client.post("/api/market/plugins/demo-tools/disable").json()
    assert off["installed"][0]["enabled"] is False
    assert runtime.registry.get("demo-tools-hello") is None

    on = client.post("/api/market/plugins/demo-tools/enable").json()
    assert on["installed"][0]["enabled"] is True

    gone = client.delete("/api/market/plugins/demo-tools")
    assert gone.status_code == 200
    assert gone.json()["installed"] == []


@pytest.fixture(autouse=True)
def _no_real_market_source(monkeypatch):
    """任何用例都不得因为本机环境里的 OMNI_MARKET_URL 而连到真实服务器。"""
    monkeypatch.delenv("OMNI_MARKET_URL", raising=False)
