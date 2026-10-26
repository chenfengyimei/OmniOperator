"""B站 WBI 签名（纯算法，无网络依赖）。

算法与常量表来自社区公开规范 bilibili-API-collect；端点用法参考
xuexi/dsh-bili-whale（见 xuexi/NOTES.md §4）。WBI 是B站 web 接口的
防刷参数签名：带 /wbi/ 的端点要求 wts + w_rid，否则返回 -403。
"""
from __future__ import annotations

import time
from hashlib import md5
from urllib.parse import quote, urlencode

# 打乱顺序表（64 项，社区规范公开常量；索引取自 img_key+sub_key 拼接串）
MIXIN_KEY_ENC_TAB = [
    46, 47, 18, 2, 53, 8, 23, 32, 15, 50, 10, 31, 58, 3, 45, 35, 27, 43,
    5, 49, 33, 9, 42, 19, 29, 28, 14, 39, 12, 38, 41, 13, 37, 48, 7, 16,
    24, 55, 40, 61, 26, 17, 0, 1, 60, 51, 30, 4, 22, 25, 54, 21, 56, 59,
    6, 63, 57, 62, 11, 36, 20, 34, 44, 52,
]

# 规范要求把值里的这些字符剔除后再编码
_CHARS_TO_STRIP = "!'()*"


def get_mixin_key(img_key: str, sub_key: str) -> str:
    """img_key + sub_key（各32字符）按打乱表重排，取前 32 位。"""
    raw = img_key + sub_key
    return "".join(raw[i] for i in MIXIN_KEY_ENC_TAB)[:32]


def sign_wbi(
    params: dict[str, str | int], img_key: str, sub_key: str
) -> dict[str, str | int]:
    """给请求参数加 wts + w_rid，返回可直接发给接口的完整参数集。"""
    signed: dict[str, str | int] = {k: v for k, v in sorted(params.items())}
    signed["wts"] = int(time.time())
    filtered = {
        k: str(v).translate({ord(c): None for c in _CHARS_TO_STRIP})
        for k, v in signed.items()
    }
    query = urlencode(filtered, quote_via=quote)
    signed["w_rid"] = md5((query + get_mixin_key(img_key, sub_key)).encode()).hexdigest()
    return signed
