"""资源租约域的 `state.db` 访问 mixin（会话/浏览器 profile 等互斥资源）。

`StateStore` 通过继承本 mixin 获得这些方法；宿主必须提供 `_tx`、`_read`。
语义：同一资源同一时刻只允许一个持有者；过期后自动可再领取，进程崩溃
不需要人工解锁，但要等满 TTL。
"""
from __future__ import annotations

import time
import uuid

from .state_records import Lease

SESSION_RESOURCE = "session:{}"


class LeasesMixin:
    """资源租约：互斥领取、按 TTL 续约、按 token 释放。"""

    def lease(self, resource: str) -> Lease | None:
        rows = self._read("SELECT * FROM resource_leases WHERE resource = ?", (resource,))
        if not rows:
            return None
        row = rows[0]
        return Lease(
            resource=str(row["resource"]),
            owner=str(row["owner"]),
            token=str(row["token"]),
            acquired_at=str(row["acquired_at"]),
            expires_at=float(row["expires_at"]),
        )

    def acquire_lease(
        self,
        resource: str,
        owner: str,
        *,
        ttl_s: float = 600.0,
        token: str | None = None,
        now: float | None = None,
    ) -> Lease | None:
        """拿到租约返回 Lease；被别的所有者占着返回 None（不排队、不抢）。"""
        token = token or uuid.uuid4().hex
        stamp = time.time() if now is None else float(now)
        expires = stamp + float(ttl_s)
        with self._tx() as conn:
            # 过期租约先清掉：进程崩溃后不需要人工解锁，但要等满 TTL
            conn.execute(
                "DELETE FROM resource_leases WHERE resource = ? AND expires_at < ?",
                (resource, stamp),
            )
            held = conn.execute(
                "SELECT owner FROM resource_leases WHERE resource = ?", (resource,)
            ).fetchone()
            if held is not None:
                return None
            conn.execute(
                "INSERT INTO resource_leases(resource, owner, token, acquired_at, expires_at) "
                "VALUES (?, ?, ?, ?, ?)",
                (resource, str(owner), token, _leases_now(), expires),
            )
        return Lease(
            resource=resource,
            owner=str(owner),
            token=token,
            acquired_at=_leases_now(),
            expires_at=expires,
        )

    def renew_lease(
        self, token: str, *, ttl_s: float = 600.0, now: float | None = None
    ) -> bool:
        stamp = time.time() if now is None else float(now)
        with self._tx() as conn:
            cur = conn.execute(
                "UPDATE resource_leases SET expires_at = ? WHERE token = ? AND expires_at >= ?",
                (stamp + float(ttl_s), token, stamp),
            )
            return cur.rowcount > 0

    def release_lease(self, token: str) -> bool:
        """只释放自己那把（按 token）：不能顺手释放别人持有的租约。"""
        with self._tx() as conn:
            return conn.execute(
                "DELETE FROM resource_leases WHERE token = ?", (token,)
            ).rowcount > 0

    # 会话是资源租约的特例（键 `session:<id>`），保留语义化方法给调用方用
    def session_lease(self, session_id: int) -> Lease | None:
        return self.lease(SESSION_RESOURCE.format(int(session_id)))

    def acquire_session_lease(
        self,
        session_id: int,
        owner: str,
        *,
        ttl_s: float = 600.0,
        token: str | None = None,
        now: float | None = None,
    ) -> Lease | None:
        return self.acquire_lease(
            SESSION_RESOURCE.format(int(session_id)),
            owner,
            ttl_s=ttl_s,
            token=token,
            now=now,
        )

    def renew_session_lease(
        self, token: str, *, ttl_s: float = 600.0, now: float | None = None
    ) -> bool:
        return self.renew_lease(token, ttl_s=ttl_s, now=now)

    def release_session_lease(self, token: str) -> bool:
        return self.release_lease(token)


def _leases_now() -> str:
    import datetime as dt

    return dt.datetime.now().astimezone().isoformat(timespec="seconds")
