"""人设卡：社区分发的人设内容规格与收窄。

人设卡是**纯文本**（没有可执行内容，与插件包不是一类东西），但来源是网络，
仍然逐字段收窄后再交给界面：长度上限、类型校验、id 白名单字符——

- id 只允许 `[a-z0-9._-]`：它会拼进前端的预设键与 DOM 属性，不接受其它字符；
- `system_prompt` 直接进模型上下文，是唯一有"注入面"的字段，因此设硬上限
  并在界面里原样展示（用户导入前能看全文，不藏在折叠里）；
- 缺 id/name/system_prompt 的卡一律判无效，不做"猜一个默认值"的补救。

字段与 `config.Persona`（name + system_prompt）对齐：导入只是把这两项填进
表单，落盘仍由用户点「保存人设」走既有配置写入链。
"""

from __future__ import annotations

import re
from typing import Any

ID_RE = re.compile(r"^[a-z0-9][a-z0-9._-]{0,39}$")
VERSION_RE = re.compile(r"^[0-9A-Za-z][0-9A-Za-z.+-]{0,19}$")

MAX_NAME = 30
MAX_DESC = 200
MAX_PROMPT = 20000
MAX_GREETING = 120
MAX_TAGS = 6
MAX_TAG = 12
MAX_AUTHOR = 60
MAX_HOMEPAGE = 200


def _text(raw: Any, limit: int) -> str:
    if not isinstance(raw, str):
        return ""
    return raw.strip()[:limit]


def _tags(raw: Any) -> list[str]:
    if not isinstance(raw, list):
        return []
    out: list[str] = []
    for tag in raw:
        if isinstance(tag, str):
            tag = tag.strip()[:MAX_TAG]
            if tag and tag not in out:
                out.append(tag)
        if len(out) >= MAX_TAGS:
            break
    return out


def _homepage(raw: Any) -> str:
    url = _text(raw, MAX_HOMEPAGE)
    if not url:
        return ""
    return url if url.startswith(("http://", "https://")) else ""


def normalize(raw: Any) -> dict[str, Any] | None:
    """把一条索引/详情收窄成界面契约；不合格返回 None（调用方剔除该条）。"""
    if not isinstance(raw, dict):
        return None
    persona_id = _text(raw.get("id"), 40)
    name = _text(raw.get("name"), MAX_NAME)
    prompt = _text(raw.get("system_prompt"), MAX_PROMPT)
    if not ID_RE.match(persona_id) or not name or not prompt:
        return None
    version = _text(raw.get("version") or raw.get("latest_version"), 20)
    return {
        "id": persona_id,
        "name": name,
        "version": version if VERSION_RE.match(version) else "",
        "description": _text(raw.get("description"), MAX_DESC),
        "system_prompt": prompt,
        "greeting": _text(raw.get("greeting"), MAX_GREETING),
        "tags": _tags(raw.get("tags")),
        "author": _text(raw.get("author"), MAX_AUTHOR),
        "homepage": _homepage(raw.get("homepage")),
        "sha256": _text(raw.get("sha256") or raw.get("latest_sha256"), 64),
    }


def normalize_many(raw: Any) -> list[dict[str, Any]]:
    """收窄一个列表；非 dict 项与不合格项直接跳过（不报错，不让一条坏数据毁掉整页）。"""
    if not isinstance(raw, list):
        return []
    out: list[dict[str, Any]] = []
    for item in raw:
        card = normalize(item)
        if card is not None:
            out.append(card)
    return out
