"""数字具身总览与B站账号接口：只读聚合的诚实性（不联网也能说清就绪状态）。

注意：bili-account 会真的外呼 B站 nav 接口——测试里用 monkeypatch 换成假实现，
默认套件保持离线。
"""

from __future__ import annotations

import json

import pytest
from test_web import _client

from omni.tools.bilibili import api as bili_api


@pytest.fixture
def workbench(tmp_path):
    client, runtime = _client(tmp_path)
    try:
        yield client, runtime
    finally:
        assert runtime.close()["complete"]
        client.close()


def test_embodiments_lists_four_embodiments(workbench):
    client, _runtime = workbench
    response = client.get("/api/system/embodiments")
    assert response.status_code == 200
    rows = response.json()["embodiments"]
    assert [row["id"] for row in rows] == ["bilibili", "browser", "android", "qq"]
    for row in rows:
        assert row["label"] and row["section"].startswith("sec-")
        assert row["detail"]
        assert isinstance(row["ready"], bool)
        assert row["capabilities"]


def test_embodiment_bilibili_reports_missing_credentials(workbench):
    client, runtime = workbench
    rows = client.get("/api/system/embodiments").json()["embodiments"]
    bili = next(row for row in rows if row["id"] == "bilibili")
    assert bili["ready"] is False
    assert "未配置登录凭证" in bili["detail"]
    assert bili["extras"]["has_sessdata"] is False
    assert runtime.paths.cookies.exists() is False


def test_embodiment_bilibili_ready_when_cookies_present(workbench):
    client, runtime = workbench
    runtime.paths.cookies.write_text(json.dumps({"SESSDATA": "sess", "bili_jct": ""}), encoding="utf-8")
    rows = client.get("/api/system/embodiments").json()["embodiments"]
    bili = next(row for row in rows if row["id"] == "bilibili")
    assert bili["ready"] is True
    assert bili["extras"]["has_sessdata"] is True


def test_embodiment_never_echoes_cookie_values(workbench):
    client, runtime = workbench
    runtime.paths.cookies.write_text(json.dumps({"SESSDATA": "super-secret-value"}), encoding="utf-8")
    body = client.get("/api/system/embodiments").text
    assert "super-secret-value" not in body


def test_bili_account_reports_login_state(workbench, monkeypatch):
    client, _runtime = workbench

    class FakeAPI:
        def __init__(self, *args, **kwargs):
            pass

        def account(self):
            return {
                "logged_in": True,
                "uid": "10086",
                "name": "小鱼本鱼",
                "level": 5,
                "coins": 42,
                "vip": False,
                "face": "",
            }

        def close(self):
            pass

    monkeypatch.setattr(bili_api, "BiliAPI", FakeAPI)
    body = client.get("/api/system/bili-account").json()
    assert body["checked"] is True
    assert body["logged_in"] is True
    assert body["account"]["uid"] == "10086"


def test_bili_account_reports_failure_without_faking_login(workbench, monkeypatch):
    client, _runtime = workbench

    class FailingAPI:
        def __init__(self, *args, **kwargs):
            pass

        def account(self):
            raise bili_api.BiliAPIError("B站接口 /x/web-interface/nav 返回错误 code=-101")

        def close(self):
            pass

    monkeypatch.setattr(bili_api, "BiliAPI", FailingAPI)
    body = client.get("/api/system/bili-account").json()
    assert body["logged_in"] is False
    assert "-101" in body["error"]


def test_bili_account_requires_pairing(tmp_path):
    client, runtime = _client(tmp_path, pair=False)
    try:
        assert client.get("/api/system/bili-account").status_code in (401, 403)
    finally:
        assert runtime.close()["complete"]
        client.close()
