"""记忆写入的隐私闸门（判定委托给共享原语 `system/text_guard.py`）。

学习自 surfcore 的 `plugins/surfcore/lib/privacy.js`（抄判定逻辑，不抄形态）。
它开头注释里有一句话值得抄：

    「模型可以撒谎，正则不会。」

我们这边的情况一样：`core/review.py` 与 `core/journal.py` 的提示词里都写着
「不记录密钥、令牌、密码、身份证号等凭据」——但那是**提示词**，是概率约束。
模型偶然不听话（或者被诱导）一次，密钥就进了长期记忆库，而长期记忆会：
① 进 system prompt（每轮都发出去）；② 被召回后回灌上下文；③ 落到磁盘上不加密。

所以这里用**确定性正则**做同一件事，落在 `MemoryGuard.check` 里——
记忆写入有五个入口（对话 remember / review / 工作台导入 / 技能创建 / 回滚），
闸门收在一处，所以在这里拦住就等于所有入口都拦住了。

**检测规则在 `system/text_guard.py`**（共享原语，单一真源）。本模块只负责
记忆侧的两件事：把命中翻译成给用户看的拒绝理由，以及对外保留旧接口。
第一版两份正则表各写一份时，人格侧漏掉了「密码是」与 Cookie 两类——
漂移只会往「某一处更宽松」的方向走，所以检测必须只有一份。

边界（这一层管什么、不管什么）：
- **管**：确定是凭据形态的内容（私钥、API key、Cookie、密码赋值…）→ 整条拒绝写入。
- **管**：高危个人标识（身份证号、银行卡号）→ 整条拒绝写入。
- **不管**：内容对不对、好不好（那是 review 提示词与人工判断的事）。
- **不管**：普通长数字（订单号、房间号、楼层号、BV 号里的数字）——不能误伤，
  所以身份证/银行卡这类必须靠**上下文词**或**校验位**判定，不能只按位数。

为什么不做「脱敏后写入」而是「拒绝写入」：脱敏后的残缺值仍然在库里，
而且用户不会知道自己的记忆被改过——下次模型用出来的是一个错的凭据。
宁可让模型当场改写一句「用户配置了一个 API key」。
"""
from __future__ import annotations

from ..system.text_guard import (
    PRIVACY_POLICY_VERSION,
    TextGuardHit,
    scan_sensitive,
)

# 旧名保留：调用方（含既有测试）不用改。
PrivacyHit = TextGuardHit

__all__ = [
    "PRIVACY_POLICY_VERSION",
    "PrivacyHit",
    "is_safe",
    "rejection_reason",
    "scan",
]


def scan(text: str) -> list[PrivacyHit]:
    """扫一遍文本，返回所有命中（按出现位置排序）。

    返回空列表 = 没有命中凭据形态或高危标识，可以正常写入。
    """
    return scan_sensitive(text)


def is_safe(text: str) -> bool:
    """没有命中就是安全（可以写入）。"""
    return not scan_sensitive(text)


def rejection_reason(text: str) -> str:
    """给用户/模型看的拒绝理由；安全时返回空串。"""
    hits = scan_sensitive(text)
    if not hits:
        return ""
    labels = sorted({hit.label for hit in hits})
    listed = "、".join(labels)
    return (
        f"这条内容里有疑似{listed}，不能写进长期记忆："
        "长期记忆会进入每一轮的提示词，也会落到磁盘上，不适合放凭据。"
        f"请改写成不含具体值的一句说明（例如「用户配置了一个 {labels[0]}」），"
        f"或先让用户明确说不要记这一类内容。隐私策略 v{PRIVACY_POLICY_VERSION}。"
    )
