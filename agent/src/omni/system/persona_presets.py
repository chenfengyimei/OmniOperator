"""人设预设文件的版本化事务；不改变当前运行人格，不接触浏览器存储。"""

from __future__ import annotations

import hashlib
import json
import re
from pathlib import Path

from .atomic import atomic_write_text
from .filelock import FileLock
from .persona_rules import check_persona_name, check_persona_prompt


def read_presets(path: Path) -> dict:
    try:
        raw = path.read_bytes()
    except FileNotFoundError:
        return {"items": [], "revision": ""}
    data = json.loads(raw)
    if not isinstance(data, list):
        raise ValueError("人设预设存储格式损坏，请从备份恢复")
    return {"items": data, "revision": hashlib.sha256(raw).hexdigest()}


def save_presets(path: Path, items: list[dict], revision: str) -> dict:
    if len(items) > 100:
        raise ValueError("最多保存100套人设预设")
    cleaned, identities = [], set()
    for item in items:
        identity = item.get("id", "")
        if not re.fullmatch(r"[A-Za-z0-9_-]{1,80}", identity) or identity in identities:
            raise ValueError("预设编号无效或重复")
        identities.add(identity)
        check_persona_name(item.get("name", ""))
        check_persona_prompt(item.get("prompt", ""))
        cleaned.append({key: str(item.get(key, "")) for key in ("id", "name", "desc", "prompt")})
    path.parent.mkdir(parents=True, exist_ok=True)
    with FileLock(path.with_suffix(".lock")):
        if read_presets(path)["revision"] != revision:
            raise FileExistsError("预设已被其他页面修改，请刷新后重试")
        atomic_write_text(path, json.dumps(cleaned, ensure_ascii=False, indent=2) + "\n")
        return read_presets(path)
