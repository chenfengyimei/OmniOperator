"""跨域共享的路径安全原语：zip/目录条目名规范化与内容摘要。

`plugins/package_reader.py`（插件包）与 `memory/skill_inbox.py`（外来技能包）
曾各持一份逐字相同的 `_safe_parts`/`_digest` 实现——同一份安全逻辑存在两个
副本，改一处漏一处的风险真实存在（历史教训：登记项「重复定义扫描」）。
现在两域都消费本模块的同一实现。

边界：本模块只有**纯函数**，不读文件系统状态、不执行包内容、不做白名单
判定（各域的条目形状白名单不同，留在各自域内：plugins 的 entry_shape 是
manifest+唯一入口，skills 的是 SKILL.md+references）。
"""

from __future__ import annotations

import hashlib
from pathlib import PureWindowsPath


def digest(data: bytes) -> str:
    """字节内容的 sha256 hex（指纹/防篡改统一口径，按字节算不按文本算）。"""
    return hashlib.sha256(data).hexdigest()


def safe_parts(entry: str) -> tuple[str, ...] | None:
    """zip 条目名 → 规范化相对部件；越界名拒绝。

    Windows 与 POSIX 语法各查一遍：宿主是 Windows，但 zip 里的名字可以
    用任意一种语法表达越界。拒绝：绝对路径、`..`、盘符、ADS 冒号、NUL、
    尾随空格/点、Windows 保留名（CON/NUL/...）。
    """
    name = (entry or "").strip().replace("\\", "/")
    if not name or name.startswith("/"):
        return None
    if PureWindowsPath(entry).drive or PureWindowsPath(entry).is_absolute():
        return None
    parts = name.split("/")
    for part in parts:
        if (
            part in {"", ".", ".."}
            or part.endswith((" ", "."))
            or ":" in part
            or "\x00" in part
            or PureWindowsPath(part).is_reserved()
        ):
            return None
    return tuple(parts)
