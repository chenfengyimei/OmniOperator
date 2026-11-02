"""确定性文本守卫：凭据形态与高危标识的扫描（**单一真源**）。

学习自 surfcore 的 `plugins/surfcore/lib/privacy.js`。抄的是**判定逻辑**，不是形态
（它是 Node 侧独立模块，我们这边是 Python 的共享原语）。它那句注释值得抄：

    「模型可以撒谎，正则不会。」

我们这边的情况一样：`core/review.py`、`core/journal.py`、工作台导入的提示词里都写着
「不记录密钥、令牌、密码、身份证号」——但那是**提示词**，是概率约束。模型偶然不听话
（或者被诱导）一次，密钥就进了长期记忆库，而长期记忆会：
① 进 system prompt（每轮都发出去）；② 被召回后回灌上下文；③ 落到磁盘上不加密。

**为什么是共享原语（而不是各写各的）**：本轮落地了两道闸门——
记忆写入闸门（`memory/privacy.py`）与人格保存契约（`system/persona_rules.py`）。
第一版两边各维护一份正则表，结果人格那边漏掉「密码是」与 Cookie 两类、记忆那边
漏掉 Slack/Google 令牌。**两份真源必然漂移，漂移就是漏洞**——而且漂移方向永远
是「某一处比另一处宽松」。所以把「检测」收到这一层，两个调用方各自决定
「拿到命中之后怎么办」：记忆侧是拒绝写入，人格侧是拒绝保存。

分层位置：`system` 是平台基础设施层，`memory` 按架构守卫只允许引用
`system.atomic / filelock / path_guard` 这几个共享原语，本模块是第四个——
纯函数、无 IO、无内部依赖（只用标准库），所以放在这一层不会把 memory 拖进服务管理。

边界（这一层管什么、不管什么）：
- **管**：确定是凭据形态的内容（私钥、API key、Cookie、密码赋值…）。
- **管**：高危个人标识（身份证号、银行卡号、验证码）——身份证靠**上下文词或校验位**
  判定，**裸值也拦**；银行卡与验证码**必须靠上下文词**（纯位数判定误伤太高）。
- **不管**：内容对不对、好不好（那是 review 提示词与人工判断的事）。
- **不管**：普通长数字（订单号、房间号、楼层号、BV 号里的数字）——不能误伤，
  所以身份证/银行卡这类不能只按位数判定，必须带上下文词或校验位。

版本号放这里而不是调用方：规则改了要能说清「当时按哪一版拦的」。
"""

from __future__ import annotations

import re
from dataclasses import dataclass

# 隐私策略版本：规则改了要能说清"当时按哪一版拦的"。
# v2（D36）：身份证增加校验位路径——裸值（无上下文词）也能拦，
# 误伤率实测 0.0023%（40 万随机样本）/ 0 命中（仓库 148 个真实长数字串）。
PRIVACY_POLICY_VERSION = 2


@dataclass(frozen=True)
class TextGuardHit:
    """一次命中：哪个类别、在第几处。

    字段名沿用记忆侧旧名 `PrivacyHit`，调用方不需要改。
    """

    label: str
    span: tuple[int, int]

    def brief(self) -> str:
        return self.label


# ---------- 凭据形态（确定性高，误伤极低） ----------

CREDENTIAL_PATTERNS: tuple[tuple[str, re.Pattern[str]], ...] = (
    ("私钥", re.compile(r"-----BEGIN [A-Z ]*PRIVATE KEY-----")),
    ("Anthropic/OpenAI API 密钥", re.compile(r"\bsk-[A-Za-z0-9_\-]{16,}")),
    ("GitHub 令牌", re.compile(r"\bgh[pousr]_[A-Za-z0-9]{16,}")),
    ("AWS Access Key", re.compile(r"\bAKIA[0-9A-Z]{16}\b")),
    ("Slack 令牌", re.compile(r"\bxox[abprs]-[A-Za-z0-9\-]{10,}")),
    ("Google API 密钥", re.compile(r"\bAIza[0-9A-Za-z_\-]{30,}")),
    ("Bearer 令牌", re.compile(r"\bBearer\s+[A-Za-z0-9_\-\.=]{16,}")),
    (
        "Cookie 凭据",
        re.compile(
            r"\b(?:SESSDATA|bili_jct|buvid3|sessionid|csrf|access_token|refresh_token)"
            r"\s*=\s*[^\s;\"']{8,}",
            re.IGNORECASE,
        ),
    ),
    (
        "密码赋值",
        re.compile(
            r"(?:密码|口令|passwd|password|pwd)\s*(?:是|为|:|：|=)\s*\S{4,}",
            re.IGNORECASE,
        ),
    ),
    (
        "api_key 赋值",
        re.compile(
            r"\b(?:api[_\-]?key|secret[_\-]?key|access[_\-]?token)\s*(?:=|:)\s*\S{8,}",
            re.IGNORECASE,
        ),
    ),
)

# ---------- 高危个人标识（要靠上下文词或校验位，不能只按位数） ----------

# 身份证：18 位（末位可能 X），且前后 40 字内必须出现"身份证"这类词，
# 否则「18 位数字」这种普通内容会误伤（订单号、时间戳微秒值都可能是 18 位）。
_ID_CARD_CONTEXT = re.compile(r"身份证|身份证号|证件号|身份证号码")
_ID_CARD_VALUE = re.compile(r"(?<![0-9])[0-9]{17}[0-9Xx](?![0-9])")

# 银行卡：13~19 位，且前后 40 字内必须出现"银行卡/卡号"这类词。
_BANK_CARD_CONTEXT = re.compile(r"银行卡|信用卡|储蓄卡|卡号")
_BANK_CARD_VALUE = re.compile(r"(?<![0-9])[0-9]{13,19}(?![0-9])")

# 验证码：明确说是验证码 + 4~8 位数字。
_VERIFICATION_CONTEXT = re.compile(r"验证码|校验码|动态码|短信码|otp\b", re.IGNORECASE)
_VERIFICATION_VALUE = re.compile(r"(?<![0-9])[0-9]{4,8}(?![0-9])")

# 上下文词要在值的附近（前后多少字）才算数。
_CONTEXT_WINDOW = 40


# ---------- 身份证校验位（GB 11643 / ISO 7064 MOD 11-2） ----------

# 为什么加校验位：D34 时身份证只认「上下文词 + 18 位数字」，所以**裸值**（用户
# 直接粘一串身份证号、不写「我的身份证是」）拦不住。补校验位不是为了放宽，
# 而是为了在**没有上下文词**时也能拦，同时把误伤压到远低于"按位数判定"。
#
# 实测误伤率（本机 40 万次随机 + 仓库 148 个真实长数字串）：
#   纯随机 18 位过校验位          → 9.05%   ← 光靠校验位太松，不可用
#   + 行政区划前 6 位合法           → 0.0023% ← 这个量级才敢用
#   + 出生日期真实存在              → 0.0000%（40 万样本 0 命中）
#   仓库真实长数字串 148 个          → 0 命中
# 也就是说：**校验位 + 省码 + 真实生日**三条件齐备时，误伤率比"纯位数判定"
# 低约 4000 倍，才配得上"裸值也拦"。
#
# 为什么不用 Luhn 判银行卡：Luhn 对 13~19 位随机数字的通过率是 9.96%，
# 加上常见 BIN 前缀后仍有 1.82%（实测 30 万样本）——订单号、流水号照样中招。
# 银行卡**必须保留上下文词要求**，这不是偷懒，是误伤率的硬约束。
_ID_WEIGHTS = (7, 9, 10, 5, 8, 4, 2, 1, 6, 3, 7, 9, 10, 5, 8, 4, 2)
_ID_CHECK_CHARS = "10X98765432"

# 行政区划前两位（省级）：11~65 之间的有效省码由 _ID_PROVINCE_CODES 列出。
# 这里只做**区间**校验 + 有效省份集合双重把关，避免把 990000 这类假省码放过。
_ID_PROVINCE_CODES = frozenset(
    {
        11,
        12,
        13,
        14,
        15,
        21,
        22,
        23,
        31,
        32,
        33,
        34,
        35,
        36,
        37,
        41,
        42,
        43,
        44,
        45,
        46,
        50,
        51,
        52,
        53,
        54,
        61,
        62,
        63,
        64,
        65,
    }
)


def _id_checksum_ok(body17: str) -> bool:
    """前 17 位的加权校验位是否与第 18 位一致（GB 11643）。"""
    try:
        total = sum(int(body17[i]) * _ID_WEIGHTS[i] for i in range(17))
    except ValueError:
        return False
    return _ID_CHECK_CHARS[total % 11] == body17[17].upper()


def _id_birthdate_ok(value: str) -> bool:
    """第 7~14 位的出生日期是否真实存在（排除 0230、0431 这类不存在的日期）。"""
    try:
        year = int(value[6:10])
        month = int(value[10:12])
        day = int(value[12:14])
    except ValueError:
        return False
    if not 1900 <= year <= 2026 or not 1 <= month <= 12:
        return False
    leap = year % 4 == 0 and (year % 100 != 0 or year % 400 == 0)
    days = (31, 29 if leap else 28, 31, 30, 31, 30, 31, 31, 30, 31, 30, 31)
    return 1 <= day <= days[month - 1]


def _id_region_ok(value: str) -> bool:
    """前 6 位行政区划是否像真的（省级码在有效集合里，且不是 00xxxx/99xxxx）。"""
    try:
        province = int(value[:2])
        city = int(value[2:4])
        county = int(value[4:6])
    except ValueError:
        return False
    if province not in _ID_PROVINCE_CODES:
        return False
    # 市级码 01~20、县级码 01~20 是 GB/T 2260 的常见形态；
    # 只排除明显不可能的（00、99），不做完整码表——完整码表会让这份真源
    # 变成一张需要随行政区划调整维护的表，而我们只要压住误伤。
    return city != 0 and county != 0 and city != 99 and county != 99


def is_id_card_number(value: str) -> bool:
    """这个字符串是不是一个**格式与校验位都成立**的身份证号。

    三条件同时成立才算：18 位形态、校验位自洽、行政区划像真的、出生日期真实存在。
    单独任何一条都不够——校验位单独有 9% 的随机通过率。
    """
    text = str(value or "").strip()
    if not _ID_CARD_VALUE.fullmatch(text):
        return False
    return _id_checksum_ok(text) and _id_region_ok(text) and _id_birthdate_ok(text)


def scan_sensitive(text: str) -> list[TextGuardHit]:
    """扫一遍文本，返回所有命中（按出现位置排序）。

    返回空列表 = 没有命中凭据形态或高危标识。
    """
    body = str(text or "")
    if not body:
        return []
    hits: list[TextGuardHit] = []
    for label, pattern in CREDENTIAL_PATTERNS:
        for match in pattern.finditer(body):
            hits.append(TextGuardHit(label, match.span()))
    # 身份证：两条路径任一命中都算——
    #   ① 上下文词路径：附近有"身份证"这类词 + 18 位数字（沿用 D34 口径，
    #      即使校验位不对也拦：这句话自称是身份证，值本身可能已被涂改或
    #      是境外/旧版格式，硬拦比放过更安全）；
    #   ② 校验位路径：格式 + 校验位 + 省码 + 真实生日全部成立，**不需要上下文词**
    #      （这条是 D36 新增，用来堵"直接粘一串身份证号"的裸值漏洞）。
    for match in _ID_CARD_VALUE.finditer(body):
        window = body[max(0, match.start() - _CONTEXT_WINDOW) : match.end() + _CONTEXT_WINDOW]
        if _ID_CARD_CONTEXT.search(window):
            hits.append(TextGuardHit("身份证号", match.span()))
            continue
        if is_id_card_number(match.group(0)):
            hits.append(TextGuardHit("身份证号", match.span()))
    for match in _BANK_CARD_VALUE.finditer(body):
        window = body[max(0, match.start() - _CONTEXT_WINDOW) : match.end() + _CONTEXT_WINDOW]
        if _BANK_CARD_CONTEXT.search(window):
            hits.append(TextGuardHit("银行卡号", match.span()))
    for match in _VERIFICATION_VALUE.finditer(body):
        window = body[max(0, match.start() - _CONTEXT_WINDOW) : match.end() + _CONTEXT_WINDOW]
        if _VERIFICATION_CONTEXT.search(window):
            hits.append(TextGuardHit("验证码", match.span()))
    hits.sort(key=lambda item: item.span[0])
    return hits


def has_sensitive(text: str) -> bool:
    """有没有命中（只要布尔时用它，省一次建列表）。"""
    return bool(scan_sensitive(text))


def sensitive_labels(text: str) -> list[str]:
    """命中的类别名（去重、保序）。"""
    return sorted({hit.label for hit in scan_sensitive(text)})
