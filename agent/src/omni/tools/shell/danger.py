"""硬性安全黑名单：灾难级操作命中即拒——即便用户已确认也不执行。

为什么保留在 shell 能力域内（与 SAFE 白名单分开）：
- SAFE 白名单是"什么可以免确认"，黑名单是"什么任何情况下都不做"；
- 确认可以授权"有风险"，不能授权"灾难"（递归删根、格式化、远程内容进 shell…）。
"""

from __future__ import annotations

import re

# 清单刻意精炼为六类，避免误伤正常开发操作（git reset --hard、del 临时文件等不拦）
_HARD_BLOCKED_RES = [
    re.compile(p, re.IGNORECASE)
    for p in (
        # 1. 递归强删根/用户目录（POSIX rm 删除独立的 / 或 ~；Windows 盘根整删）
        r"\brm\s+(?:-\w+\s+)*(?:--\s+)?(?<![\w./~-])(?:/|~)(?=\s|$)",
        r"\b(rd|del|rmdir)\s+/s\s+/q\s+[a-z]:\\?\s*$",
        r"remove-item\s+.*-recurse.*-force\s+[a-z]:\\\s*$",
        # 2. 裸盘 / 文件系统 / 分区
        r"\bdd\b[^|;]*\bof=/dev/",
        r"\bmkfs\b|\bwipefs\b|\bfdisk\b|\bdiskpart\b|\bformat\s+[a-z]:",
        # 3. fork bomb（bash 与 cmd 形态）
        r":\(\)\s*\{[^}]*\};*:",
        r"%0\s*\|\s*%0",
        # 4. 远程内容直接进 shell 执行
        r"(curl|wget)\b[^|]*\|\s*(sudo\s+)?(ba|z|fi|da)?sh\b",
        r"\b(iex|invoke-expression)\b[^|]*\b(iwr|invoke-webrequest|curl|new-object\s+system\.net)",
        # 5. 主机电源控制
        r"\b(shutdown|poweroff|halt)\b(\.exe)?\s",
        # 6. SSH 私钥外读
        r"(type|get-content|cat)\b[^|;]*(id_rsa|id_ed25519|id_ecdsa)\b",
    )
]


def hard_blocked_reason(command: str) -> str:
    """命中硬黑名单返回拒绝理由，否则空串（唯一检查入口）。"""
    for pattern in _HARD_BLOCKED_RES:
        match = pattern.search(command)
        if match:
            return (
                f"命中硬性安全黑名单（{match.group(0)[:60]!r}）：该类操作即使获得确认也"
                "不会执行——确认可以授权'有风险'，不能授权'灾难'。请改用安全等价操作"
            )
    return ""
