"""领域路由的兼容契约、应用隔离和统一鉴权，全部使用临时运行态。"""

from __future__ import annotations

import json
from pathlib import Path

from fastapi.testclient import TestClient
import pytest

from omni.bootstrap import build_runtime
from omni.channels import web
from omni.channels.web_api import schemas


@pytest.fixture
def application(tmp_path):
    config = tmp_path / "config.yaml"
    config.write_text("persona:\n  name: 领域路由测试\n", encoding="utf-8")
    runtime = build_runtime(
        data_dir=tmp_path / "data",
        db_path=tmp_path / "memory.db",
        config_path=config,
        skills_root=tmp_path / "skills",
        start_background_workers=False,
    )
    with TestClient(web.create_app(runtime), base_url="http://127.0.0.1") as client:
        yield client, runtime


def pair(client):
    code = client.app.state.web_auth.pairing_code
    assert client.post("/api/auth/pair", json={"code": code}).status_code == 200


def test_published_http_contract_is_preserved(application):
    client, _ = application
    frozen = json.loads((Path(__file__).parent / "fixtures/web_api_v1.json").read_text(encoding="utf-8"))
    current = client.get("/openapi.json").json()
    operations = {}
    for path, methods in current["paths"].items():
        for method, value in methods.items():
            if method.upper() not in {"GET", "POST", "PUT", "PATCH", "DELETE", "HEAD", "OPTIONS"}:
                continue
            operations[method.upper() + " " + path] = {
                key: value[key] for key in ("operationId", "parameters", "requestBody", "responses") if key in value
            }
    # 新增接口允许；既有路径、方法、请求/响应、校验模型与 operationId 必须保持。
    for identity, expected in frozen["operations"].items():
        assert operations.get(identity) == expected, identity
    for name, expected in frozen["schemas"].items():
        assert current["components"]["schemas"].get(name) == expected, name


@pytest.mark.parametrize(
    "method,path",
    [
        ("GET", "/api/state"),
        ("POST", "/api/chat"),
        ("GET", "/api/runs"),
        ("GET", "/api/profile"),
        ("GET", "/api/skills/pending"),
        ("GET", "/api/artifacts"),
        ("GET", "/api/approvals"),
        ("GET", "/api/mcp/overview"),
        ("GET", "/api/system/doctor"),
        ("GET", "/api/system/cron"),
        ("GET", "/api/publications"),
        ("GET", "/api/desktop/windows"),
    ],
)
def test_every_domain_router_inherits_pairing_and_origin_checks(application, method, path):
    client, runtime = application
    assert client.request(method, path, json={}).status_code == 401
    pair(client)
    assert client.request(method, path, json={}, headers={"Host": "evil.example"}).status_code == 403
    if method not in {"GET", "HEAD", "OPTIONS"}:
        assert (
            client.request(
                method, path, json={}, headers={"Origin": "https://evil.example", "Sec-Fetch-Site": "cross-site"}
            ).status_code
            == 403
        )
    # 这些请求不应创建运行或触发领域动作；模型传输仍被默认测试闸门阻断。
    assert runtime.active_runs.snapshot() == []


def test_domain_factories_keep_application_runtime_isolated(application, tmp_path):
    first, first_runtime = application
    pair(first)
    config = tmp_path / "other.yaml"
    config.write_text("persona:\n  name: 另一个运行态\n", encoding="utf-8")
    other = build_runtime(
        data_dir=tmp_path / "other-data",
        db_path=tmp_path / "other.db",
        config_path=config,
        skills_root=tmp_path / "other-skills",
        start_background_workers=False,
    )
    with TestClient(web.create_app(other), base_url="http://127.0.0.1") as second:
        pair(second)
        original = second.get("/api/sessions").json()["sessions"]
        first.post("/api/sessions")
        assert len(first.get("/api/sessions").json()["sessions"]) == len(original) + 1
        assert second.get("/api/sessions").json()["sessions"] == original
        assert first.get("/api/state").json()["persona"] == first_runtime.config.persona.name
        assert second.get("/api/state").json()["persona"] == "另一个运行态"


def test_legacy_web_request_models_are_explicit_aliases():
    for name in ("ChatIn", "ResolveIn", "CronCreateIn", "CronUpdateIn", "AndroidTapIn", "SkillSaveIn", "PairIn"):
        assert getattr(web, name) is getattr(schemas, name)


def test_static_skill_routes_keep_priority_over_name_route(application):
    client, runtime = application
    pair(client)
    body = "# 路由测试\n\n## When to Use\n检查静态路由。\n\n## Procedure\n读取待审阅列表。\n\n## Pitfalls\n不可当作技能名。\n\n## Verification\n结果含 items 与 count。"
    ok, message = runtime.skills.create("pending", "标题", body)
    assert ok, message
    result = client.get("/api/skills/pending").json()
    assert set(result) == {"items", "count"}
    assert any(item["name"] == "pending" for item in result["items"])
