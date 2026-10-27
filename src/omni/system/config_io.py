"""config.yaml 安全读写：保留注释的定向修改（只动白名单键）。

工作台/工具改配置不允许整文件重写——注释和说明是人的上下文。
支持三种替换：标量行值、多行块（key: |）、以及按行追加校验。
"""
from __future__ import annotations

import re
from pathlib import Path
from typing import Any

# 允许通过接口修改的键：(section, key) → 渲染方式
EDITABLE: dict[tuple[str, str], str] = {
    ("persona", "name"): "str",
    ("persona", "system_prompt"): "multiline",
    ("models", "chat"): "str",
    ("models", "vision"): "str",
    ("models", "review"): "str_or_empty",
    ("limits", "max_history_messages"): "int",
    ("limits", "max_agent_steps"): "int",
    ("review", "enabled"): "bool",
    ("review", "max_messages"): "int",
    ("review", "steps"): "int",
    ("asr", "model"): "str",
    ("asr", "device"): "str",
    ("asr", "compute_type"): "str",
    ("qq", "enabled"): "bool",
    ("qq", "ws_url"): "str",
    ("qq", "access_token"): "str_or_empty",
    ("qq", "allowed_users"): "int_list",
    ("qq", "allowed_groups"): "int_list",
    ("qq", "reply_delay_s"): "float_list",
    ("browser", "headless"): "bool",
    ("browser", "slow_mo_ms"): "int",
    ("browser", "max_writes_per_day"): "int",
    ("browser", "approval_timeout_s"): "float",
    ("approval", "mode"): "choice:manual,smart",
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
    if kind == "float":
        if isinstance(value, bool) or not isinstance(value, (int, float)):
            raise ConfigEditError(f"需要数字，得到 {value!r}")
        return str(value)
    if kind == "str":
        if not isinstance(value, str) or not value.strip():
            raise ConfigEditError(f"需要非空字符串，得到 {value!r}")
        return '"' + value.replace('"', '\\"') + '"'
    if kind == "str_or_empty":
        if not isinstance(value, str):
            raise ConfigEditError(f"需要字符串，得到 {value!r}")
        return '"' + value.replace('"', '\\"') + '"'
    if kind.startswith("choice:"):
        allowed = kind.split(":", 1)[1].split(",")
        if value not in allowed:
            raise ConfigEditError(f"需要 {'/'.join(allowed)} 之一，得到 {value!r}")
        return '"' + str(value) + '"'
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


def _scalar_updates(text: str, updates: dict[tuple[str, str], Any]) -> tuple[str, int]:
    """标量替换（key: value 行），返回 (新文本, 替换数)。multiline 键跳过。"""
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
        if (section, key) in updates:
            kind = EDITABLE[(section, key)]
            if kind == "multiline":
                continue  # 多行块走专门替换
            rendered = render_value(kind, updates[(section, key)])
            tail = comment or ""
            lines[i] = f"{indent}{key}: {rendered}{tail}"
            changed += 1
    return "\n".join(lines) + "\n", changed


def _replace_multiline_block(
    text: str, section: str, key: str, value_lines: list[str]
) -> str:
    """替换 `key: |` 多行块的内容（保留键行与缩进），找不到返回原文本。"""
    lines = text.splitlines(keepends=False)
    in_section = False
    for i, line in enumerate(lines):
        top = re.match(r"^([A-Za-z_][\w]*):\s*(#.*)?$", line)
        if top:
            in_section = top.group(1) == section
            continue
        m = re.match(r"^(\s+)([A-Za-z_][\w]*):\s*\|\s*(#.*)?$", line)
        if not (in_section and m and m.group(2) == key):
            continue
        key_indent = len(m.group(1))
        j = i + 1
        while j < len(lines):
            nxt = lines[j]
            if not nxt.strip():
                j += 1
                continue
            if len(nxt) - len(nxt.lstrip()) <= key_indent:
                break  # 块结束（缩进回到同级或更浅）
            j += 1
        new_block = [f"{' ' * key_indent}{key}: |"] + [
            f"{' ' * (key_indent + 2)}{ln}" for ln in value_lines
        ]
        return "\n".join(lines[: i + 1] + new_block + lines[j:]) + "\n"
    return text


def apply_updates(text: str, updates: dict[str, dict[str, Any]]) -> str:
    """updates 形如 {"qq": {"enabled": True, "allowed_users": [1]}}；返回新文本。

    标量键只替换 `  key: value` 的值部分，行内注释原样保留；
    multiline 键（如 persona.system_prompt）整块替换。
    """
    pending = {
        (section, key): value
        for section, keys in updates.items()
        for key, value in keys.items()
    }
    unknown = [k for k in pending if k not in EDITABLE]
    if unknown:
        raise ConfigEditError(f"不允许修改的键：{unknown}（白名单见 config_io.EDITABLE）")

    # 多行块先替换
    for (section, key), value in list(pending.items()):
        if EDITABLE[(section, key)] == "multiline":
            if not isinstance(value, list) or not all(
                isinstance(x, str) for x in value
            ):
                raise ConfigEditError(f"{section}.{key} 需要字符串列表（每行一项）")
            text = _replace_multiline_block(text, section, key, value)
            del pending[(section, key)]

    text, changed = _scalar_updates(text, pending)

    missed = len(pending) - changed
    if missed:
        raise ConfigEditError(f"有 {missed} 个键在 config.yaml 里没找到对应行，未做任何破坏性改动")
    return text


def safe_write(path: Path, new_text: str) -> None:
    """原子写：先写 .tmp 再替换。"""
    tmp = path.with_suffix(".yaml.tmp")
    tmp.write_text(new_text, encoding="utf-8")
    tmp.replace(path)
