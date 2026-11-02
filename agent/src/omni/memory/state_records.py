"""state.db 各域仓储返回值的数据形状（唯一来源）。

之前这些 dataclass 定义在 `state.py`；拆域 mixin 后由 state.py 与各域模块
共同单向依赖这里，避免循环 import。只承载数据形状，不做查询或事务。
"""

from __future__ import annotations

from dataclasses import dataclass

from .jobs import Job


@dataclass(frozen=True)
class BudgetHit:
    ok: bool
    used: int
    limit: int
    detail: str = ""


@dataclass(frozen=True)
class Claim:
    job: Job
    token: str
    overdue_s: float


@dataclass(frozen=True)
class Lease:
    """资源租约：谁占着这个资源（会话 / 浏览器 profile），什么时候自动失效。"""

    resource: str
    owner: str
    token: str
    acquired_at: str
    expires_at: float

    @property
    def session_id(self) -> int | None:
        """`session:<id>` 形态时返回会话 id（其它资源返回 None）。"""
        prefix, _, rest = self.resource.partition(":")
        return int(rest) if prefix == "session" and rest.isdigit() else None


SessionLease = Lease  # 兼容旧名


@dataclass(frozen=True)
class MigrationRecord:
    source: str
    status: str  # missing | empty | ok | corrupt | resolved_missing | failed
    sha256: str = ""
    imported: int = 0
    detail: str = ""
    applied_at: str = ""
