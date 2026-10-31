"""进程身份与已验证终止的公共协议与平台分派；不以裸 PID 或端口身份授权终止。

后端按平台选择：Windows 走 winproc.py（Win32 保留句柄核验语义），POSIX 走 posixproc.py。
后端模块只在运行期按 os.name 惰性导入——winproc 顶层 import ctypes.wintypes 在
POSIX 上会直接失败，惰性分派保证非 Windows 代码路径永不触碰它。

公共保证（所有后端一致）：
- 创建时间用整数表示，避免浮点精度；查询和终止使用同一份核验过的身份。
- 进程树仅处理已观察、身份可核对的后代；无法核验时报告残留，不扩大到端口进程。
"""
from __future__ import annotations

import os
from dataclasses import dataclass


@dataclass(frozen=True)
class ProcessIdentity:
    pid: int
    created: str
    executable: str


class ProcessIdentityError(RuntimeError):
    pass


def _backend():
    """按平台选择后端模块；仅此处分派，业务层不感知平台。"""
    if os.name == "nt":
        from . import winproc
        return winproc
    from . import posixproc
    return posixproc


def inspect_process(pid: int) -> ProcessIdentity | None:
    return _backend().inspect_process(pid)


def process_parents() -> dict[int, int]:
    return _backend().process_parents()


def sample_process(identity: ProcessIdentity) -> dict:
    return _backend().sample_process(identity)


def confirmed_exited(identity: ProcessIdentity) -> bool:
    return _backend().confirmed_exited(identity)


def terminate_verified_tree(root: ProcessIdentity, *, timeout_s: float = 8) -> None:
    _backend().terminate_verified_tree(root, timeout_s=timeout_s)
