"""工作区路径与敏感项的单一策略（D13）。

为什么单独抽一层：SAFE 观察（`tools/shell/spec.py`）和结构化文件工具（`tools/files/`）
必须用**同一份**敏感名单。两份各写一遍，迟早一份加了 `.npmrc`、另一份没加——
而"只读"同样可以把密钥读出来（A02 的另一半）。

明确不做：这里只判断"名字看起来是不是敏感"，不做路径规范化与根目录校验
（那需要知道工作区根，见 `tools/files/paths.py`）。
"""

from __future__ import annotations

import fnmatch

# 目录名命中即整棵子树不可作为模型输入
PROTECTED_DIR_NAMES = frozenset(
    {
        ".git",
        ".ssh",
        ".aws",
        ".gnupg",
        "data",  # 运行数据：会话、账本、个人记忆、日志
        "browser_profile",  # 浏览器登录态
        ".venv",
        "node_modules",  # 体量大且无阅读价值（内含大量第三方密钥样例）
        "private",  # 包括迁出 data/ 后的 temp/private 私人归档
    }
)

# 文件名（小写）命中即不可读
PROTECTED_FILE_GLOBS = (
    ".env",
    ".env.*",
    "*.pem",
    "*.key",
    "*.pfx",
    "*.p12",
    "*.kdbx",
    "id_rsa*",
    "id_ed25519*",
    "id_ecdsa*",
    "*.ppk",
    "*.db",
    "*.db-wal",
    "*.db-shm",
    "*.sqlite",
    "*.sqlite3",
    "*cookie*.json",
    "*credential*",
    "*secret*",
    ".git-credentials",
    ".netrc",
    ".npmrc",
    ".pypirc",
)

_SENSITIVE_HINT = "属于密钥/凭据/个人数据类路径"


def is_protected_name(name: str) -> bool:
    """文件名是否命中敏感名单（大小写不敏感）。"""
    lowered = str(name or "").lower()
    return any(fnmatch.fnmatch(lowered, pattern) for pattern in PROTECTED_FILE_GLOBS)


def protected_dir(parts) -> str:
    """路径段里是否含受保护目录；返回命中的目录名，否则空串。"""
    for part in parts:
        lowered = str(part).lower()
        if lowered in PROTECTED_DIR_NAMES:
            return lowered
    return ""


def protected_reason(parts, name: str) -> str:
    """命中敏感项返回可读理由（不含绝对路径），否则空串。

    理由里刻意不写完整路径：拒绝信息也会进模型上下文，没必要把机器目录结构带进去。
    """
    directory = protected_dir(parts)
    if directory:
        return f"路径包含受保护目录 {directory}/（{_SENSITIVE_HINT}）"
    if is_protected_name(name):
        return f"{name} {_SENSITIVE_HINT}，默认不作为模型输入"
    return ""
