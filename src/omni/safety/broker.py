"""审批代理：跨线程确认流（Web 工作台用）。

机制：agent 跑在请求线程里（工具派发时阻塞在 Event.wait），
用户在浏览器里点同意/拒绝 → HTTP 端点 resolve → Event 置位 → 工具继续。
超时自动拒绝（fail-closed），绝不无限等待。
"""
from __future__ import annotations

import datetime as dt
import threading
import uuid
from dataclasses import dataclass, field
from typing import Any

from .gates import Decision


@dataclass
class PendingApproval:
    id: str
    tool_name: str
    description: str
    risk: str
    created_at: str
    detail: str = ""  # 本次调用的具体参数（供用户判断）
    event: threading.Event = field(default_factory=threading.Event)
    allowed: bool | None = None
    reason: str = ""


class ApprovalBroker:
    def __init__(self, timeout_s: float = 180.0) -> None:
        self._timeout = timeout_s
        self._lock = threading.Lock()
        self._pending: dict[str, PendingApproval] = {}

    def request(self, tool: Any, detail: str = "", timeout: float | None = None) -> Decision:
        """阻塞等待用户的批准决定（在 agent 线程里调用）。"""
        pending = PendingApproval(
            id=uuid.uuid4().hex[:8],
            tool_name=str(getattr(tool, "name", "unknown")),
            description=str(getattr(tool, "description", "")),
            risk=str(getattr(getattr(tool, "risk", ""), "value", "confirm")),
            created_at=dt.datetime.now().astimezone().isoformat(timespec="seconds"),
            detail=detail,
        )
        with self._lock:
            self._pending[pending.id] = pending
        ok = pending.event.wait(timeout if timeout is not None else self._timeout)
        with self._lock:
            self._pending.pop(pending.id, None)
        if not ok:
            return Decision(False, "确认超时（3 分钟无人处理），已自动拒绝")
        if pending.allowed:
            return Decision(True)
        return Decision(False, pending.reason or "用户拒绝了该操作")

    def resolve(self, approval_id: str, allowed: bool, reason: str = "") -> bool:
        """HTTP 端点调用：对某个待确认项做出决定；不存在（已超时）返回 False。"""
        with self._lock:
            pending = self._pending.get(approval_id)
            if pending is None:
                return False
            pending.allowed = bool(allowed)
            pending.reason = reason
            pending.event.set()
        return True

    def list_pending(self) -> list[dict[str, str]]:
        with self._lock:
            return [
                {
                    "id": p.id,
                    "tool_name": p.tool_name,
                    "description": p.description,
                    "detail": p.detail,
                    "risk": p.risk,
                    "created_at": p.created_at,
                }
                for p in self._pending.values()
            ]
