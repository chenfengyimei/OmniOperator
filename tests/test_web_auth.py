"""工作台身份与 CSRF 单测（D03：A07）。

判据：未配对/跨站/过期会话一律拿不到数据、改不了配置、启不了服务、开不了终端；
拒绝时真实副作用为零（配置文件字节不变、服务管理器没被调用）。
"""
from __future__ import annotations

import time

import pytest
from fastapi.testclient import TestClient
from starlette.websockets import WebSocketDisconnect

from omni.bootstrap import build_runtime
from omni.channels.web import _validate_bind_host, create_app
from omni.channels.web_auth import COOKIE_NAME, WebAuth
from omni.safety.gates import DenyApprover

SENSITIVE_GETS = (
    "/api/state",
    "/api/sessions",
    "/api/facts",
    "/api/profile",
    "/api/resident",
    "/api/reviews",
    "/api/skills",
    "/api/approvals",
    "/api/system/overview",
    "/api/system/config",
    "/api/system/cron",
    "/api/android/state",
)


def _app(tmp_path, config_text="persona:\n  name: 测试小鱼\n"):
    config_path = tmp_path / "config.yaml"
    config_path.write_text(config_text, encoding="utf-8")
    runtime = build_runtime(
        approver=DenyApprover(),
        db_path=tmp_path / "m.db",
        config_path=config_path,
        data_dir=tmp_path / "data",
    )
    app = create_app(runtime)
    return app, runtime


def _paired(tmp_path, config_text=None):
    kwargs = {} if config_text is None else {"config_text": config_text}
    app, runtime = _app(tmp_path, **kwargs)
    client = TestClient(app, base_url="http://127.0.0.1")
    code = app.state.web_auth.pairing_code
    assert client.post("/api/auth/pair", json={"code": code}).status_code == 200
    return client, runtime, app


# ---------------------------------------------------------------- 未配对


@pytest.mark.parametrize("path", SENSITIVE_GETS)
def test_unpaired_cannot_read_any_api(path, tmp_path):
    app, _runtime = _app(tmp_path)
    client = TestClient(app, base_url="http://127.0.0.1")
    response = client.get(path)
    assert response.status_code == 401
    assert response.json()["code"] == "auth_required"


def test_unpaired_cannot_change_config_or_services(tmp_path, monkeypatch):
    app, runtime = _app(tmp_path)
    config_path = runtime.paths.config  # 注入路径：与 bootstrap 读取的是同一个文件
    client = TestClient(app, base_url="http://127.0.0.1")

    calls: list[str] = []
    monkeypatch.setattr(
        runtime.services, "start", lambda name, qq="": calls.append(f"start:{name}") or {}
    )
    monkeypatch.setattr(runtime.services, "stop", lambda name: calls.append(f"stop:{name}") or {})

    before = config_path.read_bytes()
    assert client.post(
        "/api/system/config", json={"updates": {"persona": {"name": "被改了"}}}
    ).status_code == 401
    assert client.post("/api/system/services/omni-qq/start", json={}).status_code == 401
    assert client.post("/api/system/services/omni-qq/stop").status_code == 401
    assert client.post("/api/approvals/abc/resolve", json={"allowed": True}).status_code == 401
    assert client.post("/api/chat", json={"message": "hi"}).status_code == 401
    assert config_path.read_bytes() == before
    assert calls == []


def test_unpaired_terminal_websocket_is_rejected(tmp_path):
    app, _runtime = _app(tmp_path)
    client = TestClient(app, base_url="http://127.0.0.1")
    with pytest.raises(WebSocketDisconnect):
        with client.websocket_connect("/api/terminal/ws"):
            pass


def test_auth_status_is_public_but_reveals_nothing(tmp_path):
    app, _runtime = _app(tmp_path)
    client = TestClient(app, base_url="http://127.0.0.1")
    payload = client.get("/api/auth/status").json()
    assert payload == {"auth_required": True, "paired": False, "session_days": 30}


# ---------------------------------------------------------------- 配对


def test_wrong_pairing_code_is_rejected_without_cookie(tmp_path):
    app, _runtime = _app(tmp_path)
    client = TestClient(app, base_url="http://127.0.0.1")
    response = client.post("/api/auth/pair", json={"code": "00000000"})
    assert response.status_code == 403
    assert response.json()["code"] == "pair_rejected"
    assert COOKIE_NAME not in response.cookies
    assert client.get("/api/state").status_code == 401


def test_cross_site_pairing_is_rejected(tmp_path):
    app, _runtime = _app(tmp_path)
    code = app.state.web_auth.pairing_code
    client = TestClient(app, base_url="http://127.0.0.1")
    response = client.post(
        "/api/auth/pair",
        json={"code": code},
        headers={"origin": "https://evil.example"},
    )
    assert response.status_code == 403
    # 中间件的 CSRF 闸门在路由之前就拦下（路由里还有一层 origin 校验兜底）
    assert response.json()["code"] in {"csrf_rejected", "origin_rejected"}
    assert client.get("/api/state").status_code == 401


def test_pairing_is_locked_after_repeated_failures(tmp_path):
    app, _runtime = _app(tmp_path)
    auth = app.state.web_auth
    old_code = auth.pairing_code
    client = TestClient(app, base_url="http://127.0.0.1")
    for _ in range(5):
        client.post("/api/auth/pair", json={"code": "11111111"})
    assert auth.pairing_code != old_code, "连续失败后必须更换配对码"
    # 旧码失效、新码可用
    assert client.post("/api/auth/pair", json={"code": old_code}).status_code == 403
    assert client.post("/api/auth/pair", json={"code": auth.pairing_code}).status_code == 200


def test_paired_client_can_read_and_session_cookie_is_httponly(tmp_path):
    client, _runtime, _app_ = _paired(tmp_path)
    assert client.get("/api/state").status_code == 200
    assert client.get("/api/auth/status").json()["paired"] is True

    second = tmp_path / "second"
    second.mkdir()
    app, _rt = _app(second)
    fresh = TestClient(app, base_url="http://127.0.0.1")
    response = fresh.post("/api/auth/pair", json={"code": app.state.web_auth.pairing_code})
    cookie_header = response.headers.get("set-cookie", "").lower()
    assert "httponly" in cookie_header and "samesite=strict" in cookie_header


# ---------------------------------------------------------------- Host / Origin / CSRF


def test_foreign_host_header_is_rejected(tmp_path):
    app, _runtime = _app(tmp_path)
    # 模拟 DNS rebinding：恶意域名解析到 127.0.0.1，Host 仍是外部域名
    client = TestClient(app, base_url="http://evil.example")
    response = client.get("/api/auth/status")
    assert response.status_code == 403
    assert response.json()["code"] == "host_rejected"


def test_cross_site_post_is_rejected_and_has_no_side_effect(tmp_path):
    client, runtime, _app_ = _paired(tmp_path)
    config_path = runtime.paths.config
    before = config_path.read_bytes()

    for headers in (
        {"origin": "https://evil.example"},
        {"sec-fetch-site": "cross-site"},
        {"origin": "null"},
    ):
        response = client.post(
            "/api/system/config",
            json={"updates": {"persona": {"name": "被跨站改了"}}},
            headers=headers,
        )
        assert response.status_code == 403
        assert response.json()["code"] == "csrf_rejected"
    assert config_path.read_bytes() == before, "跨站请求产生了副作用"


def test_same_origin_post_is_allowed(tmp_path):
    client, runtime, _app_ = _paired(tmp_path)
    config_path = runtime.paths.config
    response = client.post(
        "/api/system/config",
        json={"updates": {"persona": {"name": "本地改名"}}},
        headers={"origin": "http://127.0.0.1", "sec-fetch-site": "same-origin"},
    )
    assert response.status_code == 200
    assert "本地改名" in config_path.read_text(encoding="utf-8")


def test_expired_or_tampered_session_is_rejected(tmp_path):
    app, _runtime = _app(tmp_path)
    auth: WebAuth = app.state.web_auth
    client = TestClient(app, base_url="http://127.0.0.1")

    expired_payload = f"deadbeef.{int(time.time()) - 10}"
    expired = f"{expired_payload}.{auth._sign(expired_payload)}"
    assert auth.verify_session(expired) is False
    client.cookies.set(COOKIE_NAME, expired, path="/")
    assert client.get("/api/state").status_code == 401

    client.cookies.set(COOKIE_NAME, "deadbeef.9999999999.0123456789abcdef", path="/")
    assert client.get("/api/state").status_code == 401


def test_logout_clears_session(tmp_path):
    client, _runtime, _app_ = _paired(tmp_path)
    assert client.get("/api/state").status_code == 200
    assert client.post("/api/auth/logout").status_code == 200
    client.cookies.delete(COOKIE_NAME)
    assert client.get("/api/state").status_code == 401


# ---------------------------------------------------------------- 密钥不回显


def test_access_token_is_never_echoed(tmp_path):
    client, runtime, _app_ = _paired(
        tmp_path, config_text='persona:\n  name: 测试小鱼\nqq:\n  access_token: "s3cret-token"\n'
    )
    config_path = runtime.paths.config

    payload = client.get("/api/system/config").json()
    assert "s3cret-token" not in str(payload)
    assert payload["qq"]["access_token"] == ""
    assert payload["qq"]["access_token_configured"] is True

    # 空串 = 保留旧值（前端拿不到明文，空值不能当成"清空"）
    assert client.post(
        "/api/system/config", json={"updates": {"qq": {"access_token": ""}}}
    ).status_code == 200
    assert "s3cret-token" in config_path.read_text(encoding="utf-8")

    # null = 显式清除
    assert client.post(
        "/api/system/config", json={"updates": {"qq": {"access_token": None}}}
    ).status_code == 200
    assert "s3cret-token" not in config_path.read_text(encoding="utf-8")

    # 非空 = 替换
    assert client.post(
        "/api/system/config", json={"updates": {"qq": {"access_token": "new-token"}}}
    ).status_code == 200
    assert "new-token" in config_path.read_text(encoding="utf-8")


def test_secret_error_message_does_not_echo_value(tmp_path):
    client, _runtime, _app_ = _paired(tmp_path)
    # 非字符串密钥值 → 被拒，但错误信息里不能出现该值
    response = client.post(
        "/api/system/config", json={"updates": {"qq": {"access_token": 12345678}}}
    )
    assert response.status_code == 400
    assert "12345678" not in response.text


# ---------------------------------------------------------------- 监听地址


def test_non_loopback_bind_is_refused_by_default(monkeypatch):
    monkeypatch.delenv("OMNI_WEB_ALLOW_REMOTE", raising=False)
    with pytest.raises(SystemExit) as excinfo:
        _validate_bind_host("0.0.0.0")
    assert "非回环" in str(excinfo.value)
    assert _validate_bind_host("127.0.0.1") == "127.0.0.1"

    monkeypatch.setenv("OMNI_WEB_ALLOW_REMOTE", "1")
    assert _validate_bind_host("0.0.0.0") == "0.0.0.0"


# ---------------------------------------------------------------- 控制台配对链接


def test_pairing_link_sets_cookie_and_redirects(tmp_path):
    """控制台链接点开即配对：浏览器正常打开工作台，前端不需要配合。"""
    app, _runtime = _app(tmp_path)
    auth = app.state.web_auth
    client = TestClient(app, base_url="http://127.0.0.1")

    response = client.get(f"/pair/{auth.pairing_code}", follow_redirects=False)
    assert response.status_code == 303
    assert response.headers["location"] == "/"
    assert COOKIE_NAME in response.cookies
    assert client.get("/api/state").status_code == 200


def test_pairing_link_rejects_cross_site_navigation(tmp_path):
    app, _runtime = _app(tmp_path)
    auth = app.state.web_auth
    client = TestClient(app, base_url="http://127.0.0.1")

    response = client.get(
        f"/pair/{auth.pairing_code}",
        headers={"sec-fetch-site": "cross-site"},
        follow_redirects=False,
    )
    assert response.status_code == 403
    assert client.get("/api/state").status_code == 401


def test_expired_pairing_code_rotates_and_reports(tmp_path, capsys):
    app, _runtime = _app(tmp_path)
    auth = app.state.web_auth
    old_code = auth.pairing_code
    auth._pair_deadline = 0  # 模拟过期

    client = TestClient(app, base_url="http://127.0.0.1")
    assert client.post("/api/auth/pair", json={"code": old_code}).status_code == 403
    assert auth.pairing_code != old_code
    assert "配对链接" in capsys.readouterr().out  # 新链接打到控制台
    assert client.post("/api/auth/pair", json={"code": auth.pairing_code}).status_code == 200


# ---------------------------------------------------------------- 控制台配对链接


def test_pairing_link_sets_cookie_and_redirects(tmp_path):
    """控制台链接点开即配对：浏览器正常打开工作台，前端不需要配合。"""
    app, _runtime = _app(tmp_path)
    auth = app.state.web_auth
    client = TestClient(app, base_url="http://127.0.0.1")

    response = client.get(f"/pair/{auth.pairing_code}", follow_redirects=False)
    assert response.status_code == 303
    assert response.headers["location"] == "/"
    assert COOKIE_NAME in response.cookies
    assert client.get("/api/state").status_code == 200


def test_pairing_link_rejects_cross_site_navigation(tmp_path):
    app, _runtime = _app(tmp_path)
    auth = app.state.web_auth
    client = TestClient(app, base_url="http://127.0.0.1")

    response = client.get(
        f"/pair/{auth.pairing_code}",
        headers={"sec-fetch-site": "cross-site"},
        follow_redirects=False,
    )
    assert response.status_code == 403
    assert client.get("/api/state").status_code == 401


def test_expired_pairing_code_rotates_and_reports(tmp_path, capsys):
    app, _runtime = _app(tmp_path)
    auth = app.state.web_auth
    old_code = auth.pairing_code
    auth._pair_deadline = 0  # 模拟过期

    client = TestClient(app, base_url="http://127.0.0.1")
    assert client.post("/api/auth/pair", json={"code": old_code}).status_code == 403
    assert auth.pairing_code != old_code
    assert "配对链接" in capsys.readouterr().out  # 新链接打到控制台
    assert client.post("/api/auth/pair", json={"code": auth.pairing_code}).status_code == 200


def test_websocket_gate_matrix(tmp_path):
    """WS 握手闸门：Host/Origin/会话三者都过才放行（真实 PTY 留待手动验收）。"""
    app, _runtime = _app(tmp_path)
    auth = app.state.web_auth
    session = auth.issue_session()

    ok = auth.check_websocket(host="127.0.0.1:8000", origin="http://127.0.0.1:8000", cookie=session)
    assert ok is None
    assert auth.check_websocket(host="127.0.0.1:8000", origin=None, cookie=session) is None

    assert auth.check_websocket(host="evil.example:8000", origin=None, cookie=session).code == "host_rejected"
    assert auth.check_websocket(
        host="127.0.0.1:8000", origin="https://evil.example", cookie=session
    ).code == "origin_rejected"
    assert auth.check_websocket(host="127.0.0.1:8000", origin=None, cookie=None).code == "auth_required"


def test_websocket_gate_matrix(tmp_path):
    """WS 握手闸门：Host/Origin/会话三者都过才放行（真实 PTY 留待手动验收）。"""
    app, _runtime = _app(tmp_path)
    auth = app.state.web_auth
    session = auth.issue_session()

    ok = auth.check_websocket(host="127.0.0.1:8000", origin="http://127.0.0.1:8000", cookie=session)
    assert ok is None
    assert auth.check_websocket(host="127.0.0.1:8000", origin=None, cookie=session) is None

    assert auth.check_websocket(host="evil.example:8000", origin=None, cookie=session).code == "host_rejected"
    assert auth.check_websocket(
        host="127.0.0.1:8000", origin="https://evil.example", cookie=session
    ).code == "origin_rejected"
    assert auth.check_websocket(host="127.0.0.1:8000", origin=None, cookie=None).code == "auth_required"
