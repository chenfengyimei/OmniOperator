"""模型调用的用量与费用账本（`state.db`，D10）。

两件事必须落在这个进程之外（SQLite、跨进程事务）：

- **当日费用**：网页、QQ、心跳、CLI 各自都可能调模型。用一个内存计数器守预算，
  四个进程就各有各的"已用额度"，等于四倍上限。所以预留要走 `BEGIN IMMEDIATE`
  里的条件更新：`UPDATE ... WHERE reserved + spent + ? <= limit` 的 rowcount
  就是"这笔钱是我预留到的"的证据。
- **调用流水**：谁、哪个 run、什么用途（chat/compress/review/test）、多少次尝试、
  多少 token、多少钱、钱是哪来的（上报/预留/未知）。没有这张表，"这个月为什么花了
  这些钱"就永远说不清。

`cost_micro` 允许 NULL：未知不是 0。读接口一律把 NULL 原样交给界面，
不在仓储层"补一个 0"。

**分口径账本**（P5-c）：`llm_budget_scope` 按口径（通道）分别记账，
与 `llm_budget`（总额）并行。为什么要分口径：用户真正想控制的是
"QQ 上别烧太多"——总额只有一个数字，分了口径才能各自设限、互不挤占。
判定逻辑在 `llm/tiers.py`（纯函数），这里只管落盘与原子预留。

**提醒落盘**（P5-c）：`llm_budget_alert` 记"今天这个口径已经提醒过了"。
抄 surfcore `token-budget.js::takeAlert` 的纪律——在**返回时**就落盘，
而不是等用户点"知道了"；用户关掉页面、进程重启，同一个提醒也不会弹第二遍。
"""

from __future__ import annotations

import datetime as dt
from dataclasses import dataclass
from typing import Any

from .state import StateStore


@dataclass(frozen=True)
class CallRecord:
    id: int
    run_id: str | None
    session_id: int | None
    purpose: str
    model: str
    prompt_tokens: int | None
    completion_tokens: int | None
    cost_micro: int | None
    cost_source: str
    outcome: str
    error_code: str
    attempts: int
    duration_ms: int
    started_at: str
    detail: str

    def brief(self) -> dict[str, Any]:
        return {
            "id": self.id,
            "run_id": self.run_id,
            "session_id": self.session_id,
            "purpose": self.purpose,
            "model": self.model,
            "prompt_tokens": self.prompt_tokens,
            "completion_tokens": self.completion_tokens,
            "cost_micro": self.cost_micro,
            "cost_known": self.cost_micro is not None,
            "cost_source": self.cost_source,
            "outcome": self.outcome,
            "error_code": self.error_code,
            "attempts": self.attempts,
            "duration_ms": self.duration_ms,
            "started_at": self.started_at,
        }


class UsageLedger:
    """费用账本 + 调用流水。实现 `llm.budget.Ledger` 协议。"""

    def __init__(self, state: StateStore) -> None:
        self._state = state

    # ---------------------------------------------------------------- 预留/结算

    def reserve(self, *, day: str, micro: int, limit_micro: int) -> tuple[bool, int]:
        """原子预留。返回 (是否成功, 今日已预留 + 已花费)。"""
        micro = max(0, int(micro))
        limit_micro = int(limit_micro)
        if micro == 0:
            with self._state._tx() as conn:
                conn.execute(
                    "INSERT OR IGNORE INTO llm_budget(day, reserved_micro, spent_micro) VALUES (?, 0, 0)",
                    (day,),
                )
            return True, self.day_total(day)["used_micro"]
        with self._state._tx() as conn:
            conn.execute(
                "INSERT OR IGNORE INTO llm_budget(day, reserved_micro, spent_micro) VALUES (?, 0, 0)",
                (day,),
            )
            if limit_micro > 0:
                cur = conn.execute(
                    "UPDATE llm_budget SET reserved_micro = reserved_micro + ?"
                    " WHERE day = ? AND reserved_micro + spent_micro + ? <= ?",
                    (micro, day, micro, limit_micro),
                )
                ok = cur.rowcount == 1
            else:  # 未设上限：只记账不拦截
                conn.execute(
                    "UPDATE llm_budget SET reserved_micro = reserved_micro + ? WHERE day = ?",
                    (micro, day),
                )
                ok = True
            row = conn.execute("SELECT reserved_micro + spent_micro FROM llm_budget WHERE day = ?", (day,)).fetchone()
        return ok, int(row[0]) if row else 0

    def settle(self, *, day: str, reserved_micro: int, charged_micro: int) -> None:
        """退回预留，把实际花费计入。跨天结算（预留在前一天）也不会丢账。"""
        reserved = max(0, int(reserved_micro))
        charged = max(0, int(charged_micro))
        with self._state._tx() as conn:
            cur = conn.execute(
                "UPDATE llm_budget SET reserved_micro = MAX(0, reserved_micro - ?),"
                " spent_micro = spent_micro + ? WHERE day = ?",
                (reserved, charged, day),
            )
            if cur.rowcount == 0:
                conn.execute(
                    "INSERT INTO llm_budget(day, reserved_micro, spent_micro) VALUES (?, 0, ?)",
                    (day, charged),
                )

    def day_total(self, day: str) -> dict[str, int]:
        rows = self._state._read("SELECT reserved_micro, spent_micro FROM llm_budget WHERE day = ?", (day,))
        if not rows:
            return {"reserved_micro": 0, "spent_micro": 0, "used_micro": 0}
        reserved, spent = int(rows[0][0]), int(rows[0][1])
        return {
            "reserved_micro": reserved,
            "spent_micro": spent,
            "used_micro": reserved + spent,
        }

    # ---------------------------------------------------------------- 分口径账本

    def scope_reserve(self, *, day: str, scope: str, micro: int, limit_micro: int) -> tuple[bool, int]:
        """按口径原子预留。返回 (是否成功, 该口径已预留+已花费)。

        与 `reserve` 同一套纪律：条件更新的 rowcount 就是"这笔钱是我预留到的"
        的证据，多进程并发下也不会超发。

        `limit_micro<=0` 表示该口径不限：只记账不拦（与总额口径一致）。
        """
        micro = max(0, int(micro))
        limit_micro = int(limit_micro)
        scope = str(scope or "other")
        if micro == 0:
            with self._state._tx() as conn:
                conn.execute(
                    "INSERT OR IGNORE INTO llm_budget_scope(day, scope, reserved_micro, spent_micro)"
                    " VALUES (?, ?, 0, 0)",
                    (day, scope),
                )
            return True, self.scope_total(day, scope)["used_micro"]
        with self._state._tx() as conn:
            conn.execute(
                "INSERT OR IGNORE INTO llm_budget_scope(day, scope, reserved_micro, spent_micro) VALUES (?, ?, 0, 0)",
                (day, scope),
            )
            if limit_micro > 0:
                cur = conn.execute(
                    "UPDATE llm_budget_scope SET reserved_micro = reserved_micro + ?"
                    " WHERE day = ? AND scope = ? AND reserved_micro + spent_micro + ? <= ?",
                    (micro, day, scope, micro, limit_micro),
                )
                ok = cur.rowcount == 1
            else:
                conn.execute(
                    "UPDATE llm_budget_scope SET reserved_micro = reserved_micro + ? WHERE day = ? AND scope = ?",
                    (micro, day, scope),
                )
                ok = True
            row = conn.execute(
                "SELECT reserved_micro + spent_micro FROM llm_budget_scope WHERE day = ? AND scope = ?",
                (day, scope),
            ).fetchone()
        return ok, int(row[0]) if row else 0

    def scope_settle(self, *, day: str, scope: str, reserved_micro: int, charged_micro: int) -> None:
        """按口径退回预留、计入实际花费。与总额结算同一套语义。"""
        reserved = max(0, int(reserved_micro))
        charged = max(0, int(charged_micro))
        scope = str(scope or "other")
        with self._state._tx() as conn:
            cur = conn.execute(
                "UPDATE llm_budget_scope SET reserved_micro = MAX(0, reserved_micro - ?),"
                " spent_micro = spent_micro + ? WHERE day = ? AND scope = ?",
                (reserved, charged, day, scope),
            )
            if cur.rowcount == 0:
                conn.execute(
                    "INSERT INTO llm_budget_scope(day, scope, reserved_micro, spent_micro) VALUES (?, ?, 0, ?)",
                    (day, scope, charged),
                )

    def scope_total(self, day: str, scope: str) -> dict[str, int]:
        """某口径某日的用量。"""
        rows = self._state._read(
            "SELECT reserved_micro, spent_micro FROM llm_budget_scope WHERE day = ? AND scope = ?",
            (day, str(scope or "other")),
        )
        if not rows:
            return {"reserved_micro": 0, "spent_micro": 0, "used_micro": 0}
        reserved, spent = int(rows[0][0]), int(rows[0][1])
        return {
            "reserved_micro": reserved,
            "spent_micro": spent,
            "used_micro": reserved + spent,
        }

    def scope_totals(self, day: str) -> dict[str, dict[str, int]]:
        """某日所有口径的用量。"""
        rows = self._state._read(
            "SELECT scope, reserved_micro, spent_micro FROM llm_budget_scope WHERE day = ?",
            (day,),
        )
        out: dict[str, dict[str, int]] = {}
        for row in rows:
            reserved, spent = int(row[1]), int(row[2])
            out[str(row[0])] = {
                "reserved_micro": reserved,
                "spent_micro": spent,
                "used_micro": reserved + spent,
            }
        return out

    # ---------------------------------------------------------------- 提醒落盘

    def take_scope_alert(self, *, day: str, scope: str, level: str = "warn") -> bool:
        """领取"今天这个口径的提醒权"。返回 True = 你是今天第一次，**该提醒**。

        返回 False = 今天已经提醒过了，**不要再弹**。

        为什么必须落盘而不是放内存：网页、QQ、心跳是不同进程，各有自己的内存；
        进程重启内存也没了。抄 surfcore `token-budget.js::takeAlert` 的纪律——
        "这里在**返回时**就落盘，而不是等用户点'知道了'"。用户没点也记了，
        所以关页面/重启都不会弹第二遍。

        实现用主键冲突当闸门：`INSERT OR IGNORE` 的 rowcount 就是"没人领过"的证据。
        """
        stamp = dt.datetime.now().astimezone().isoformat(timespec="seconds")
        with self._state._tx() as conn:
            cur = conn.execute(
                "INSERT OR IGNORE INTO llm_budget_alert(day, scope, level, at) VALUES (?, ?, ?, ?)",
                (day, str(scope or "other"), str(level or "warn"), stamp),
            )
            return cur.rowcount == 1

    def scope_alerts(self, day: str) -> list[dict[str, str]]:
        """某日已发出的提醒记录（按口径、档位）。"""
        rows = self._state._read(
            "SELECT scope, level, at FROM llm_budget_alert WHERE day = ? ORDER BY at ASC",
            (day,),
        )
        return [{"scope": str(r[0]), "level": str(r[1]), "at": str(r[2])} for r in rows]

    # ---------------------------------------------------------------- 流水

    def record(
        self,
        *,
        run_id: str | None = None,
        session_id: int | None = None,
        purpose: str = "",
        model: str = "",
        prompt_tokens: int | None = None,
        completion_tokens: int | None = None,
        cost_micro: int | None = None,
        cost_source: str = "unknown",
        outcome: str = "ok",
        error_code: str = "",
        attempts: int = 1,
        duration_ms: int = 0,
        started_at: str | None = None,
        detail: str = "",
    ) -> None:
        stamp = started_at or dt.datetime.now().astimezone().isoformat(timespec="seconds")
        with self._state._tx() as conn:
            conn.execute(
                "INSERT INTO llm_calls(run_id, session_id, purpose, model, prompt_tokens,"
                " completion_tokens, cost_micro, cost_source, outcome, error_code, attempts,"
                " duration_ms, started_at, detail) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
                (
                    run_id,
                    None if session_id is None else int(session_id),
                    purpose,
                    model,
                    prompt_tokens,
                    completion_tokens,
                    cost_micro,
                    cost_source,
                    outcome,
                    error_code,
                    max(0, int(attempts)),
                    max(0, int(duration_ms)),
                    stamp,
                    detail,
                ),
            )

    def recent(self, limit: int = 50) -> list[CallRecord]:
        rows = self._state._read("SELECT * FROM llm_calls ORDER BY id DESC LIMIT ?", (max(1, min(limit, 500)),))
        return [
            CallRecord(
                id=int(row["id"]),
                run_id=row["run_id"],
                session_id=int(row["session_id"]) if row["session_id"] is not None else None,
                purpose=str(row["purpose"]),
                model=str(row["model"]),
                prompt_tokens=int(row["prompt_tokens"]) if row["prompt_tokens"] is not None else None,
                completion_tokens=int(row["completion_tokens"]) if row["completion_tokens"] is not None else None,
                cost_micro=int(row["cost_micro"]) if row["cost_micro"] is not None else None,
                cost_source=str(row["cost_source"]),
                outcome=str(row["outcome"]),
                error_code=str(row["error_code"]),
                attempts=int(row["attempts"]),
                duration_ms=int(row["duration_ms"]),
                started_at=str(row["started_at"]),
                detail=str(row["detail"]),
            )
            for row in rows
        ]

    def summary(self, days: int = 7) -> dict[str, Any]:
        """按用途汇总最近 N 天：花了多少、有多少笔算不出钱。"""
        since = (dt.date.today() - dt.timedelta(days=max(0, days - 1))).isoformat()
        rows = self._state._read(
            "SELECT purpose, COUNT(*) AS calls, SUM(COALESCE(cost_micro, 0)) AS micro,"
            " SUM(CASE WHEN cost_micro IS NULL THEN 1 ELSE 0 END) AS unknown_cost,"
            " SUM(CASE WHEN outcome != 'ok' THEN 1 ELSE 0 END) AS failures"
            " FROM llm_calls WHERE substr(started_at, 1, 10) >= ? GROUP BY purpose"
            " ORDER BY micro DESC",
            (since,),
        )
        by_purpose = [
            {
                "purpose": str(row["purpose"]),
                "calls": int(row["calls"]),
                "cost_micro": int(row["micro"] or 0),
                "unknown_cost": int(row["unknown_cost"] or 0),
                "failures": int(row["failures"] or 0),
            }
            for row in rows
        ]
        return {
            "since": since,
            "by_purpose": by_purpose,
            "calls": sum(item["calls"] for item in by_purpose),
            "cost_micro": sum(item["cost_micro"] for item in by_purpose),
            "unknown_cost": sum(item["unknown_cost"] for item in by_purpose),
        }

    def prune(self, keep_days: int = 180) -> int:
        """删除过老流水（只删流水，不删当日账：账是钱，流水是明细）。"""
        cutoff = (dt.date.today() - dt.timedelta(days=max(1, keep_days))).isoformat()
        with self._state._tx() as conn:
            return conn.execute("DELETE FROM llm_calls WHERE substr(started_at, 1, 10) < ?", (cutoff,)).rowcount
