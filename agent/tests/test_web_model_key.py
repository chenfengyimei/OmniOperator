"""工作台「填 API Key」：密钥只进 .env，接口不回明文。

为什么单独一组：这条链路以前是假的（Key 只存浏览器 localStorage，后端读不到），
现在改成真正写 `.env` + 立刻注入进程环境，所以必须锁住三件事——
1) 只允许密钥形状的变量名，别把任意 KEY=VALUE 塞进 .env；
2) 定向替换那一行，注释、顺序、其它变量原样保留；
3) 任何响应里都不能出现明文。
"""
from __future__ import annotations

import pytest
from fastapi.testclient import TestClient

from omni.bootstrap import build_runtime
from omni.channels.web import create_app
from omni.safety.gates import DenyApprover
from omni.system.config_io import ConfigEditError, read_env_secrets, set_env_secret


def _client(tmp_path):
    """临时工程根（config/ + .env）+ 临时库 + 真实配对。"""
    root = tmp_path / "proj"
    (root / "config").mkdir(parents=True)
    config_path = root / "config" / "config.yaml"
    config_path.write_text("persona:\n  name: 测试小鱼\n", encoding="utf-8")
    runtime = build_runtime(
        approver=DenyApprover(),
        db_path=tmp_path / "m.db",
        config_path=config_path,
        skills_root=tmp_path / "skills",
        data_dir=tmp_path / "data",
        start_background_workers=False,
    )
    app = create_app(runtime)
    client = TestClient(app, base_url="http://127.0.0.1")
    code = app.state.web_auth.pairing_code
    assert client.post("/api/auth/pair", json={"code": code}).status_code == 200
    return client, runtime, root / ".env"


# ---------- 纯函数层 ----------


def test_read_env_secrets_only_returns_secret_shaped_names(tmp_path):
    env = tmp_path / ".env"
    env.write_text(
        "# 注释不该出现\n"
        "DEEPSEEK_API_KEY=sk-abcdefghijklmn\n"
        "OPENAI_API_KEY=\n"
        "HF_ENDPOINT=https://hf-mirror.com\n"
        "SOME_TOKEN=abcdefghijk\n"
        "lower_key=nope\n",
        encoding="utf-8",
    )
    keys = read_env_secrets(env)
    assert set(keys) == {"DEEPSEEK_API_KEY", "OPENAI_API_KEY", "SOME_TOKEN"}
    assert keys["DEEPSEEK_API_KEY"] == {"configured": True, "masked": "sk-a…klmn"}
    assert keys["OPENAI_API_KEY"] == {"configured": False, "masked": ""}
    # 明文不许出现在返回值里
    assert "abcdefghijklmn" not in str(keys)


def test_read_env_secrets_missing_file_is_empty(tmp_path):
    assert read_env_secrets(tmp_path / "没有这个文件.env") == {}


def test_set_env_secret_replaces_line_and_keeps_everything_else(tmp_path, monkeypatch):
    monkeypatch.setenv("DEEPSEEK_API_KEY", "placeholder")  # 让 monkeypatch 负责还原进程环境
    env = tmp_path / ".env"
    env.write_text(
        "# 我的密钥\nDEEPSEEK_API_KEY=old-key-1234567890\nHF_ENDPOINT=https://hf-mirror.com\n",
        encoding="utf-8",
    )
    masked = set_env_secret(env, "DEEPSEEK_API_KEY", "  sk-new-abcdefghijklmn  ")
    text = env.read_text(encoding="utf-8")
    assert "DEEPSEEK_API_KEY=sk-new-abcdefghijklmn" in text
    assert "# 我的密钥" in text and "HF_ENDPOINT=https://hf-mirror.com" in text
    assert "old-key" not in text
    assert masked == "sk-n…klmn"


def test_set_env_secret_appends_when_missing(tmp_path, monkeypatch):
    monkeypatch.setenv("MOONSHOT_API_KEY", "placeholder")
    env = tmp_path / ".env"
    env.write_text("HF_ENDPOINT=https://hf-mirror.com\n", encoding="utf-8")
    set_env_secret(env, "MOONSHOT_API_KEY", "sk-moonshot-1234567")
    lines = env.read_text(encoding="utf-8").splitlines()
    assert lines[-1] == "MOONSHOT_API_KEY=sk-moonshot-1234567"
    assert lines[0] == "HF_ENDPOINT=https://hf-mirror.com"


def test_set_env_secret_creates_file(tmp_path, monkeypatch):
    monkeypatch.setenv("OPENAI_API_KEY", "placeholder")
    env = tmp_path / "新.env"
    set_env_secret(env, "OPENAI_API_KEY", "sk-openai-1234567890")
    assert env.read_text(encoding="utf-8").strip() == "OPENAI_API_KEY=sk-openai-1234567890"


def test_set_env_secret_empty_value_clears_but_keeps_line(tmp_path, monkeypatch):
    monkeypatch.setenv("ANTHROPIC_API_KEY", "placeholder")
    env = tmp_path / ".env"
    env.write_text("ANTHROPIC_API_KEY=sk-ant-1234567890\n", encoding="utf-8")
    masked = set_env_secret(env, "ANTHROPIC_API_KEY", "   ")
    assert env.read_text(encoding="utf-8").strip() == "ANTHROPIC_API_KEY="
    assert masked == ""
    assert read_env_secrets(env)["ANTHROPIC_API_KEY"]["configured"] is False


@pytest.mark.parametrize(
    "name,value",
    [
        ("DEEPSEEK_API_KEY", "sk-has space-1234"),
        ("DEEPSEEK_API_KEY", "sk-line1\nsk-line2"),
        ("DEEPSEEK_API_KEY", "x" * 513),
        ("DEEPSEEK_API_KEY;rm -rf", "sk-whatever-1234"),
        ("not_secret", "value-1234567890"),
        ("", "value-1234567890"),
    ],
)
def test_set_env_secret_rejects_bad_input(tmp_path, name, value):
    env = tmp_path / ".env"
    with pytest.raises(ConfigEditError):
        set_env_secret(env, name, value)
    assert not env.exists()  # 拒绝时不留下半个文件


# ---------- HTTP 层 ----------


def test_model_key_requires_pairing(tmp_path):
    client, _runtime, _env = _client(tmp_path)
    anon = TestClient(client.app, base_url="http://127.0.0.1")
    assert anon.get("/api/system/model-keys").status_code == 401
    assert anon.post("/api/system/model-key", json={"name": "OPENAI_API_KEY", "value": "sk-x"}).status_code == 401


def test_model_key_post_writes_env_and_hides_plaintext(tmp_path, monkeypatch):
    monkeypatch.setenv("OPENAI_API_KEY", "placeholder")
    client, runtime, env_path = _client(tmp_path)
    env_path.write_text("# 保留我\nHF_ENDPOINT=https://hf-mirror.com\n", encoding="utf-8")

    response = client.post(
        "/api/system/model-key",
        json={"name": "OPENAI_API_KEY", "value": "sk-openai-abcdefghijkl"},
    )
    assert response.status_code == 200
    payload = response.json()
    assert payload == {
        "ok": True,
        "name": "OPENAI_API_KEY",
        "masked": "sk-o…ijkl",
        "configured": True,
    }
    assert "sk-openai-abcdefghijkl" not in response.text

    text = env_path.read_text(encoding="utf-8")
    assert "OPENAI_API_KEY=sk-openai-abcdefghijkl" in text
    assert "# 保留我" in text
    # runtime 的 env 路径就是 config 同一棵树的 .env（不是真机文件）
    assert runtime.paths.env == env_path

    status = client.get("/api/system/model-keys").json()["keys"]
    assert status["OPENAI_API_KEY"] == {"configured": True, "masked": "sk-o…ijkl"}


def test_model_key_bad_name_is_400(tmp_path):
    client, _runtime, env_path = _client(tmp_path)
    response = client.post("/api/system/model-key", json={"name": "随便写", "value": "sk-x"})
    assert response.status_code == 400
    assert "不合法" in response.json()["detail"]
    assert not env_path.exists()


def test_model_key_clear_is_idempotent(tmp_path, monkeypatch):
    monkeypatch.setenv("DASHSCOPE_API_KEY", "placeholder")
    client, _runtime, env_path = _client(tmp_path)
    client.post("/api/system/model-key", json={"name": "DASHSCOPE_API_KEY", "value": "sk-dash-1234567890"})
    cleared = client.post("/api/system/model-key", json={"name": "DASHSCOPE_API_KEY", "value": ""})
    assert cleared.status_code == 200
    assert cleared.json()["configured"] is False
    assert "DASHSCOPE_API_KEY=\n" in env_path.read_text(encoding="utf-8")


# ---------- 保存全部设置：错误信息不能被密钥脱敏"顺手"吞掉 ----------
#
# 前端每次保存都带一个空串的 qq.access_token（掩码值不会回传），
# 旧逻辑只要看到密钥键就把所有错误替换成"含密钥字段，详情已省略"，
# 于是真正的失败原因（找不到对应行）永远看不到。

def test_config_save_reports_real_error_when_secret_untouched(tmp_path):
    """密钥是空的（保留旧值）时，错误信息必须说真话。"""
    client, _runtime, _env = _client(tmp_path)
    resp = client.post(
        "/api/system/config",
        json={"updates": {"qq": {"access_token": "", "ws_url": ""}}},
    )
    assert resp.status_code == 400
    detail = resp.json()["detail"]
    assert "详情已省略" not in detail
    assert "非空字符串" in detail


def test_config_save_hides_detail_only_when_writing_real_secret(tmp_path):
    """真的要写入非空密钥时，错误信息脱敏，且响应里绝不能回显明文。"""
    client, _runtime, _env = _client(tmp_path)
    resp = client.post(
        "/api/system/config",
        json={"updates": {"qq": {"access_token": "sk-very-secret-abcdef", "ws_url": ""}}},
    )
    assert resp.status_code == 400
    body = resp.text
    assert "sk-very-secret-abcdef" not in body
    assert "详情已省略" in resp.json()["detail"]


def test_config_save_creates_missing_section_end_to_end(tmp_path):
    """config.yaml 只有 persona 时，保存 qq 也要成功（补节），而不是 400。"""
    client, runtime, _env = _client(tmp_path)
    resp = client.post(
        "/api/system/config",
        json={"updates": {"qq": {"enabled": True, "ws_url": "ws://127.0.0.1:3001"}}},
    )
    assert resp.status_code == 200
    text = runtime.paths.config.read_text(encoding="utf-8")
    assert "qq:" in text and "enabled: true" in text
    assert "persona:" in text  # 原有内容不能被冲掉


# ---------- 设置中心读的是磁盘，不是启动快照（D30 修「选过又变没选」） ----------


def test_config_get_reflects_disk_without_restart(tmp_path):
    """保存成功后重开设置中心，必须看到刚存的值。

    以前 GET 读的是 `runtime.config`（进程启动那一刻的快照），保存完再打开页面
    看到的还是旧值——用户以为"没存上"，于是再选一遍，第二遍就把第一遍覆盖了。
    """
    client, runtime, _env = _client(tmp_path)
    runtime.paths.config.write_text(
        'models:\n  chat: "deepseek/deepseek-v4-pro"\n  vision: ""\n', encoding="utf-8"
    )
    body = client.get("/api/system/config").json()
    assert body["models"]["chat"] == "deepseek/deepseek-v4-pro"
    assert body["models"]["vision"] == ""
    # 启动快照里还是默认值 → 明确告诉界面"这次改动要重启才生效"
    assert body["pending_restart"] is True


def test_config_post_writes_reasoning_levels(tmp_path):
    client, runtime, _env = _client(tmp_path)
    resp = client.post(
        "/api/system/config",
        json={
            "updates": {
                "models": {"chat": "deepseek/deepseek-flash"},
                "reasoning": {"chat": "high", "agent_mode": "off"},
            }
        },
    )
    assert resp.status_code == 200, resp.text
    assert resp.json()["pending_restart"] is True

    body = client.get("/api/system/config").json()
    assert body["models"]["chat"] == "deepseek/deepseek-flash"
    assert body["reasoning"]["chat"] == "high"
    assert body["reasoning"]["agent_mode"] == "off"
    # 没动过的档位保持 auto（不因为保存一次就给别人塞一个默认档）
    assert body["reasoning"]["vision"] == "auto"
    # 文件里真的写了，重启后读到的就是它
    from omni.config import load_config

    assert load_config(runtime.paths.config).reasoning.chat == "high"


def test_config_post_rejects_unknown_reasoning_level(tmp_path):
    client, _runtime, _env = _client(tmp_path)
    resp = client.post(
        "/api/system/config",
        json={"updates": {"reasoning": {"chat": "ultra"}}},
    )
    assert resp.status_code == 400


def test_config_post_keeps_unknown_model_string_verbatim(tmp_path):
    """列表里没有的新型号也必须原样存下来：认不出 ≠ 清空。"""
    client, runtime, _env = _client(tmp_path)
    resp = client.post(
        "/api/system/config",
        json={"updates": {"models": {"chat": "deepseek/还没发布的名字"}}},
    )
    assert resp.status_code == 200, resp.text
    from omni.config import load_config

    assert load_config(runtime.paths.config).models.chat == "deepseek/还没发布的名字"


def test_config_post_allows_empty_vision(tmp_path):
    """「看截图」留空 = 跟随日常对话（V4.1 Flash 自己能看图），不该卡住整次保存。"""
    client, runtime, _env = _client(tmp_path)
    resp = client.post(
        "/api/system/config",
        json={"updates": {"models": {"chat": "deepseek/deepseek-flash", "vision": ""}}},
    )
    assert resp.status_code == 200, resp.text
    from omni.config import load_config

    assert load_config(runtime.paths.config).models.vision == ""
