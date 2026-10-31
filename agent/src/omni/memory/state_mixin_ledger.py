"""执行账本与写额度域的 `state.db` 访问 mixin。

`StateStore` 通过继承本 mixin 获得这些方法；宿主必须提供 `_tx`、`_read`。
执行账本滚动保留最近 N 条；写额度用 `UPDATE ... WHERE used < limit` 的
rowcount 判定（跨进程下这是"我拿到了最后一份额度"的唯一证据）。
"""
from __future__ import annotations

from .state_records import BudgetHit


class LedgerMixin:
    """执行账本（executions）与写额度（budgets）。"""

    def record_execution(
        self,
        *,
        job: str,
        started_at: str,
        duration_s: float,
        ok: bool,
        result: str = "",
        delivery: str = "",
        keep: int | None = None,
    ) -> None:
        keep = self._keep_executions if keep is None else max(1, int(keep))
        with self._tx() as conn:
            conn.execute(
                "INSERT INTO executions(job, started_at, duration_s, ok, result, delivery) "
                "VALUES (?, ?, ?, ?, ?, ?)",
                (job, started_at, round(float(duration_s), 2), 1 if ok else 0,
                 str(result)[:300], str(delivery)[:200]),
            )
            conn.execute(
                "DELETE FROM executions WHERE id NOT IN "
                "(SELECT id FROM executions ORDER BY id DESC LIMIT ?)",
                (keep,),
            )

    def recent_executions(self, limit: int = 50) -> list[dict]:
        rows = self._read(
            "SELECT job, started_at, duration_s, ok, result, delivery FROM executions "
            "ORDER BY id DESC LIMIT ?",
            (max(1, int(limit)),),
        )
        return [
            {
                "job": str(row["job"]),
                "started_at": str(row["started_at"]),
                "duration_s": float(row["duration_s"]),
                "ok": bool(row["ok"]),
                "result": str(row["result"]),
                "delivery": str(row["delivery"]),
            }
            for row in rows
        ]

    def consume_budget(self, scope: str, day: str, limit: int) -> BudgetHit:
        """原子消耗一份额度：`UPDATE ... WHERE used < limit` 的 rowcount 就是判据。"""
        limit = int(limit)
        if limit <= 0:
            return BudgetHit(False, 0, max(0, limit), "额度上限为 0：未授权任何写操作")
        with self._tx() as conn:
            conn.execute(
                "INSERT OR IGNORE INTO budgets(scope, day, used) VALUES (?, ?, 0)",
                (scope, day),
            )
            cur = conn.execute(
                "UPDATE budgets SET used = used + 1 WHERE scope = ? AND day = ? AND used < ?",
                (scope, day, limit),
            )
            used = int(
                conn.execute(
                    "SELECT used FROM budgets WHERE scope = ? AND day = ?", (scope, day)
                ).fetchone()[0]
            )
        if cur.rowcount == 1:
            return BudgetHit(True, used, limit, f"写操作 {used}/{limit}（今日）")
        return BudgetHit(False, used, limit, f"额度已用完（{used}/{limit}）")

    def budget_used(self, scope: str, day: str) -> int:
        rows = self._read(
            "SELECT used FROM budgets WHERE scope = ? AND day = ?", (scope, day)
        )
        return int(rows[0][0]) if rows else 0
