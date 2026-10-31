"""POSIX 进程身份后端（P0 骨架）：Linux 只读身份走 /proc，身份核验失败一律拒绝授权终止。

macOS 无 /proc：inspect_process 返回 None（身份不可核验即不可停止），不回退裸 PID kill。
进程树读取 / 资源采样 / 已验证终止的 POSIX 实现待后续批次补全；缺位函数显式抛错，
不静默降级成不安全的近似行为。
"""
from __future__ import annotations

import os
from pathlib import Path

from .process_identity import ProcessIdentity, ProcessIdentityError


def inspect_process(pid: int) -> ProcessIdentity | None:
    """Linux 只读身份；其它系统无法核验即返回 None，不授权任何终止。"""
    if pid <= 0:
        return None
    try:
        stat = Path(f"/proc/{pid}/stat").read_text().rsplit(")", 1)[1].split()
        return ProcessIdentity(pid, stat[19], str(Path(f"/proc/{pid}/exe").resolve(strict=True)))
    except (OSError, IndexError):
        return None


def process_parents() -> dict[int, int]:
    raise ProcessIdentityError("POSIX 进程树读取后端尚未提供；当前无法核验后代身份")


def sample_process(identity: ProcessIdentity) -> dict:
    raise ProcessIdentityError("POSIX 资源采样后端尚未提供")


def confirmed_exited(identity: ProcessIdentity) -> bool:
    """不能把查询权限不足误报成已退出。"""
    try:
        os.kill(identity.pid, 0)
    except ProcessLookupError:
        return True
    except OSError:
        return False
    current = inspect_process(identity.pid)
    return current is not None and current != identity


def terminate_verified_tree(root: ProcessIdentity, *, timeout_s: float = 8) -> None:
    raise ProcessIdentityError("此平台暂无已验证的硬终止后端；请用服务优雅停机（停止文件）")
