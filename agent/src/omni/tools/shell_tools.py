"""兼容薄层：旧的 `omni.tools.shell_tools` 导入路径继续可用。

实现已拆到 `tools/shell/` 包（spec/process/danger/tools）；本文件只做转发，
不新增逻辑——避免出现"两套相互矛盾的放行判断"。
"""
from __future__ import annotations

from .shell import (
    MAX_OUTPUT_CHARS,
    SAFE_COMMANDS,
    ProcResult,
    Rejected,
    SafeCommand,
    decode_output,
    hard_blocked_reason,
    minimal_env,
    parse_safe_command,
    register_shell_tools,
    run_argv,
    run_shell,
    user_env,
)

__all__ = [
    "MAX_OUTPUT_CHARS",
    "ProcResult",
    "Rejected",
    "SAFE_COMMANDS",
    "SafeCommand",
    "decode_output",
    "hard_blocked_reason",
    "minimal_env",
    "parse_safe_command",
    "register_shell_tools",
    "run_argv",
    "run_shell",
    "user_env",
]
