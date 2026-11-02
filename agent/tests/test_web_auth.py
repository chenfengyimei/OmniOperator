"""工作台身份与 CSRF 单测（D03：A07）。

判据：未配对/跨站/过期会话一律拿不到数据、改不了配置、启不了服务、开不了终端；
拒绝时真实副作用为零（配置文件字节不变、服务管理器没被调用）。
"""

from __future__ import annotations

import json
import time

import pytest
from fastapi.testclient import TestClient
from starlette.websockets import WebSocketDisconnect

from omni.bootstrap import build_runtime
from omni.channels.web import _validate_bind_host, create_app
from omni.channels.web_auth import COOKIE_NAME, PAIR_TTL_S, WebAuth
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
    "/api/llm/usage",
    "/api/facts/detail",
    "/api/memory/status",
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
    monkeypatch.setattr(runtime.services, "start", lambda name, qq="": calls.append(f"start:{name}") or {})
    monkeypatch.setattr(runtime.services, "stop", lambda name: calls.append(f"stop:{name}") or {})

    before = config_path.read_bytes()
    assert client.post("/api/system/config", json={"updates": {"persona": {"name": "被改了"}}}).status_code == 401
    assert client.post("/api/system/services/omni-qq/start", json={}).status_code == 401
    assert client.post("/api/system/services/omni-qq/stop").status_code == 401
    assert client.post("/api/approvals/abc/resolve", json={"allowed": True}).status_code == 401
    assert client.post("/api/chat", json={"message": "hi"}).status_code == 401
    assert client.post("/api/chat/stream", json={"message": "hi"}).status_code == 401
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
    assert client.post("/api/system/config", json={"updates": {"qq": {"access_token": ""}}}).status_code == 200
    assert "s3cret-token" in config_path.read_text(encoding="utf-8")

    # null = 显式清除
    assert client.post("/api/system/config", json={"updates": {"qq": {"access_token": None}}}).status_code == 200
    assert "s3cret-token" not in config_path.read_text(encoding="utf-8")

    # 非空 = 替换
    assert client.post("/api/system/config", json={"updates": {"qq": {"access_token": "new-token"}}}).status_code == 200
    assert "new-token" in config_path.read_text(encoding="utf-8")


def test_secret_error_message_does_not_echo_value(tmp_path):
    client, _runtime, _app_ = _paired(tmp_path)
    # 非字符串密钥值 → 被拒，但错误信息里不能出现该值
    response = client.post("/api/system/config", json={"updates": {"qq": {"access_token": 12345678}}})
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


def test_websocket_gate_matrix(tmp_path):
    """WS 握手闸门：Host/Origin/会话三者都过才放行（真实 PTY 留待手动验收）。"""
    app, _runtime = _app(tmp_path)
    auth = app.state.web_auth
    session = auth.issue_session()

    ok = auth.check_websocket(host="127.0.0.1:8000", origin="http://127.0.0.1:8000", cookie=session)
    assert ok is None
    assert auth.check_websocket(host="127.0.0.1:8000", origin=None, cookie=session) is None

    assert auth.check_websocket(host="evil.example:8000", origin=None, cookie=session).code == "host_rejected"
    assert (
        auth.check_websocket(host="127.0.0.1:8000", origin="https://evil.example", cookie=session).code
        == "origin_rejected"
    )
    assert auth.check_websocket(host="127.0.0.1:8000", origin=None, cookie=None).code == "auth_required"


# ------------------------------------------------- 异常输入不能打成 500

# `/pair/{code}` 谁都能敲：中文路径、空值、超长都不能让身份接口 500，
# 否则就是本地给自己留了一个"一键刷错误日志"的口子。
BAD_PAIR_CODES = ("配对码", "１２３４５６７８", "sk-模型", "%E9%85%8D", "abcdefgh", "   ")


@pytest.mark.parametrize("code", BAD_PAIR_CODES)
def test_pair_link_bad_code_is_not_500(tmp_path, code):
    """异常配对码：一律按失败处理（403），不抛 TypeError。"""
    app, _runtime = _app(tmp_path)
    client = TestClient(app, base_url="http://127.0.0.1")
    response = client.get(f"/pair/{code}")
    assert response.status_code == 403
    assert "配对" in response.text


@pytest.mark.parametrize("code", [c for c in BAD_PAIR_CODES if c.strip()])
def test_pair_api_bad_code_is_not_500(tmp_path, code):
    """配对接口同样：非法输入按拒绝处理，不打 500。"""
    app, _runtime = _app(tmp_path)
    client = TestClient(app, base_url="http://127.0.0.1")
    response = client.post("/api/auth/pair", json={"code": code})
    assert response.status_code in (400, 403)


def test_pair_non_ascii_is_rejection_not_exception(tmp_path):
    """WebAuth.pair：非 ASCII 输入返回 Rejection，且失败计数照常推进。"""
    auth = WebAuth(tmp_path / "data")
    result = auth.pair("中文码")
    assert not isinstance(result, str)
    assert result.code in ("pair_rejected", "pair_locked")
    assert auth._pair_failures >= 1


# ------------------------------------------------- 配对链接落盘


# 为什么：配对码只在控制台打印一次、15 分钟就换，助手想再拿就得重启服务，
# 而重启又会换码 —— "想拿链接"和"保持链接有效"互相打架。落盘后随时可读。
def test_pair_url_dumped_to_run_dir(tmp_path, capsys):
    auth = WebAuth(tmp_path / "data")
    url = auth.announce_pairing()
    target = tmp_path / "data" / "run" / "pair_url.txt"
    assert target.exists()
    assert target.read_text(encoding="utf-8").strip() == auth.pairing_url()


def test_pair_url_dump_refreshed_after_rotate(tmp_path, capsys):
    auth = WebAuth(tmp_path / "data")
    auth.announce_pairing()  # 先落一次盘，再验证换码会同步刷新
    capsys.readouterr()
    target = tmp_path / "data" / "run" / "pair_url.txt"
    before = target.read_text(encoding="utf-8").strip()
    auth._rotate_code()
    after = target.read_text(encoding="utf-8").strip()
    assert after == auth.pairing_url()
    assert after != before


def test_pair_url_json_carries_expiry(tmp_path, capsys):
    """落盘的 json 必须带到期时刻：读码方先查它，才能判断手里的码还能不能用。"""
    import json
    import time

    auth = WebAuth(tmp_path / "data")
    auth.announce_pairing()
    capsys.readouterr()
    data = json.loads((tmp_path / "data" / "run" / "pair_url.json").read_text(encoding="utf-8"))
    assert data["code"] == auth.pairing_code
    assert data["url"] == auth.pairing_url()
    assert data["port"] == auth._display_port
    # 到期时刻应落在 TTL 附近（允许几秒误差），不能是过去
    remaining = data["expires_at"] - time.time()
    assert 0 < remaining <= PAIR_TTL_S + 2  # 允许两次取时刻之间的几毫秒漂移
    assert abs(remaining - data["expires_in_s"]) < 5


def test_pair_url_json_refreshed_on_rotate(tmp_path, capsys):
    """换码后 json 里的码与到期时刻都要跟着变，否则等于留下一条死链。"""
    import json

    auth = WebAuth(tmp_path / "data")
    auth.announce_pairing()
    capsys.readouterr()
    target = tmp_path / "data" / "run" / "pair_url.json"
    before = json.loads(target.read_text(encoding="utf-8"))
    auth._rotate_code()
    after = json.loads(target.read_text(encoding="utf-8"))
    assert after["code"] == auth.pairing_code
    assert after["code"] != before["code"]
    assert after["url"] == auth.pairing_url()


def test_pair_deadline_epoch_tracks_rotation(tmp_path):
    """_pair_deadline 是 monotonic，pair_deadline_epoch 要如实换算成墙上时钟。

    注意它不是单调递增的：换码后仍是「此刻 + TTL」，所以只能比对与当前时刻的差值。
    """
    auth = WebAuth(tmp_path / "data")
    assert auth.pair_deadline_epoch - time.time() == pytest.approx(PAIR_TTL_S, abs=3)

    auth._pair_deadline = time.monotonic() - 100  # 人为判成已过期
    assert auth.pair_deadline_epoch - time.time() < 0

    auth._rotate_code()
    assert auth.pair_deadline_epoch - time.time() == pytest.approx(PAIR_TTL_S, abs=3)


def test_expired_pair_rotates_and_rewrites_dump(tmp_path, capsys):
    """过期分支：换码 + 重打链接，落盘文件必须同步刷新成新的。"""
    auth = WebAuth(tmp_path / "data")
    auth.announce_pairing()
    capsys.readouterr()
    target = tmp_path / "data" / "run" / "pair_url.txt"

    old_code, old_url = auth.pairing_code, auth.pairing_url()
    auth._pair_deadline = time.monotonic() - 1  # 人为判过期

    result = auth.pair(old_code)
    assert not isinstance(result, str)
    assert result.code == "pair_expired"
    # 关键：文件里不能还是那条已经作废的链接
    assert target.read_text(encoding="utf-8").strip() != old_url
    assert target.read_text(encoding="utf-8").strip() == auth.pairing_url()
    assert auth.pairing_code != old_code


# ---------- 链接寿命：重启不换码、旧链接单独归类 ----------
#
# 用户连续几轮反馈"配对码不正确"，根因不是他点错，而是设计：
# 15 分钟自动换码 + 每次重启换码，发出去的链接必然常死。
# 下面三条锁住新的语义。


def test_code_survives_restart(tmp_path):
    """重启后要续用盘上未过期的码——否则我每改一次代码就作废一条链接。"""
    first = WebAuth(tmp_path / "data")
    first.announce_pairing()
    code = first.pairing_code

    second = WebAuth(tmp_path / "data")
    assert second.pairing_code == code
    assert second.pair_deadline_epoch - time.time() == pytest.approx(PAIR_TTL_S, abs=5)
    # 续用的码必须真能用
    assert isinstance(second.pair(code), str)


def test_expired_dump_is_not_reused(tmp_path):
    """盘上的码快过期/已过期时不能续用，要换新的（别递一条随时会断的链接）。"""
    auth = WebAuth(tmp_path / "data")
    auth.announce_pairing()
    dump = tmp_path / "data" / "run" / "pair_url.json"
    data = json.loads(dump.read_text(encoding="utf-8"))
    data["expires_at"] = time.time() - 1
    dump.write_text(json.dumps(data), encoding="utf-8")

    fresh = WebAuth(tmp_path / "data")
    assert fresh.pairing_code != auth.pairing_code


def test_stale_code_is_its_own_rejection_and_does_not_count(tmp_path, capsys):
    """点旧链接要报"这条是早前的"，而且不能记成暴力枚举失败。

    否则自己人点几下聊天记录里的旧链接，就把当前有效的码锁掉了。
    """
    auth = WebAuth(tmp_path / "data")
    old_code = auth.pairing_code
    auth._rotate_code()
    capsys.readouterr()

    result = auth.pair(old_code)
    assert not isinstance(result, str)
    assert result.code == "pair_stale"
    assert auth._pair_failures == 0
    # 当前码仍然有效，没被旧链接连带打掉
    assert isinstance(auth.pair(auth.pairing_code), str)


def test_stale_history_is_bounded(tmp_path):
    """记住的旧码有上限，不会无限攒。"""
    from omni.channels.web_auth import RETIRED_KEEP

    auth = WebAuth(tmp_path / "data")
    for _ in range(RETIRED_KEEP + 5):
        auth._rotate_code()
    assert len(auth._retired) == RETIRED_KEEP
