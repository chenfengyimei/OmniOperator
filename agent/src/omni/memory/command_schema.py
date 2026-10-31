"""命令账本声明的旧 import 兼容层；真实声明见 `memory/state_schema.py`。"""
from __future__ import annotations

from .state_schema import COMMAND_JOBS_SCHEMA

__all__ = ["COMMAND_JOBS_SCHEMA"]
