"""shell 能力域：SAFE 观察（结构化 argv）与 CONFIRM 执行（shell 语义）。

模块分工：
- `spec.py`：命令串 → 受控 argv（解析失败即拒绝，唯一放行入口）；
- `process.py`：spawn / 有界读取 / 超时进程树回收 / 环境最小化；
- `danger.py`：灾难级硬黑名单（任何情况下都拒绝）；
- `tools.py`：工具契约与结果呈现。

对外的旧入口 `omni.tools.shell_tools` 保留为薄兼容层，导入路径不变。
"""
from __future__ import annotations

from .danger import hard_blocked_reason
from .process import ProcResult, minimal_env, run_argv, run_shell, user_env
from .spec import SAFE_COMMANDS, Rejected, SafeCommand, parse_safe_command
from .tools import (
    MAX_OUTPUT_CHARS,
    decode_output,
    register_shell_tools,
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
