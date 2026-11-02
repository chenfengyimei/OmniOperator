"""D36：隐私闸门的身份证校验位路径 + 记忆容量三档告警。

**背景**：D34 落地四道闸门后，台账一直挂着一条「待真实验收」的限制：
「裸身份证/银行卡号不拦（按位数判定会误伤订单号与时间戳，取误伤更少）」。
D36 先核实这条限制的准确边界，再补上能补的那一半。

**核实结论（实测，不是推断）**：

- 身份证**可以**裸拦，但前提不是「按位数」而是「按校验位」。
  纯随机 18 位数字过 GB 11643 校验位 = 9.05%；再加上省码与真实出生日期，
  误伤率降到 0.0023%（40 万样本），仓库 148 个真实长数字串 0 命中。
  也就是说校验位路径比纯位数判定精确约 4000 倍，才配得上「裸值也拦」。
- 银行卡**不能**裸拦：Luhn 对 13~19 位随机数字通过率 9.96%，加常见 BIN
  前缀后仍有 1.82%（30 万样本）。所以银行卡继续要求上下文词——
  这不是偷懒，是误伤率的硬约束。
- 验证码同理，继续要求上下文词。

**这一层为什么仍是单一真源**：人格保存契约（`system/persona_rules.py`）与
记忆写入闸门（`memory/privacy.py`）共用本模块的检测结果。两边各写一份
正则表必然漂移，而漂移只会往「某一处更宽松」的方向走。

**全部离线**：不联网、不碰真实账号与真实目录、不调模型。
"""

from __future__ import annotations

import random

from omni.memory.privacy import PRIVACY_POLICY_VERSION, scan
from omni.system.text_guard import is_id_card_number


# ---------- 身份证校验位路径 ----------


def _valid_ids() -> list[str]:
    """构造若干**校验位自洽**的身份证号（GB 11643）。"""
    weights = (7, 9, 10, 5, 8, 4, 2, 1, 6, 3, 7, 9, 10, 5, 8, 4, 2)
    check = "10X98765432"
    out: list[str] = []
    for region, year, month, day, seq in (
        ("110101", 1990, 3, 15, 123),
        ("310101", 1985, 11, 7, 45),
        ("440305", 2000, 1, 1, 1),
        ("510107", 1995, 6, 30, 999),
        ("320106", 1978, 12, 25, 500),
        ("330102", 2001, 2, 28, 7),
    ):
        body = f"{region}{year:04d}{month:02d}{day:02d}{seq:03d}"
        out.append(body + check[sum(int(body[i]) * weights[i] for i in range(17)) % 11])
    return out


def test_valid_id_card_is_recognized() -> None:
    """校验位自洽 + 省码合法 + 生日真实的 18 位串，要能识别成身份证。"""
    values = _valid_ids()
    assert values, "样本构造失败"
    for value in values:
        assert is_id_card_number(value), f"应当识别为身份证：{value}"


def test_bare_id_card_is_blocked_without_context_word() -> None:
    """**裸值也拦**——这是 D36 补上的那条限制。

    用户直接粘一串身份证号、不写「我的身份证是」，旧规则拦不住。
    """
    for value in _valid_ids():
        hits = scan(f"这是我的资料 {value} 请查收")
        assert any(hit.label == "身份证号" for hit in hits), f"裸值没拦：{value}"


def test_id_card_with_context_word_is_still_blocked() -> None:
    """上下文词路径照旧生效：即使校验位不对（涂改/境外/旧版格式）也拦。"""
    # 这个样本校验位不成立（末位应为 7），但它自称是身份证——照样拦。
    assert scan("他的身份证号是 11010519900307001X")
    assert scan("身份证号码 11010519900307001X")


def test_order_numbers_are_not_misjudged_as_id_cards() -> None:
    """**不能误伤**：18 位订单号、流水号、时间戳不该被当成身份证。

    这是本轮最要紧的回归：加校验位就是为了让「拦得更全」不以「误伤更多」为代价。
    """
    random.seed(20261101)
    # 随机 18 位：校验位通过率约 9%，但省码+生日再砍一刀后应几乎不命中。
    false_positives = 0
    for _ in range(20000):
        value = "".join(random.choice("0123456789") for _ in range(18))
        if scan(f"订单号 {value} 已发货"):
            false_positives += 1
    # 允许极少量（省码+生日联合约束的残余概率），但绝不能是「一大半」。
    assert false_positives <= 20, f"误伤过高：{false_positives}/20000"


def test_common_non_id_numbers_pass() -> None:
    """几类真实存在的高频长数字，逐条确认不误伤。"""
    samples = (
        "快递单号 6222021234567890 已签收",  # 13 位 + 银行卡上下文词
        "订单 202608011234567890 已发货",  # 18 位日期前缀流水
        "时间戳 1767225600000",  # 13 位毫秒
        "房间号 1234 定了",  # 短数字
        "BV1xx411c7mD 的 av 号 114514",  # BV 号里的数字
        "电话 13800138000",  # 手机号
    )
    for text in samples:
        assert not scan(text), f"误伤：{text}"


def test_invalid_checksum_but_valid_shape_is_not_a_bare_id() -> None:
    """形态像但校验位不成立 → 裸值不拦（等上下文词路径去拦）。

    这条是「不误伤」与「拦得全」之间的分界：校验位不自洽的数字有 91%
    是普通长数字，放行它们才是对的。
    """
    assert not is_id_card_number("110101199003151234")  # 末位应为 3
    assert not is_id_card_number("11010119900315123X")  # 末位应为 5
    assert not is_id_card_number("110101199013151233")  # 13 月不存在
    assert not is_id_card_number("110101199002301233")  # 2 月 30 日不存在
    assert not is_id_card_number("990101199003151233")  # 99 省码不存在
    assert not is_id_card_number("11010119900315123")  # 17 位，形态就不对


def test_bank_card_still_requires_context_word() -> None:
    """银行卡**继续**要求上下文词——Luhn 路径的误伤率不够低（实测 1.82%）。"""
    assert not scan("快递单号 6222021234567890 已签收")
    assert scan("他的银行卡号是 6222021234567890")


def test_verification_code_still_requires_context_word() -> None:
    assert not scan("房间号 1234 定了")
    assert scan("短信验证码是 482913")


def test_policy_version_bumped() -> None:
    """规则改了要能说清「当时按哪一版拦的」。"""
    assert PRIVACY_POLICY_VERSION >= 2
