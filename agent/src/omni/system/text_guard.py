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
- **管**：高危个人标识（身份证号、银行卡号、验证码）——必须靠**上下文词**判定。
- **不管**：内容对不对、好不好（那是 review 提示词与人工判断的事）。
- **不管**：普通长数字（订单号、房间号、楼层号、BV 号里的数字）——不能误伤，
  所以身份证/银行卡这类不能只按位数判定，必须带上下文词。

版本号放这里而不是调用方：规则改了要能说清「当时按哪一版拦的」。
"""
from __future__ import annotations

import re
from dataclasses import dataclass

# 隐私策略版本：规则改了要能说清"当时按哪一版拦的"。
PRIVACY_POLICY_VERSION = 1


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
    ("Cookie 凭据", re.compile(
        r"\b(?:SESSDATA|bili_jct|buvid3|sessionid|csrf|access_token|refresh_token)"
        r"\s*=\s*[^\s;\"']{8,}",
        re.IGNORECASE,
    )),
    ("密码赋值", re.compile(
        r"(?:密码|口令|passwd|password|pwd)\s*(?:是|为|:|：|=)\s*\S{4,}",
        re.IGNORECASE,
    )),
    ("api_key 赋值", re.compile(
        r"\b(?:api[_\-]?key|secret[_\-]?key|access[_\-]?token)\s*(?:=|:)\s*\S{8,}",
        re.IGNORECASE,
    )),
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
    # 身份证：上下文词必须出现在值附近，避免全文里随便一个"身份证"
    # 就让所有 18 位数字都变成命中。
    for match in _ID_CARD_VALUE.finditer(body):
        window = body[max(0, match.start() - _CONTEXT_WINDOW):match.end() + _CONTEXT_WINDOW]
        if _ID_CARD_CONTEXT.search(window):
            hits.append(TextGuardHit("身份证号", match.span()))
            break
    for match in _BANK_CARD_VALUE.finditer(body):
        window = body[max(0, match.start() - _CONTEXT_WINDOW):match.end() + _CONTEXT_WINDOW]
        if _BANK_CARD_CONTEXT.search(window):
            hits.append(TextGuardHit("银行卡号", match.span()))
            break
    for match in _VERIFICATION_VALUE.finditer(body):
        window = body[max(0, match.start() - _CONTEXT_WINDOW):match.end() + _CONTEXT_WINDOW]
        if _VERIFICATION_CONTEXT.search(window):
            hits.append(TextGuardHit("验证码", match.span()))
            break
    hits.sort(key=lambda item: item.span[0])
    return hits


def has_sensitive(text: str) -> bool:
    """有没有命中（只要布尔时用它，省一次建列表）。"""
    return bool(scan_sensitive(text))


def sensitive_labels(text: str) -> list[str]:
    """命中的类别名（去重、保序）。"""
    return sorted({hit.label for hit in scan_sensitive(text)})
