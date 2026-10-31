"""QQ 投递 outbox 仓储（`state.db`）。

职责：把心跳任务在 QQ 掉线期间的待投递结果持久化，并按「至多一次、
unknown 不自动重发」的纪律补投。表结构声明见 `memory/state_schema.py`；
连接、事务与迁移执行仍归 `memory/state.py::StateStore`，本模块不新建数据库。

语义：
- **pending**：明确没发出去（QQ 未连接等），恢复后自动补投；
- **unknown**：发送中途失败——可能已经发出一部分，**不自动补投**，
  只在工作台等人决定（重复发送比漏发半条更糟）；
- sent / discarded / dead：终态，不再参与去重与补投。

去重 = (target_qq, content_hash) 在活跃期（pending/unknown）内唯一：
同一条消息不会排两次队；进入终态后同样的内容可以重新入队。
"""
from __future__ import annotations

import datetime as dt
import hashlib
import sqlite3
import uuid
from collections.abc import Iterable
from typing import TYPE_CHECKING

from .jobs import to_iso

if TYPE_CHECKING:
    # 只做类型标注；运行期由 state.py 单向导入本模块，避免循环依赖。
    from .state import StateStore


class OutboxStore:
    LIVE_STATUSES = ("pending", "sending", "unknown")
    MAX_LIVE = 50
    MAX_ATTEMPTS = 3

    def __init__(self, state: StateStore) -> None:
        self._state = state

    @staticmethod
    def _now() -> str:
        return to_iso(dt.datetime.now().astimezone())

    def enqueue(
        self,
        *,
        job: str,
        target_qq: int,
        content: str,
        status: str = "pending",
        error: str = "",
    ) -> int | None:
        """入队返回条目 id；活跃重复返回 None（不是错误）；队列满抛 ValueError。"""
        if status not in {"pending", "unknown"}:
            raise ValueError("新投递只能是 pending 或 unknown")
        content_hash = hashlib.sha256(content.encode("utf-8")).hexdigest()
        now = self._now()
        try:
            with self._state._tx() as conn:
                live = conn.execute(
                    "SELECT COUNT(*) FROM outbox WHERE status IN ('pending','sending','unknown')"
                ).fetchone()[0]
                if int(live) >= self.MAX_LIVE:
                    raise ValueError(
                        f"待投递队列已满（{self.MAX_LIVE} 条活跃上限）：先在工作台清理"
                    )
                cursor = conn.execute(
                    "INSERT INTO outbox(job, target_qq, content, content_hash, status,"
                    " attempts, last_error, created_at, updated_at)"
                    " VALUES (?,?,?,?,?,0,?,?,?)",
                    (job, int(target_qq), content, content_hash, status, error, now, now),
                )
                return int(cursor.lastrowid or 0)
        except sqlite3.IntegrityError:
            return None  # 活跃期内 (target, content_hash) 已在队列——去重命中

    def list(self, *, limit: int = 100) -> list[dict]:
        rows = self._state._read(
            "SELECT * FROM outbox"
            " ORDER BY CASE WHEN status IN ('pending','sending','unknown') THEN 0 ELSE 1 END,"
            " updated_at DESC LIMIT ?",
            (max(1, int(limit)),),
        )
        return [self._row(r) for r in rows]

    def get(self, entry_id: int) -> dict | None:
        rows = self._state._read("SELECT * FROM outbox WHERE id = ?", (int(entry_id),))
        return self._row(rows[0]) if rows else None

    def pending(self, *, max_attempts: int | None = None) -> list[dict]:
        cap = self.MAX_ATTEMPTS if max_attempts is None else max(1, int(max_attempts))
        rows = self._state._read(
            "SELECT * FROM outbox WHERE status = 'pending' AND attempts < ?"
            " ORDER BY created_at, id",
            (cap,),
        )
        return [self._row(r) for r in rows]

    def mark(self, entry_id: int, status: str, *, error: str = "") -> bool:
        """写终态/状态；sent 会带上 sent_at。返回是否真的改到了行。"""
        now = self._now()
        sent_at = now if status == "sent" else None
        with self._state._tx() as conn:
            cursor = conn.execute(
                "UPDATE outbox SET status = ?, last_error = ?, updated_at = ?,"
                " sent_at = COALESCE(?, sent_at) WHERE id = ? AND status != 'sending'",
                (status, error, now, sent_at, int(entry_id)),
            )
            return cursor.rowcount > 0

    def claim_next(self, *, now: dt.datetime | None = None, stale_after_s: float = 300,
                   only_id: int | None = None, exclude_ids: tuple[int, ...] = ()) -> dict | None:
        """原子领取一次发送；过期在途记录变 unknown，绝不恢复为可重试。"""
        instant = now or dt.datetime.now().astimezone()
        stamp = to_iso(instant)
        cutoff = to_iso(instant - dt.timedelta(seconds=max(60, stale_after_s)))
        with self._state._tx() as conn:
            conn.execute(
                "UPDATE outbox SET status='unknown', claim_token=NULL, updated_at=?,"
                " last_error='发送进程中断或超时，结果未知，禁止自动重发'"
                " WHERE status='sending' AND julianday(updated_at) < julianday(?)",
                (stamp, cutoff),
            )
            filters = "" if only_id is None else " AND id=?"
            params = [self.MAX_ATTEMPTS] + ([] if only_id is None else [int(only_id)])
            if exclude_ids:
                filters += " AND id NOT IN (" + ",".join("?" for _ in exclude_ids) + ")"
                params.extend(exclude_ids)
            row = conn.execute(
                "SELECT id FROM outbox WHERE status='pending' AND attempts < ?"
                + filters + " ORDER BY created_at,id LIMIT 1", params,
            ).fetchone()
            if row is None:
                return None
            token = uuid.uuid4().hex
            conn.execute(
                "UPDATE outbox SET status='sending', claim_token=?, updated_at=?,"
                " attempts=attempts+1 WHERE id=? AND status='pending'",
                (token, stamp, row[0]),
            )
            return self._row(conn.execute("SELECT * FROM outbox WHERE id=?", (row[0],)).fetchone())

    def finish_claim(self, entry_id: int, token: str, status: str, *, error: str = "") -> bool:
        """只有仍持有本次发送身份的调用方能结算；旧进程不能覆盖人工处理结果。"""
        if status not in {"pending", "sent", "unknown", "dead", "discarded"}:
            raise ValueError("非法投递结果")
        now = self._now()
        with self._state._tx() as conn:
            cursor = conn.execute(
                "UPDATE outbox SET status=?, claim_token=NULL, last_error=?, updated_at=?,"
                " sent_at=CASE WHEN ?='sent' THEN ? ELSE sent_at END"
                " WHERE id=? AND status='sending' AND claim_token=?",
                (status, error, now, status, now, int(entry_id), token),
            )
            return cursor.rowcount == 1

    def defer_claim(self, entry_id: int, token: str, *, reason: str) -> bool:
        """发送尚未开始的策略等待不计网络尝试；仍需当前领取身份。"""
        with self._state._tx() as conn:
            return conn.execute(
                "UPDATE outbox SET status='pending', claim_token=NULL, attempts=MAX(0,attempts-1),"
                " last_error=?, updated_at=? WHERE id=? AND status='sending' AND claim_token=?",
                (reason, self._now(), entry_id, token)).rowcount == 1

    def attempt(self, entry_id: int, *, error: str = "") -> int:
        """记一次失败尝试并返回累计次数（调用方据此决定是否成死信）。"""
        now = self._now()
        with self._state._tx() as conn:
            cursor = conn.execute(
                "UPDATE outbox SET attempts = attempts + 1, last_error = ?, updated_at = ?"
                " WHERE id = ?",
                (error, now, int(entry_id)),
            )
            if cursor.rowcount == 0:
                raise KeyError(f"outbox 条目 {entry_id} 不存在")
            rows = conn.execute(
                "SELECT attempts FROM outbox WHERE id = ?", (int(entry_id),)
            ).fetchall()
            return int(rows[0][0])

    def retry(self, entry_id: int, *, note: str = "用户手动重试") -> bool:
        """unknown/dead/discarded → 重新排队（attempts 清零，给满额重试预算）。"""
        now = self._now()
        with self._state._tx() as conn:
            # 活跃内容唯一约束同样适用于人工重排，冲突应返回拒绝而非 500。
            row = conn.execute("SELECT target_qq,content_hash,status FROM outbox WHERE id=?", (entry_id,)).fetchone()
            if row is None or conn.execute(
                "SELECT 1 FROM outbox WHERE id<>? AND target_qq=? AND content_hash=? AND status IN ('pending','sending','unknown')",
                (entry_id, row[0], row[1])).fetchone():
                return False
            if row[2] not in self.LIVE_STATUSES and conn.execute(
                "SELECT COUNT(*) FROM outbox WHERE status IN ('pending','sending','unknown')"
            ).fetchone()[0] >= self.MAX_LIVE:
                return False
            cursor = conn.execute(
                "UPDATE outbox SET status = 'pending', attempts = 0, last_error = ?,"
                " updated_at = ? WHERE id = ? AND status IN ('unknown','dead','discarded')",
                (note, now, int(entry_id)),
            )
            return cursor.rowcount > 0

    def discard(self, entry_id: int, *, note: str = "用户丢弃") -> bool:
        now = self._now()
        with self._state._tx() as conn:
            cursor = conn.execute(
                "UPDATE outbox SET status = 'discarded', last_error = ?, updated_at = ?"
                " WHERE id = ? AND status IN ('pending','unknown','dead','discarded')",
                (note, now, int(entry_id)),
            )
            return cursor.rowcount > 0

    # 终态滚动保留条数：工作台可查、审计可回溯；更旧的由补投周期清理
    PRUNE_KEEP = 200

    def prune_terminal(self, *, keep: int | None = None) -> int:
        """删除较旧的终态行（sent/discarded/dead），保留最近 keep 条。

        D17 登记项：表此前只增不减。活跃行（pending/sending/unknown）绝不删——
        它们是待补投/待人工处理的欠账，不是历史。
        """
        limit = self.PRUNE_KEEP if keep is None else max(0, int(keep))
        with self._state._tx() as conn:
            cursor = conn.execute(
                "DELETE FROM outbox WHERE status IN ('sent','discarded','dead') AND id NOT IN ("
                " SELECT id FROM outbox WHERE status IN ('sent','discarded','dead')"
                " ORDER BY updated_at DESC, id DESC LIMIT ?)",
                (limit,),
            )
            return cursor.rowcount

    @staticmethod
    def _row(r: Iterable) -> dict:
        row = list(r)
        keys = (
            "id", "job", "target_qq", "content", "content_hash", "status",
            "attempts", "last_error", "created_at", "updated_at", "sent_at", "claim_token",
        )
        out = {k: v for k, v in zip(keys, row)}
        out["id"] = int(out["id"])
        out["target_qq"] = int(out["target_qq"])
        out["attempts"] = int(out["attempts"])
        return out
