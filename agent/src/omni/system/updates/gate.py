"""安装级维护闸门和运行入口租约；只管理协议文件，不构造或终止业务对象。"""

from __future__ import annotations

import contextvars
import os
import threading
import uuid
from contextlib import contextmanager
from pathlib import Path

from ..filelock import FileLock, LockTimeout
from .common import UpdateError, read_json, remove_file, write_json


class MaintenanceGate:
    def __init__(self, root: Path):
        self.root = root
        self.path = root / "maintenance.json"
        self.id = uuid.uuid4().hex
        self.lease = None
        self.busy = 0
        self._mutex = threading.Lock()
        self._depth = contextvars.ContextVar("update_admission_" + self.id, default=0)

    @contextmanager
    def work(self, *, allow_drain: bool = False):
        with FileLock(self.root / "registry.lock"):
            if self.active() and not allow_drain and not self._depth.get():
                raise UpdateError("maintenance", "系统正在升级，暂时不接收新操作")
            with self._mutex:
                self.busy += 1
        token = self._depth.set(self._depth.get() + 1)
        try:
            yield
        finally:
            self._depth.reset(token)
            with self._mutex:
                self.busy -= 1

    def accepting(self) -> bool:
        return bool(self._depth.get()) or not self.active()

    def state(self) -> dict:
        data = read_json(self.path, {})
        if not isinstance(data, dict):
            raise UpdateError("state_invalid", "维护状态格式无效，请先恢复")
        return data

    def active(self) -> bool:
        return bool(self.state())

    def enter(self, kind: str, *, probe_token: str = "") -> None:
        with FileLock(self.root / "registry.lock"):
            state = self.state()
            if state and (not probe_token or probe_token != state.get("probe_token")):
                raise UpdateError("maintenance", "安装正在升级或等待恢复，请通过 start.bat 恢复")
            self.lease = FileLock(self.root / "leases" / (self.id + ".lock"), owner=f"{kind}:{os.getpid()}")
            self.lease.acquire()
            write_json(
                self.root / "leases" / (self.id + ".json"), {"id": self.id, "pid": os.getpid(), "kind": kind, "busy": 1}
            )

    def report(self, busy: int) -> None:
        if self.lease:
            path = self.root / "leases" / (self.id + ".json")
            value = read_json(path, {})
            value["busy"] = busy
            write_json(path, value)

    def peers(self) -> list[dict]:
        rows = []
        for path in (self.root / "leases").glob("*.json"):
            lock = FileLock(path.with_suffix(".lock"), timeout_s=0)
            try:
                lock.acquire()
            except LockTimeout:
                row = read_json(path)
                if row is not None:
                    if not row.get("id") or not row.get("pid"):
                        raise UpdateError("state_invalid", "入口租约损坏，无法确认安装是否空闲")
                    rows.append(row)
                else:
                    # 所有者可能刚释放锁并删除记录，再确认一次，避免把退出中的入口当作损坏。
                    check = FileLock(path.with_suffix(".lock"), timeout_s=0)
                    try:
                        check.acquire()
                    except LockTimeout as exc:
                        raise UpdateError("state_invalid", "入口仍持锁但缺少记录，未执行升级") from exc
                    else:
                        check.release()
            else:
                lock.release()
        return rows

    def begin(self, value: dict) -> None:
        with FileLock(self.root / "registry.lock"):
            if self.active():
                raise UpdateError("update_busy", "已有安装级升级事务")
            write_json(self.path, value)

    def clear(self, identifier: str) -> None:
        with FileLock(self.root / "registry.lock"):
            state = self.state()
            if state and state.get("operation") != identifier:
                raise UpdateError("operation_changed", "维护状态属于其他事务，未解除维护")
            remove_file(self.path)

    def leave(self) -> None:
        if self.lease:
            self.lease.release()
            self.lease = None
            remove_file(self.root / "leases" / (self.id + ".json"))
