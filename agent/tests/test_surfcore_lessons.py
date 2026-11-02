"""从 surfcore 学习后落地的四道闸门（判定逻辑落地，不抄形态）。

参考来源：`xuexi/surfcore`（DSH 插件，Node/JS）。我们只取**判定逻辑**，
不取它的进程拓扑、文件布局与 UI。

| 本文件覆盖 | 参考项目的对应教训 | 我们系统的原缺口 |
| --- | --- | --- |
| 人格保存契约 | `cases/2026-10-31-persona-tone-overwrites-core.md`：保存契约必须逐层显式落地、空值语义要在协议里说清 | 空正文会把人格整块清空，而界面还在说「已保存」 |
| 人格热生效 | 「不被看到比被看到更危险」——用户以为改了其实没改 | persona 不在「改了要重启」的比较范围里 |
| 记忆隐私闸门 | `lib/privacy.js`：「模型可以撒谎，正则不会」 | 密钥拦截只写在提示词里，是概率约束 |
| 主动发言闸门 | 白名单归一化：多写入点共用一个归一化纯函数 | 定时投递过主动策略，模型自己发私聊不过 |

所有用例都**离线**：不联网、不碰真实账号与真实目录、不调模型。
"""

from __future__ import annotations

from pathlib import Path
from types import SimpleNamespace

import pytest

from omni.memory.guard import MemoryGuard
from omni.memory.privacy import PRIVACY_POLICY_VERSION, scan
from omni.safety.proactive import ProactiveDecision, proactive_decision
from omni.system.config_io import ConfigEditError, apply_updates, commit_config_updates
from omni.system.persona_rules import (
    PERSONA_PROMPT_MIN_CHARS,
    PersonaRuleError,
    check_persona_prompt,
    validate_persona_update,
)
from omni.tools.qq.tools import register_qq_tools
from omni.tools.registry import ToolRegistry


# ================================================================ 人格保存契约


def test_empty_persona_prompt_is_rejected_not_applied() -> None:
    """空正文必须被拒绝——放行等于「保存全部」时把人设悄悄清空。"""
    with pytest.raises(PersonaRuleError) as exc:
        check_persona_prompt([])
    assert "不能是空的" in str(exc.value)


def test_whitespace_only_persona_prompt_is_rejected() -> None:
    """纯空白也是空：YAML 里会被当成空串写进去。"""
    with pytest.raises(PersonaRuleError):
        check_persona_prompt(["   ", "\t", ""])


def test_persona_prompt_below_minimum_length_is_rejected() -> None:
    """太短 = 身份、说话方式、规则都没写全，视为误操作。"""
    with pytest.raises(PersonaRuleError) as exc:
        check_persona_prompt(["你是小鱼"])
    assert "太短" in str(exc.value)


def test_persona_prompt_with_minimum_length_is_accepted() -> None:
    """刚好够长就放行——不要用长度把人拦死在门槛外。"""
    body = "你" * PERSONA_PROMPT_MIN_CHARS
    check_persona_prompt([body])


def test_persona_prompt_over_maximum_length_is_rejected() -> None:
    """超长拒绝：人设每轮都进提示词，不能塞整本日志。"""
    with pytest.raises(PersonaRuleError) as exc:
        check_persona_prompt(["x" * 20001])
    assert "太长" in str(exc.value)


@pytest.mark.parametrize(
    "body",
    [
        "我的密码是 hunter2xyz",
        "密钥 sk-abcdefghijklmnopqrstuvwxyz",
        "token: ghp_abcdefghijklmnopqrstuvwxyz012345",
        "AKIAIOSFODNN7EXAMPLE",
        "Authorization: Bearer abcdefghijklmnopqrstuvwxyz",
        "SESSDATA=abcdefghijklmnopqrst",
    ],
)
def test_persona_prompt_containing_credentials_is_rejected(body: str) -> None:
    """人设会跟着每轮对话进提示词，凭据不能写在里面。"""
    with pytest.raises(PersonaRuleError) as exc:
        check_persona_prompt([body])
    assert "不能写在这里" in str(exc.value)


def test_credential_is_reported_before_length() -> None:
    """太短的正文里若有凭据，先报凭据。

    顺序反了会误导用户：报「太短」他会去补字，而不是删掉密码。
    """
    with pytest.raises(PersonaRuleError) as exc:
        check_persona_prompt(["密码是 hunter2"])
    assert "不能写在这里" in str(exc.value)


def test_persona_and_memory_share_one_credential_table() -> None:
    """两处闸门必须共用同一份检测表。

    第一版各写一份正则表：人格侧漏掉「密码是」与 Cookie 两类，记忆侧漏掉
    Slack/Google 令牌。两份真源必然漂移，漂移只会往「某一处更宽松」的方向走。
    """
    from omni.system.text_guard import scan_sensitive

    samples = [
        "我的密码是 hunter2xyz",
        "SESSDATA=abcdefghijklmnopqrst",
        "Slack 令牌 xoxb-1234567890-abcdefghijkl",
        "Google AIzaSyA1234567890abcdefghijklmnopq",
        "api_key: 0123456789abcdef",
    ]
    for text in samples:
        assert scan(text), f"记忆侧没检出：{text}"
        assert scan_sensitive(text), f"共享层没检出：{text}"
        with pytest.raises(PersonaRuleError):
            check_persona_prompt([text + "，这段话足够长不会被长度闸门拦下。"])


def test_persona_name_rules() -> None:
    with pytest.raises(PersonaRuleError):
        validate_persona_update({"persona": {"name": "  "}})
    with pytest.raises(PersonaRuleError):
        validate_persona_update({"persona": {"name": "x" * 31}})


def test_validate_persona_update_ignores_other_sections() -> None:
    """保存别的段不该被人格的规则挡住。"""
    validate_persona_update({"qq": {"enabled": True}})
    validate_persona_update({})


def test_config_write_rejects_empty_persona_and_keeps_file_intact(tmp_path: Path) -> None:
    """端到端：走真实的配置写入路径，拒绝时不碰文件。"""
    path = tmp_path / "config.yaml"
    original = (
        "persona:\n"
        '  name: "小鱼"\n'
        "  system_prompt: |\n"
        "    你是小鱼，一个运行在用户电脑上的个人智能体。\n"
        "    说话简洁，不要废话。\n"
    )
    path.write_text(original, encoding="utf-8")
    with pytest.raises(ConfigEditError) as exc:
        commit_config_updates(path, {"persona": {"system_prompt": []}})
    assert "不能是空的" in str(exc.value)
    assert path.read_text(encoding="utf-8") == original


def test_config_write_accepts_valid_persona(tmp_path: Path) -> None:
    """合法的人格照常写进去（正文要够长，过最小长度闸门）。"""
    path = tmp_path / "config.yaml"
    path.write_text(
        'persona:\n  name: "小鱼"\n  system_prompt: |\n    旧的人设正文在这里。\n',
        encoding="utf-8",
    )
    new_text = apply_updates(
        path.read_text(encoding="utf-8"),
        {
            "persona": {
                "system_prompt": [
                    "你是小鱼，一个运行在用户电脑上的个人智能体，说话简洁。",
                    "先给结论，再给理由；不确定就直说。",
                ]
            }
        },
    )
    assert "你是小鱼，一个运行在用户电脑上的个人智能体，说话简洁。" in new_text
    assert "旧的人设正文在这里。" not in new_text


def test_config_write_rejects_credentials_in_persona(tmp_path: Path) -> None:
    """含凭据的人格也拒绝，且文件不变。"""
    path = tmp_path / "config.yaml"
    original = 'persona:\n  name: "小鱼"\n  system_prompt: |\n    旧正文。\n'
    path.write_text(original, encoding="utf-8")
    with pytest.raises(ConfigEditError):
        commit_config_updates(path, {"persona": {"system_prompt": ["我的密码是 hunter2xyz", "别外传。"]}})
    assert path.read_text(encoding="utf-8") == original


# ================================================================ 记忆隐私闸门


@pytest.mark.parametrize(
    "text",
    [
        "我的密码是 hunter2xyz",
        "密钥 sk-abcdefghijklmnopqrstuvwxyz",
        "token ghp_abcdefghijklmnopqrstuvwxyz012345",
        "AWS AKIAIOSFODNN7EXAMPLE 在用",
        "Authorization: Bearer abcdefghijklmnopqrstuvwxyz",
        "SESSDATA=abcdefghijklmnopqrst",
        "api_key: 0123456789abcdef",
    ],
)
def test_credentials_are_detected_in_memory_text(text: str) -> None:
    assert scan(text), f"没检出：{text}"


@pytest.mark.parametrize(
    "text",
    [
        "用户喜欢喝美式咖啡",
        "项目代号是 X-42，会议室在 3 楼",
        "订单号 123456789012345678 是他买的",
        "这个视频的 BV 号是 BV1xx411c7mD",
        "快递单号 6222021234567890 是他的",
    ],
)
def test_ordinary_content_is_not_flagged(text: str) -> None:
    """不能误伤：普通长数字、订单号、BV 号里的数字都不该被当成凭据。"""
    hits = scan(text)
    assert not hits, f"误伤：{text} -> {hits}"


def test_bank_card_needs_context_word() -> None:
    """13~19 位数字只有旁边出现「卡号」这类词才算命中。"""
    assert not scan("快递单号 6222021234567890 已签收")
    assert scan("他的银行卡号是 6222021234567890")


def test_identity_card_needs_context_word() -> None:
    """18 位数字只有旁边出现「身份证」才算命中——否则订单号也会被拦。"""
    assert not scan("单号 11010519900307001X 已发货")
    assert scan("他的身份证号是 11010519900307001X")


def test_verification_code_needs_context_word() -> None:
    assert not scan("房间号 1234 定了")
    assert scan("短信验证码是 482913")


def test_memory_guard_rejects_credentials_without_spending_quota() -> None:
    """闸门拒在额度之前——被拒的写入不消耗当日额度。"""
    guard = MemoryGuard(None, max_writes_per_day=5)
    decision = guard.check("fact", "我的密码是 hunter2xyz")
    assert not decision.allowed
    assert "不能写进长期记忆" in decision.reason
    assert str(PRIVACY_POLICY_VERSION) in decision.reason


def test_memory_guard_allows_ordinary_facts() -> None:
    guard = MemoryGuard(None, max_writes_per_day=5)
    decision = guard.check("fact", "用户喜欢喝美式咖啡")
    assert decision.allowed


def test_memory_guard_rejection_reason_names_the_category() -> None:
    """拒绝理由要说清是哪一类，不能只说「不通过」。"""
    guard = MemoryGuard(None)
    decision = guard.check("fact", "这是 sk-abcdefghijklmnopqrstuvwxyz 密钥")
    assert not decision.allowed
    assert "密钥" in decision.reason


# ================================================================ 主动发言闸门


def _policy(**overrides):
    base = dict(
        allowed_users=[10001],
        proactive_enabled=True,
        owner_qq=10001,
        timezone="Asia/Shanghai",
        quiet_start="22:00",
        quiet_end="08:00",
        proactive_daily_limit=5,
    )
    base.update(overrides)
    return SimpleNamespace(**base)


def test_proactive_decision_allows_owner_outside_quiet_hours() -> None:
    import datetime as dt

    when = dt.datetime(2026, 11, 1, 10, 0, tzinfo=dt.timezone(dt.timedelta(hours=8)))
    decision = proactive_decision(_policy(), 10001, now=when)
    assert decision.state == "allow"
    assert decision.limit == 5


def test_proactive_decision_defers_in_quiet_hours() -> None:
    import datetime as dt

    when = dt.datetime(2026, 11, 1, 23, 0, tzinfo=dt.timezone(dt.timedelta(hours=8)))
    decision = proactive_decision(_policy(), 10001, now=when)
    assert decision.state == "defer"
    assert "安静时段" in decision.reason


def test_proactive_decision_denies_non_owner() -> None:
    """主动提醒只给所有者——别人在白名单里也不代表该收到主动推送。"""
    decision = proactive_decision(_policy(allowed_users=[10001, 10002]), 10002)
    assert decision.state == "deny"
    assert "仅允许指定所有者" in decision.reason


def test_proactive_decision_denies_when_disabled() -> None:
    decision = proactive_decision(_policy(proactive_enabled=False), 10001)
    assert decision.state == "deny"
    assert "未启用" in decision.reason


def test_qq_send_message_tool_respects_active_gate() -> None:
    """模型自己调工具主动私聊，也要过安静时段。"""
    from omni.tools.qq.client import OneBotClient

    registry = ToolRegistry()
    gate = lambda target: ProactiveDecision("defer", "安静时段，等待下一次补投")
    register_qq_tools(registry, OneBotClient("ws://x"), allowed_users=[10001], active_gate=gate)
    result = registry.execute("qq_send_message", {"user_id": 10001, "text": "hi"})
    assert result.status == "error"
    assert result.error_code == "proactive_quiet_hours"
    assert "安静时段" in result.text


def test_qq_send_message_tool_reports_denial() -> None:
    from omni.tools.qq.client import OneBotClient

    registry = ToolRegistry()
    gate = lambda target: ProactiveDecision("deny", "主动私聊今日额度已用完")
    register_qq_tools(registry, OneBotClient("ws://x"), allowed_users=[10001], active_gate=gate)
    result = registry.execute("qq_send_message", {"user_id": 10001, "text": "hi"})
    assert result.status == "error"
    assert result.error_code == "proactive_denied"
    assert "额度" in result.text


def test_qq_send_message_without_gate_keeps_legacy_behavior() -> None:
    """不注入闸门时行为不变——嵌入式用法与旧测试不受影响。"""
    from omni.tools.qq.client import OneBotClient

    registry = ToolRegistry()
    register_qq_tools(registry, OneBotClient("ws://x"), allowed_users=[10001])
    result = registry.execute("qq_send_message", {"user_id": 10001, "text": "hi"})
    # 白名单内但 QQ 未连接 → 走到连接检查，说明闸门没有拦
    assert result.error_code == "qq_not_connected"


def test_qq_send_message_gate_does_not_touch_allowlist_semantics() -> None:
    """白名单外的目标仍然先被白名单拦（错误码不同，可区分）。"""
    from omni.tools.qq.client import OneBotClient

    registry = ToolRegistry()
    gate = lambda target: ProactiveDecision("allow", "ok")
    register_qq_tools(registry, OneBotClient("ws://x"), allowed_users=[10001], active_gate=gate)
    result = registry.execute("qq_send_message", {"user_id": 999, "text": "hi"})
    assert result.error_code == "target_not_allowed"


# ================================================================ 分层守卫


def test_safety_layer_does_not_import_core() -> None:
    """判定下沉到 safety 后不能倒挂依赖（safety 是叶子层）。"""
    source = Path(__file__).resolve().parents[1] / "src/omni/safety/proactive.py"
    text = source.read_text(encoding="utf-8")
    assert "from ..core" not in text
    assert "import omni.core" not in text
