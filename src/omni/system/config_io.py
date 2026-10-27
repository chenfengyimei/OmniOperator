"""config.yaml 安全读写：保留注释的定向修改（只动白名单键）。

工作台/工具改配置不允许整文件重写——注释和说明是人的上下文。
"""
from __future__ import annotations

import re
from pathlib import Path
from typing import Any

# 允许通过接口修改的键：(section, key) → 渲染方式
EDITABLE: dict[tuple[str, str], str] = {
    ("qq", "enabled"): "bool",
    ("qq", "allowed_users"): "int_list",
    ("qq", "reply_delay_s"): "float_list",
    ("browser", "headless"): "bool",
    ("browser", "max_writes_per_day"): "int",
    ("browser", "slow_mo_ms"): "int",
    ("review", "enabled"): "bool",
    ("review", "max_messages"): "int",
    ("asr", "model"): "str",
}


class ConfigEditError(ValueError):
    """配置修改不合法（未知键/值非法/找不到目标行）。"""


def render_value(kind: str, value: Any) -> str:
    if kind == "bool":
        if not isinstance(value, bool):
            raise ConfigEditError(f"需要布尔值，得到 {value!r}")
        return "true" if value else "false"
    if kind == "int":
        if isinstance(value, bool) or not isinstance(value, int):
            raise ConfigEditError(f"需要整数，得到 {value!r}")
        return str(value)
    if kind == "str":
        if not isinstance(value, str) or not value.strip():
            raise ConfigEditError(f"需要非空字符串，得到 {value!r}")
        return '"' + value.replace('"', '\\"') + '"'
    if kind == "int_list":
        if not isinstance(value, list) or not all(
            isinstance(x, int) and not isinstance(x, bool) for x in value
        ):
            raise ConfigEditError(f"需要整数列表，得到 {value!r}")
        return "[" + ", ".join(str(x) for x in value) + "]"
    if kind == "float_list":
        if not isinstance(value, list) or not all(
            isinstance(x, (int, float)) and not isinstance(x, bool) for x in value
        ) or len(value) != 2:
            raise ConfigEditError(f"需要 [下限, 上限] 两个数字，得到 {value!r}")
        return "[" + ", ".join(str(x) for x in value) + "]"
    raise ConfigEditError(f"未知字段类型 {kind}")


def apply_updates(text: str, updates: dict[str, dict[str, Any]]) -> str:
    """updates 形如 {"qq": {"enabled": True, "allowed_users": [1]}}；返回新文本。

    只替换 `  key: value` 的值部分，行内注释原样保留。
    """
    pending = {
        (section, key): value
        for section, keys in updates.items()
        for key, value in keys.items()
    }
    unknown = [k for k in pending if k not in EDITABLE]
    if unknown:
        raise ConfigEditError(f"不允许修改的键：{unknown}（白名单见 config_io.EDITABLE）")

    lines = text.splitlines(keepends=False)
    section = ""
    changed = 0
    for i, line in enumerate(lines):
        top = re.match(r"^([A-Za-z_][\w]*):\s*(#.*)?$", line)
        if top:
            section = top.group(1)
            continue
        m = re.match(r"^(\s+)([A-Za-z_][\w]*):(\s*)(.*?)(\s+#.*)?$", line)
        if not m:
            continue
        indent, key, _sp, _old, comment = m.groups()
        if (section, key) in pending:
            kind = EDITABLE[(section, key)]
            rendered = render_value(kind, pending[(section, key)])
            tail = comment or ""
            lines[i] = f"{indent}{key}: {rendered}{tail}"
            changed += 1

    missed = len(pending) - changed
    if missed:
        raise ConfigEditError(f"有 {missed} 个键在 config.yaml 里没找到对应行，未做任何破坏性改动")
    return "\n".join(lines) + "\n"


def safe_write(path: Path, new_text: str) -> None:
    """原子写：先写 .tmp 再替换。"""
    tmp = path.with_suffix(".yaml.tmp")
    tmp.write_text(new_text, encoding="utf-8")
    tmp.replace(path)
