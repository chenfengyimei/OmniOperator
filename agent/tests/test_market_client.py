"""社区读侧契约：索引/详情只有一个 HTTP 出口，返回结构化数据；人设卡逐字段收窄。

守的几条：
1. `fetch` 是唯一出口——工具文案（search/info）与工作台接口都走它，
   错误统一映射成中文文案（HTTP 错误 / 非 JSON / 不是对象）；
2. id 只接受扁平字符，带斜杠或空的 id 在发请求前就拒绝（不拼进 URL 路径）；
3. 生效源 = 显式配置 > `OMNI_MARKET_URL` > 未配置（不硬编码默认主机）；
4. 人设卡：缺 id/name/system_prompt 判无效，超长字段截断，tags 去重限量，
   非 http(s) 主页丢弃——坏数据只丢那一条，不影响整页。

这里全部用 httpx.MockTransport，不连网、不碰真机社区。
"""

from __future__ import annotations

import json

import httpx

from omni.plugins import PluginHost
from omni.plugins.community import CommunityClient
from omni.plugins.community.persona import MAX_NAME, MAX_PROMPT, normalize, normalize_many
from omni.tools.registry import ToolRegistry


def _client(tmp_path, handler, *, server="http://market.test"):
    host = PluginHost(tmp_path / "plugins", ToolRegistry())
    client = CommunityClient(
        tmp_path / "plugins" / "community.json",
        host,
        transport=httpx.MockTransport(handler),
    )
    assert client.configure(server_url=server)[0]
    return client


def _ok(payload) -> httpx.Response:
    return httpx.Response(200, json=payload)


def test_index_and_detail_share_one_exit(tmp_path):
    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path == "/api/plugins":
            assert request.url.params["query"] == "json"
            return _ok({"total": 1, "items": [{"id": "json-tools", "latest_version": "1.0.0"}]})
        if request.url.path == "/api/plugins/json-tools":
            return _ok(
                {
                    "id": "json-tools",
                    "latest_version": "1.0.0",
                    "description": "格式化 JSON",
                    "versions": ["1.0.0"],
                    "latest_sha256": "a" * 64,
                    "latest_size": 3030,
                }
            )
        return httpx.Response(404, json={"detail": "没有这个接口"})

    client = _client(tmp_path, handler)
    data, problem = client.plugin_list("json", 5)
    assert problem == "" and data["total"] == 1
    detail, problem = client.plugin_detail("json-tools")
    assert problem == "" and detail["latest_size"] == 3030

    # 工具文案复用同一出口，措辞保持不变（工具层不认识 HTTP）
    ok, message = client.search("json")
    assert ok and "json-tools v1.0.0" in message
    ok, message = client.info("json-tools")
    assert ok and "sha256 aaaaaaaaaaaaaaaa" in message


def test_upstream_failures_are_mapped(tmp_path):
    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path.endswith("/missing"):
            return httpx.Response(404, json={"detail": "没有这个条目"})
        if request.url.path.endswith("/broken"):
            return httpx.Response(200, text="<html>not json</html>")
        return _ok(["不是对象"])

    client = _client(tmp_path, handler)
    data, problem = client.fetch("/api/plugins/missing")
    assert data is None and "404" in problem and "没有这个条目" in problem
    data, problem = client.fetch("/api/plugins/broken")
    assert data is None and "不是 JSON" in problem
    data, problem = client.fetch("/api/plugins/listy")
    assert data is None and "数据结构不对" in problem

    ok, message = client.info("missing")
    assert not ok and "404" in message


def test_ids_are_rejected_before_building_a_path(tmp_path):
    def handler(request: httpx.Request) -> httpx.Response:  # pragma: no cover - 不该被调用
        raise AssertionError(f"不该发请求：{request.url}")

    client = _client(tmp_path, handler)
    for bad in ("", "   ", "a/b", "../etc/passwd"):
        data, problem = client.plugin_detail(bad)
        assert data is None and "不能为空或含斜杠" in problem
        data, problem = client.persona_detail(bad)
        assert data is None and "不能为空或含斜杠" in problem


def test_effective_source_prefers_config_then_env(tmp_path, monkeypatch):
    def handler(request: httpx.Request) -> httpx.Response:  # pragma: no cover
        return _ok({"total": 0, "items": []})

    client = _client(tmp_path, handler)
    monkeypatch.delenv("OMNI_MARKET_URL", raising=False)
    assert client.configure(server_url="")[0]
    assert client.effective_server_url() == ""
    data, problem = client.plugin_list()
    assert data is None and "未配置" in problem

    monkeypatch.setenv("OMNI_MARKET_URL", " http://env.test ")
    assert client.effective_server_url() == "http://env.test"
    data, problem = client.plugin_list()
    assert problem == "" and data["total"] == 0
    assert client.configure(server_url="http://file.test")[0]
    assert client.effective_server_url() == "http://file.test"


def test_persona_card_trims_and_drops_bad_fields():
    card = normalize(
        {
            "id": "xiaoyu",
            "name": "  小鱼  ",
            "latest_version": "1.0",
            "description": "d" * 500,
            "system_prompt": "p" * (MAX_PROMPT + 100),
            "greeting": "g" * 500,
            "tags": ["默认", "默认", "", "x" * 40] + [f"t{i}" for i in range(10)],
            "author": "official",
            "homepage": "javascript:alert(1)",
            "latest_sha256": "b" * 64,
        }
    )
    assert card["name"] == "小鱼"
    assert card["version"] == "1.0"
    assert len(card["description"]) == 200
    assert len(card["system_prompt"]) == MAX_PROMPT
    assert len(card["greeting"]) == 120
    assert card["tags"][:2] == ["默认", "x" * 12]
    assert len(card["tags"]) == 6
    assert card["homepage"] == ""  # 非 http(s) 一律丢弃
    assert card["sha256"] == "b" * 64


def test_persona_card_requires_id_name_and_prompt():
    assert normalize({"id": "a", "name": "n"}) is None  # 缺提示词
    assert normalize({"id": "a", "system_prompt": "p"}) is None  # 缺名字
    assert normalize({"name": "n", "system_prompt": "p"}) is None  # 缺 id
    assert normalize({"id": "Bad Id", "name": "n", "system_prompt": "p"}) is None
    assert normalize("字符串不是卡") is None
    assert normalize({"id": "a", "name": "n" * (MAX_NAME + 5), "system_prompt": "p"})["name"] == "n" * MAX_NAME


def test_persona_list_drops_only_the_bad_entry(tmp_path):
    payload = {
        "total": 3,
        "items": [
            {"id": "xiaoyu", "name": "小鱼", "system_prompt": "你是小鱼"},
            {"id": "../evil", "name": "坏的", "system_prompt": "x"},
            {"id": "dafeiyu", "name": "大肥鱼", "system_prompt": "你是大肥鱼"},
        ],
    }

    def handler(request: httpx.Request) -> httpx.Response:
        assert request.url.path == "/api/personas"
        return _ok(json.loads(json.dumps(payload)))

    client = _client(tmp_path, handler)
    data, problem = client.persona_list()
    assert problem == ""
    cards = normalize_many(data.get("items"))
    assert [c["id"] for c in cards] == ["xiaoyu", "dafeiyu"]
