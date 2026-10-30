"""工作台终端公共入口；平台适配、会话、会话池和 WS 路由分别维护。"""
from .backend import DEFAULT_COLS, DEFAULT_ROWS, MAX_SESSIONS, SHELL_COMMAND
from .pool import TerminalManager
from .session import TerminalSession

__all__ = ["TerminalManager", "TerminalSession", "DEFAULT_COLS", "DEFAULT_ROWS", "MAX_SESSIONS", "SHELL_COMMAND"]
