"""QQ 入站去重、撤回墓碑与回复身份；只保存 ID/状态，不保存原文。

去重保留 30 天，容量耗尽拒绝新事件；进程中断的 accepted 不自动重放。
"""

from __future__ import annotations

import time
from collections.abc import Callable

from .state import StateStore


class QQMessageStore:
    def __init__(self, state: StateStore, *, clock: Callable[[], float] = time.time, capacity: int = 20000) -> None:
        self.state = state
        self.clock = clock
        self.capacity = capacity

    def _insert(self, bot: int, scene: str, message_id: str, status: str) -> bool:
        with self.state._tx() as conn:
            now = self.clock()
            conn.execute("DELETE FROM qq_messages WHERE created_at < ?", (now - 30 * 86400,))
            conn.execute("DELETE FROM qq_sent WHERE created_at < ?", (now - 7 * 86400,))
            if status == "recalled":
                changed = conn.execute(
                    "UPDATE qq_messages SET status='recalled' WHERE bot=? AND scene=? AND message_id=?",
                    (bot, scene, message_id),
                ).rowcount
                conn.execute("DELETE FROM qq_sent WHERE bot=? AND scene=? AND message_id=?", (bot, scene, message_id))
                if changed:
                    return True
            if conn.execute("SELECT COUNT(*) FROM qq_messages").fetchone()[0] >= self.capacity:
                return False
            return (
                conn.execute(
                    "INSERT OR IGNORE INTO qq_messages VALUES(?,?,?,?,?)", (bot, scene, message_id, status, now)
                ).rowcount
                == 1
            )

    def claim(self, bot: int, scene: str, message_id: str) -> bool:
        return self._insert(bot, scene, message_id, "accepted")

    def recall(self, bot: int, scene: str, message_id: str) -> bool:
        return self._insert(bot, scene, message_id, "recalled")

    def active(self, bot: int, scene: str, message_id: str) -> bool:
        rows = self.state._read(
            "SELECT status FROM qq_messages WHERE bot=? AND scene=? AND message_id=?", (bot, scene, message_id)
        )
        return bool(rows and rows[0][0] == "accepted")

    def finish(self, bot: int, scene: str, message_id: str, status: str) -> None:
        if status not in {"done", "suppressed", "unknown"}:
            raise ValueError("非法消息状态")
        with self.state._tx() as conn:
            conn.execute(
                "UPDATE qq_messages SET status=? WHERE bot=? AND scene=? AND message_id=? AND status='accepted'",
                (status, bot, scene, message_id),
            )

    def remember_sent(self, bot: int, scene: str, ids: list[int]) -> None:
        with self.state._tx() as conn:
            now = self.clock()
            conn.execute("DELETE FROM qq_sent WHERE created_at < ?", (now - 7 * 86400,))
            # 只保留有限的近期机器人回执，过老引用不再触发群回复。
            conn.execute(
                "DELETE FROM qq_sent WHERE rowid NOT IN (SELECT rowid FROM qq_sent ORDER BY created_at DESC LIMIT 5000)"
            )
            conn.executemany(
                "INSERT OR REPLACE INTO qq_sent VALUES(?,?,?,?)", [(bot, scene, str(mid), now) for mid in ids]
            )

    def is_reply(self, bot: int, scene: str, message_id: str) -> bool:
        return bool(
            self.state._read(
                "SELECT 1 FROM qq_sent WHERE bot=? AND scene=? AND message_id=? AND created_at>=?",
                (bot, scene, message_id, self.clock() - 7 * 86400),
            )
        )

    def status(self) -> dict[str, int]:
        return {
            str(row[0]): int(row[1])
            for row in self.state._read("SELECT status,COUNT(*) FROM qq_messages GROUP BY status")
        }

    def last_inbound_at(self, scene: str, bot: int | None = None) -> float | None:
        """这个场景最近一次**收到**消息的时间（epoch 秒）；没有则返回 None。

        供通道层冷却位判断「是不是刚跟用户聊过」。刻意复用已有的
        `qq_messages.created_at` 列，**不新建表**——真机 `data/*.db` 一旦改结构
        就得先备份迁移，而这一列本来就是入站时间戳，语义完全够用。

        只统计入站。`qq_sent`（我方发出的消息）不参与：我方发过话不代表
        用户刚跟我聊过，拿它当冷却锚点会把「我没说话也被潜水」变成常态。

        撤回的消息（status='recalled'）不算——那段对话事实上没发生过。
        `bot` 传 None 时跨 bot 聚合：bot id 只有 QQ 通道从事件里取得到
        （`event['self_id']`），而 cron/工具这两条主动路径手上没有它；
        scene 本身已按「私聊/群」命名空间隔离，一实例一 bot 的现实用法下
        聚合与按 bot 过滤等价。
        """
        sql = "SELECT MAX(created_at) FROM qq_messages WHERE scene=? AND status!='recalled'"
        params: tuple[object, ...] = (scene,)
        if bot is not None:
            sql += " AND bot=?"
            params = (scene, int(bot))
        rows = self.state._read(sql, params)
        if not rows or rows[0][0] is None:
            return None
        return float(rows[0][0])
