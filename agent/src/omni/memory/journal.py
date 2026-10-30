"""笔记记忆（M3/M4/M5）：每日/每周/每月笔记的存储与到期判定。

职责：
- `journal_notes` 以 `UNIQUE(kind, period_key)` 承载"至多一篇"语义——两个整理
  进程并发认领同一时期，只有一个能拿到（照 review_runs 的思路）；
- 三态 running/done/failed + attempts：失败可重试、卡死可按 stale 窗口接管；
- `covered_to_id` 记录每日笔记取材覆盖到的对话全量最大行 id——23:30 之后又
  聊了几句，下一轮会因"有未覆盖活动"自动重整当天；
- `due_periods` 是**纯判定函数**：只根据"过去/今天/整理时刻"与两库现状算出
  该整理哪些时期，编排层（core）只管执行——关机错过的天自动补，幂等。

明确不做：不做模型调用与笔记内容生成（归 core/journal.py 编排）；不删旧笔记
（保留全文，覆盖更新即新内容替换旧内容，历史在 journal_runs 流水里可溯）。
"""
from __future__ import annotations

import contextlib
import datetime as dt
import functools
import sqlite3
import threading
from pathlib import Path

from ..config import DATA_DIR

KINDS = ("daily", "weekly", "monthly")

_SCHEMA = """
CREATE TABLE IF NOT EXISTS journal_notes (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    kind TEXT NOT NULL,
    period_key TEXT NOT NULL,
    status TEXT NOT NULL,
    content TEXT NOT NULL DEFAULT '',
    model TEXT NOT NULL DEFAULT '',
    error TEXT,
    attempts INTEGER NOT NULL DEFAULT 0,
    covered_to_id INTEGER,
    started_at TEXT,
    finished_at TEXT,
    created_at TEXT NOT NULL,
    UNIQUE(kind, period_key)
);
CREATE INDEX IF NOT EXISTS idx_journal_notes_kind ON journal_notes(kind, id);
CREATE TABLE IF NOT EXISTS journal_runs (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    kind TEXT NOT NULL,
    period_key TEXT NOT NULL,
    ok INTEGER NOT NULL,
    detail TEXT NOT NULL DEFAULT '',
    created_at TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_journal_runs_recent ON journal_runs(id DESC);
"""


def _now() -> str:
    return dt.datetime.now().astimezone().isoformat(timespec="seconds")


def _locked(method):
    @functools.wraps(method)
    def wrapper(self: "JournalStore", *args, **kwargs):
        with self._lock:
            return method(self, *args, **kwargs)

    return wrapper


def week_key(day: dt.date) -> str:
    """ISO 周键：2026-W44（周一开始的 ISO 周）。"""
    iso = day.isocalendar()
    return f"{iso[0]}-W{iso[1]:02d}"


def month_key(day: dt.date) -> str:
    return f"{day.year:04d}-{day.month:02d}"


def week_end(day: dt.date) -> dt.date:
    iso = day.isocalendar()
    return dt.date.fromisocalendar(iso[0], iso[1], 7)


def month_end(day: dt.date) -> dt.date:
    if day.month == 12:
        return dt.date(day.year, 12, 31)
    return dt.date(day.year, day.month + 1, 1) - dt.timedelta(days=1)


def week_monday(key: str) -> dt.date:
    """ISO 周键 → 该周周一（公开给整理编排取材用）。"""
    year, week = key.split("-W")
    return dt.date.fromisocalendar(int(year), int(week), 1)


def month_weeks(days: list[str]) -> dict[str, list[str]]:
    """每个自然月覆盖到哪些 ISO 周（跨界周计入两个月——"本月发生的事"口径）。

    例：W40 是 9-28(周一)~10-04(周日)，则 2026-09 与 2026-10 的月记都会消费它。
    只按"有活动的周"建映射：整月无活动的月份不出现，也就不产空月记。
    """
    week_keys: set[str] = set()
    for day in days:
        week_keys.add(week_key(dt.date.fromisoformat(day)))
    months: dict[str, set[str]] = {}
    for key in week_keys:
        monday = week_monday(key)
        sunday = week_end(monday)
        for m in {month_key(monday), month_key(sunday)}:
            months.setdefault(m, set()).add(key)
    return {m: sorted(keys) for m, keys in months.items()}


class JournalStore:
    """日/周/月笔记的记忆单元（memory.db 新表）。"""

    def __init__(self, db_path: Path | None = None) -> None:
        path = db_path or (DATA_DIR / "memory.db")
        path.parent.mkdir(parents=True, exist_ok=True)
        self._lock = threading.RLock()
        self._conn = sqlite3.connect(path, check_same_thread=False)
        self._conn.execute("PRAGMA journal_mode=WAL")
        self._conn.execute("PRAGMA busy_timeout=5000")
        self._conn.executescript(_SCHEMA)
        self._conn.commit()

    # ---------------------------------------------------------------- 认领与结算

    @_locked
    def claim(
        self,
        kind: str,
        period_key: str,
        *,
        allow_redo: bool = False,
        stale_after_s: float = 1800.0,
    ) -> bool:
        """认领一个时期的整理权；返回 False=别人在做或已完成且不允许重做。

        - 无行 → INSERT running（首个认领者）；
        - failed → 重新认领（attempts+1）；
        - running 且已超 stale 窗口 → 接管（上一个认领者大概率已死）；
        - done → 仅 allow_redo（到期判定发现"有未覆盖活动"时）才重开。
        """
        if kind not in KINDS:
            raise ValueError(f"笔记类型必须是 {KINDS}，收到 {kind!r}")
        try:
            self._conn.execute("BEGIN IMMEDIATE")
        except sqlite3.OperationalError as exc:
            raise RuntimeError(f"笔记认领失败（数据库忙）：{exc}") from exc
        try:
            row = self._conn.execute(
                "SELECT status, started_at, attempts FROM journal_notes"
                " WHERE kind = ? AND period_key = ?",
                (kind, str(period_key)),
            ).fetchone()
            now = _now()
            if row is None:
                self._conn.execute(
                    "INSERT INTO journal_notes(kind, period_key, status, attempts,"
                    " started_at, created_at) VALUES (?, ?, 'running', 1, ?, ?)",
                    (kind, str(period_key), now, now),
                )
                self._conn.commit()
                return True
            status, started_at, attempts = str(row[0]), str(row[1] or ""), int(row[2] or 0)
            if status == "running":
                age_s = 0.0
                if started_at:
                    try:
                        started = dt.datetime.fromisoformat(started_at)
                        age_s = max(0.0, (dt.datetime.now().astimezone() - started).total_seconds())
                    except ValueError:
                        age_s = float(stale_after_s)  # 解析不了当超时处理，别永远卡住
                if age_s < float(stale_after_s):
                    self._conn.commit()
                    return False
            elif status == "done" and not allow_redo:
                self._conn.commit()
                return False
            self._conn.execute(
                "UPDATE journal_notes SET status='running', started_at=?, attempts=?"
                " WHERE kind = ? AND period_key = ?",
                (now, attempts + 1, kind, str(period_key)),
            )
            self._conn.commit()
            return True
        except BaseException:
            with contextlib.suppress(sqlite3.Error):
                self._conn.rollback()
            raise

    @_locked
    def finish(
        self,
        kind: str,
        period_key: str,
        content: str,
        *,
        model: str = "",
        covered_to_id: int | None = None,
    ) -> bool:
        cur = self._conn.execute(
            "UPDATE journal_notes SET status='done', content=?, model=?, covered_to_id=?,"
            " error=NULL, finished_at=? WHERE kind = ? AND period_key = ? AND status='running'",
            (str(content), str(model or ""), covered_to_id, _now(), kind, str(period_key)),
        )
        self._conn.commit()
        return int(cur.rowcount) > 0

    @_locked
    def fail(self, kind: str, period_key: str, error: str) -> bool:
        cur = self._conn.execute(
            "UPDATE journal_notes SET status='failed', error=?, finished_at=?"
            " WHERE kind = ? AND period_key = ? AND status='running'",
            (str(error)[:1000], _now(), kind, str(period_key)),
        )
        self._conn.commit()
        return int(cur.rowcount) > 0

    @_locked
    def reopen(self, kind: str, period_key: str, *, reason: str = "用户请求重新整理") -> str:
        """把 done/failed 的笔记重新标记为待整理；真正的模型整理由心跳 worker 下一轮执行。

        返回 'ok' / 'missing' / 'running'——Web 重试端点据此映射 200/404/409。
        不在这里跑模型：Web 进程不做整理调用，心跳进程是唯一执行者。
        """
        row = self._conn.execute(
            "SELECT status FROM journal_notes WHERE kind = ? AND period_key = ?",
            (kind, str(period_key)),
        ).fetchone()
        if row is None:
            return "missing"
        if str(row[0]) == "running":
            return "running"
        self._conn.execute(
            "UPDATE journal_notes SET status='failed', error=?, finished_at=?"
            " WHERE kind = ? AND period_key = ?",
            (reason, _now(), kind, str(period_key)),
        )
        self._conn.commit()
        return "ok"

    @_locked
    def mark_attempt(self, kind: str, period_key: str, *, ok: bool, detail: str = "") -> None:
        """整理流水：每次尝试（成败都）记一笔，观测面板用。"""
        self._conn.execute(
            "INSERT INTO journal_runs(kind, period_key, ok, detail, created_at)"
            " VALUES (?, ?, ?, ?, ?)",
            (kind, str(period_key), 1 if ok else 0, str(detail)[:2000], _now()),
        )
        self._conn.commit()

    # ---------------------------------------------------------------- 读取

    @_locked
    def note(self, kind: str, period_key: str) -> dict | None:
        row = self._conn.execute(
            "SELECT id, kind, period_key, status, content, model, error, attempts,"
            " covered_to_id, started_at, finished_at, created_at"
            " FROM journal_notes WHERE kind = ? AND period_key = ?",
            (kind, str(period_key)),
        ).fetchone()
        return self._row(row) if row else None

    @_locked
    def latest_note(self, kind: str) -> dict | None:
        row = self._conn.execute(
            "SELECT id, kind, period_key, status, content, model, error, attempts,"
            " covered_to_id, started_at, finished_at, created_at"
            " FROM journal_notes WHERE kind = ? ORDER BY period_key DESC LIMIT 1",
            (kind,),
        ).fetchone()
        return self._row(row) if row else None

    @_locked
    def list_notes(self, kind: str, limit: int = 200) -> list[dict]:
        rows = self._conn.execute(
            "SELECT id, kind, period_key, status, content, model, error, attempts,"
            " covered_to_id, started_at, finished_at, created_at"
            " FROM journal_notes WHERE kind = ? ORDER BY period_key DESC LIMIT ?",
            (kind, max(1, min(int(limit), 2000))),
        ).fetchall()
        return [self._row(r) for r in rows]

    @_locked
    def note_map(self, kind: str) -> dict[str, dict]:
        """全量时期→笔记状态映射（到期判定用；个人库量级下无需分页）。"""
        return {item["period_key"]: item for item in self.list_notes(kind, limit=2000)}

    @_locked
    def runs(self, limit: int = 50) -> list[dict]:
        rows = self._conn.execute(
            "SELECT id, kind, period_key, ok, detail, created_at FROM journal_runs"
            " ORDER BY id DESC LIMIT ?",
            (max(1, min(int(limit), 500)),),
        ).fetchall()
        return [
            {
                "id": int(r[0]),
                "kind": str(r[1]),
                "period_key": str(r[2]),
                "ok": bool(r[3]),
                "detail": str(r[4]),
                "created_at": str(r[5]),
            }
            for r in rows
        ]

    @staticmethod
    def _row(r) -> dict:
        return {
            "id": int(r[0]),
            "kind": str(r[1]),
            "period_key": str(r[2]),
            "status": str(r[3]),
            "content": str(r[4]),
            "model": str(r[5]),
            "error": str(r[6] or ""),
            "attempts": int(r[7] or 0),
            "covered_to_id": int(r[8]) if r[8] is not None else None,
            "started_at": str(r[9] or ""),
            "finished_at": str(r[10] or ""),
            "created_at": str(r[11]),
        }

    @_locked
    def stats(self) -> dict:
        result: dict = {}
        for kind in KINDS:
            row = self._conn.execute(
                "SELECT COUNT(*), COALESCE(SUM(status='done'), 0), COALESCE(SUM(status='failed'), 0),"
                " COALESCE(SUM(status='running'), 0) FROM journal_notes WHERE kind = ?",
                (kind,),
            ).fetchone()
            result[kind] = {
                "total": int(row[0]),
                "done": int(row[1]),
                "failed": int(row[2]),
                "running": int(row[3]),
            }
        return result

    def close(self) -> None:
        with self._lock:
            self._conn.close()


# -------------------------------------------------------------------- 到期判定


def due_periods(
    journal: JournalStore,
    dialogue,
    *,
    today: dt.date,
    now_hhmm: str,
    daily_time: str,
    max_catchup: int,
) -> list[tuple[str, str]]:
    """该整理哪些时期：返回 [(kind, period_key), ...]，日积压（最旧优先）在前。

    判定全部只读两库现状，不推进任何状态——编排层执行成功与否由 claim/finish 兜底。
    """
    today_s = today.isoformat()
    daily_map = journal.note_map("daily")
    weekly_map = journal.note_map("weekly")
    monthly_map = journal.note_map("monthly")
    days_with_activity = [d for d in dialogue.days() if dialogue.has_activity(d)]

    # ---- 每日：过去没整理的天 + 当天到点；done 但有"迟到消息"也要重整
    daily_due: list[str] = []
    for day in days_with_activity:
        note = daily_map.get(day)
        to_time = day < today_s or now_hhmm >= daily_time
        if not to_time:
            continue
        if note is None or note["status"] == "failed":
            daily_due.append(day)
        elif note["status"] == "done":
            covered = note.get("covered_to_id")
            max_id = dialogue.max_id_for_day(day)
            if max_id is not None and (covered is None or max_id > covered):
                daily_due.append(day)  # 笔记写完后又聊了新内容 → 自动重整
    overdue = [d for d in daily_due if d < today_s]
    selected = overdue[: max(1, int(max_catchup))]
    result: list[tuple[str, str]] = [("daily", d) for d in sorted(selected)]
    for d in sorted(daily_due):
        if d == today_s:
            result.append(("daily", d))

    # ---- 每周：整周已过去 + 该周所有有活动的天都已成稿 + 本周无 done 周记
    weeks: dict[str, list[str]] = {}
    for day in days_with_activity:
        key = week_key(dt.date.fromisoformat(day))
        weeks.setdefault(key, []).append(day)
    for key in sorted(weeks):
        end = week_end(dt.date.fromisoformat(weeks[key][0]))
        if end >= today:
            continue  # 本周还没结束
        note = weekly_map.get(key)
        if note is not None and note["status"] in ("done", "running"):
            continue
        if any(daily_map.get(d, {}).get("status") != "done" for d in weeks[key]):
            continue  # 还有天没整理完：先补齐日笔记，下周轮再收周记
        result.append(("weekly", key))

    # ---- 每月：自然月已过去 + 该月各活动周（跨界周计入）的周记齐了 + 无 done 月记
    for mkey, week_list in sorted(month_weeks(days_with_activity).items()):
        if month_end(week_monday(week_list[0]).replace(day=1)) >= today:
            continue
        note = monthly_map.get(mkey)
        if note is not None and note["status"] in ("done", "running"):
            continue
        if any(weekly_map.get(w, {}).get("status") != "done" for w in week_list):
            continue
        result.append(("monthly", mkey))

    return result
