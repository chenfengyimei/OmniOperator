"""config.yaml 安全读写：保留注释的定向修改（只动白名单键）。

工作台/工具改配置不允许整文件重写——注释和说明是人的上下文。
支持三种替换：标量行值、多行块（key: |）、以及按行追加校验。
"""
from __future__ import annotations

import os
import re
import json
from pathlib import Path
from typing import Any

from .atomic import atomic_write_text

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
    ("qq", "group_reply_to_bot"): "bool",
    ("qq", "proactive_enabled"): "bool",
    ("qq", "owner_qq"): "int",
    ("qq", "proactive_daily_limit"): "int",
    ("qq", "quiet_start"): "str",
    ("qq", "quiet_end"): "str",
    ("qq", "timezone"): "str",
    ("browser", "headless"): "bool",
    ("browser", "slow_mo_ms"): "int",
    ("browser", "max_writes_per_day"): "int",
    ("browser", "approval_timeout_s"): "float",
    ("approval", "mode"): "choice:manual,smart",
    # 记忆系统（M3–M6）的整理与限额：界面在「记忆系统 → 整理设置」
    ("journal", "enabled"): "bool",
    ("journal", "daily_time"): "str",
    ("journal", "steps"): "int",
    ("journal", "max_catchup"): "int",
    ("journal", "max_input_chars"): "int",
    ("journal", "max_note_chars"): "int",
    ("journal", "daily_prompt"): "multiline",
    ("journal", "weekly_prompt"): "multiline",
    ("journal", "monthly_prompt"): "multiline",
    ("transcript", "max_entry_chars"): "int",
    ("transcript", "max_tool_args_chars"): "int",
    ("transcript", "max_tool_result_chars"): "int",
    ("transcript", "retention_days"): "int",
    ("transcript", "dialogue_retention_days"): "int",
    ("people", "person_card_chars"): "int",
    ("people", "archive_max_chars"): "int",
    ("people", "resident_max_chars"): "int",
    ("people", "event_max_chars"): "int",
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
        return json.dumps(value, ensure_ascii=False)
    if kind == "str_or_empty":
        if not isinstance(value, str):
            raise ConfigEditError(f"需要字符串，得到 {value!r}")
        return json.dumps(value, ensure_ascii=False)
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
        # 保留小数形态：1.0 不能写成 1，否则每次保存都把配置里的写法改一遍
        return "[" + ", ".join(str(float(x)) for x in value) + "]"
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


def _find_multiline_block(
    lines: list[str], section: str, key: str
) -> tuple[int, int, int] | None:
    """定位 `key: |` 块，返回 (键行下标, 块内容起, 块内容止)；找不到返回 None。"""
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
        # 块尾的空行属于节间分隔，不该被吃掉
        while j - 1 > i and not lines[j - 1].strip():
            j -= 1
        return i, i + 1, j
    return None


def _replace_multiline_block(
    text: str, section: str, key: str, value_lines: list[str]
) -> str:
    """替换 `key: |` 多行块的内容（保留键行与缩进），找不到返回原文本。"""
    lines = text.splitlines(keepends=False)
    hit = _find_multiline_block(lines, section, key)
    if hit is None:
        return text
    i, _, end = hit
    key_indent = len(lines[i]) - len(lines[i].lstrip())
    # lines[:i] 已经不含键行——必须从 i 开始切。
    # 用 lines[:i+1] 会把 `key: |` 重复写一遍，于是每保存一次文件就多一行空块。
    new_block = [f"{' ' * key_indent}{key}: |"] + [
        f"{' ' * (key_indent + 2)}{ln}" for ln in value_lines
    ]
    return "\n".join(lines[:i] + new_block + lines[end:]) + "\n"


def _section_bounds(lines: list[str], section: str) -> tuple[int, int] | None:
    """返回某顶层节的 (起行, 止行)；该节不存在返回 None。"""
    head = re.compile(rf"^{re.escape(section)}:\s*(#.*)?$")
    start = next((i for i, line in enumerate(lines) if head.match(line)), None)
    if start is None:
        return None
    end = next(
        (i for i in range(start + 1, len(lines)) if re.match(r"^[A-Za-z_]\w*:", lines[i])),
        len(lines),
    )
    return start, end


def _has_key(lines: list[str], start: int, end: int, key: str) -> bool:
    pat = re.compile(rf"^\s+{re.escape(key)}:")
    return any(pat.match(lines[i]) for i in range(start + 1, end))


def _append_key(lines: list[str], section: str, key: str, rendered: str) -> None:
    """把键补进它的节；节不存在就整节补在文件末尾。

    为什么允许补：`EDITABLE` 已经是白名单，能走到这里的键一定是界面暴露过的合法键。
    原先"找不到对应行就整体拒绝"会让**升级后新增的配置项**把整次保存打死
    （典型：config.yaml 里没有 approval 节，于是"保存全部设置"永远失败），
    而界面上只会看到一句和真实原因无关的报错。
    """
    bounds = _section_bounds(lines, section)
    if bounds is None:
        if lines and lines[-1].strip():
            lines.append("")
        lines.append(f"{section}:")
        lines.append(f"  {key}: {rendered}")
        return
    start, end = bounds
    if _has_key(lines, start, end, key):
        return
    insert_at = end
    while insert_at - 1 > start and not lines[insert_at - 1].strip():
        insert_at -= 1  # 别插到节尾空行之后，否则看起来像下一节的
    lines.insert(insert_at, f"  {key}: {rendered}")


def _append_multiline_block(
    text: str, section: str, key: str, value_lines: list[str]
) -> str:
    """`key: |` 整块不存在时补一个（节不存在就连节一起建）。"""
    lines = text.splitlines()
    bounds = _section_bounds(lines, section)
    if bounds is None:
        if lines and lines[-1].strip():
            lines.append("")
        lines.append(f"{section}:")
        start = len(lines) - 1
        insert_at = len(lines)
    else:
        start, insert_at = bounds
        while insert_at - 1 > start and not lines[insert_at - 1].strip():
            insert_at -= 1
    block = [f"  {key}: |"] + [f"    {ln}" for ln in value_lines]
    lines[insert_at:insert_at] = block
    return "\n".join(lines) + "\n"


def apply_updates(text: str, updates: dict[str, dict[str, Any]]) -> str:
    """updates 形如 {"qq": {"enabled": True, "allowed_users": [1]}}；返回新文本。

    标量键只替换 `  key: value` 的值部分，行内注释原样保留；
    multiline 键（如 persona.system_prompt）整块替换。
    节里没有的键会被补进去（整个节都没有就在文件末尾新建一节）。
    """
    crlf = "\r\n" in text
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
        if EDITABLE[(section, key)] != "multiline":
            continue
        if not isinstance(value, list) or not all(isinstance(x, str) for x in value):
            raise ConfigEditError(f"{section}.{key} 需要字符串列表（每行一项）")
        if _find_multiline_block(text.splitlines(), section, key) is None:
            # 块根本不存在：不能静默丢内容，整块补进节里（节都没有就新建）
            text = _append_multiline_block(text, section, key, value)
        else:
            text = _replace_multiline_block(text, section, key, value)
        del pending[(section, key)]

    # 缺失的标量键先补齐，再统一走替换
    lines = text.splitlines()
    for (section, key), value in pending.items():
        bounds = _section_bounds(lines, section)
        if bounds is not None and _has_key(lines, *bounds, key):
            continue
        _append_key(lines, section, key, render_value(EDITABLE[(section, key)], value))
    text, changed = _scalar_updates("\n".join(lines) + "\n", pending)

    missed = len(pending) - changed
    if missed:
        raise ConfigEditError(f"有 {missed} 个键写入失败，未做任何破坏性改动")
    # 换行符跟着原文件走：Windows 上 config.yaml 是 CRLF，内部一律用 \n 处理，
    # 不还原的话保存一次就把整个文件改成 LF，git diff 会显示"全文件都改了"。
    if crlf:
        text = text.replace("\r\n", "\n").replace("\n", "\r\n")
    return text


def read_config_text(path: Path) -> str:
    """读配置原文，**保留原换行符**。

    `Path.read_text()` 走文本通用换行模式，CRLF 会在读的时候就被静默改成 LF；
    再经 apply_updates 写回，整个文件的行尾就变了——git diff 会显示"全文件都改了"。
    这里显式 `newline=""` 保住原始形态。
    """
    with open(path, "r", encoding="utf-8", newline="") as fh:
        return fh.read()


def safe_write(path: Path, new_text: str) -> None:
    """原子写：同目录唯一临时文件 + fsync + 替换（固定 `.tmp` 名字并发时会互相覆盖）。"""
    atomic_write_text(path, new_text)


# ---------- .env 密钥：工作台"选供应商 → 填 API Key"的落地点 ----------
#
# 为什么要有：以前工作台把 Key 存在浏览器 localStorage，后端从来读不到——
# 测试和真实调用用的都是 .env 里的旧值，等于填了个心理安慰。这一段负责
# 把 Key 真正写进 .env，并立刻注入进程环境，让下一次模型调用就能用上。
#
# 边界：只动密钥形状的变量名；只替换/追加那一行；注释与顺序原样保留；
# 接口永远不回明文，只回脱敏值。

_ENV_NAME_RE = re.compile(r"^[A-Z][A-Z0-9_]{2,63}$")
_ENV_SECRET_SUFFIXES = ("_API_KEY", "_KEY", "_TOKEN")
_ENV_MAX_LEN = 512


def mask_secret(value: str) -> str:
    """脱敏展示：前 4 + 省略号 + 后 4；太短的值只给等长星号。"""
    value = (value or "").strip()
    if not value:
        return ""
    if len(value) <= 10:
        return "*" * len(value)
    return f"{value[:4]}…{value[-4:]}"


def _env_name_ok(name: str) -> bool:
    return bool(_ENV_NAME_RE.match(name)) and name.endswith(_ENV_SECRET_SUFFIXES)


def read_env_secrets(path: Path) -> dict[str, dict[str, Any]]:
    """读 .env，回 {"KEY_NAME": {"configured": bool, "masked": "sk-6…3b9c"}}。

    只回密钥形状的变量名；值一律脱敏，明文不出后端。
    """
    result: dict[str, dict[str, Any]] = {}
    try:
        text = Path(path).read_text(encoding="utf-8")
    except OSError:
        return result
    for line in text.splitlines():
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        name, _, raw = line.partition("=")
        name = name.strip()
        if not _env_name_ok(name):
            continue
        value = raw.strip().strip('"').strip("'")
        result[name] = {"configured": bool(value), "masked": mask_secret(value)}
    return result


def set_env_secret(path: Path, name: str, value: str | None) -> str:
    """把密钥写进 .env（按行定向替换，没有该行就追加），返回脱敏值。

    - 只认 `X_API_KEY` / `X_KEY` / `X_TOKEN` 形状的变量名，其它一律拒绝。
    - `value` 为空 → 该行写成空值（等于清除），但不删行：保留"这里该放什么"的位置。
    - 拒绝换行与空字节：一条 Key 不能变成多行把后面的变量顶掉。
    - 写完立即更新 `os.environ`，因为 `load_env` 尊重"已存在的非空变量优先"，
      不显式覆盖的话新 Key 要等重启才生效。
    """
    name = (name or "").strip()
    if not _env_name_ok(name):
        raise ConfigEditError(
            "密钥变量名不合法：只允许 A-Z / 0-9 / _，且以 _API_KEY、_KEY 或 _TOKEN 结尾"
        )
    raw = (value or "").strip()
    if "\n" in raw or "\r" in raw or "\0" in raw:
        raise ConfigEditError("密钥里不能有换行或空字节")
    if len(raw) > _ENV_MAX_LEN:
        raise ConfigEditError(f"密钥过长（>{_ENV_MAX_LEN} 字符），确认是不是粘多了内容")
    if raw and (" " in raw or "\t" in raw):
        raise ConfigEditError("密钥里有空格，可能连说明文字一起粘进来了")

    env_path = Path(path)
    try:
        lines = env_path.read_text(encoding="utf-8").splitlines()
    except OSError:
        lines = []

    replaced = False
    for index, line in enumerate(lines):
        stripped = line.strip()
        if not stripped or stripped.startswith("#") or "=" not in stripped:
            continue
        if stripped.partition("=")[0].strip() != name:
            continue
        lines[index] = f"{name}={raw}"
        replaced = True
        break
    if not replaced:
        lines.append(f"{name}={raw}")

    atomic_write_text(env_path, "\n".join(lines) + "\n")
    if raw:
        os.environ[name] = raw
    else:
        os.environ.pop(name, None)
    return mask_secret(raw)
