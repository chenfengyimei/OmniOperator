"""M3/M4/M5 笔记记忆单元：认领幂等、三态结算与到期判定纯函数。"""
from __future__ import annotations

import datetime as dt

import pytest

from omni.memory.dialogue import DialogueLog
from omni.memory.journal import (
    JournalStore,
    due_periods,
    month_end,
    month_key,
    week_end,
    week_key,
)


def _stores(tmp_path):
    return JournalStore(tmp_path / "memory.db"), DialogueLog(tmp_path / "memory.db")


def test_claim_first_wins_and_done_blocks(tmp_path):
    journal, _ = _stores(tmp_path)
    assert journal.claim("daily", "2026-10-01")
    assert journal.claim("daily", "2026-10-01") is False  # running 未超时 → 拒绝
    assert journal.finish("daily", "2026-10-01", "今天整理了记忆", model="deepseek", covered_to_id=99)
    assert journal.claim("daily", "2026-10-01") is False  # done → 不再重复
    assert journal.claim("daily", "2026-10-01", allow_redo=True)  # 重做要显式开
    journal.close()


def test_failed_can_be_retried_and_stale_claim_taken_over(tmp_path):
    journal, _ = _stores(tmp_path)
    assert journal.claim("weekly", "2026-W40")
    journal.fail("weekly", "2026-W40", "模型没调保存工具")
    note = journal.note("weekly", "2026-W40")
    assert note["status"] == "failed" and note["attempts"] == 1
    assert journal.claim("weekly", "2026-W40")  # failed 允许重试
    # 手动把 started_at 拨回一小时前 → 视为卡死，可被接管
    journal._conn.execute(
        "UPDATE journal_notes SET started_at = ? WHERE kind='weekly' AND period_key='2026-W40'",
        ((dt.datetime.now().astimezone() - dt.timedelta(seconds=4000)).isoformat(timespec="seconds"),),
    )
    journal._conn.commit()
    assert journal.claim("weekly", "2026-W40", stale_after_s=1800)
    journal.finish("weekly", "2026-W40", "本周大纲")
    assert journal.note("weekly", "2026-W40")["status"] == "done"
    journal.close()


def test_run_ledger_and_lists(tmp_path):
    journal, _ = _stores(tmp_path)
    journal.claim("daily", "2026-10-02")
    journal.mark_attempt("daily", "2026-10-02", ok=True, detail="笔记 1200 字")
    journal.mark_attempt("monthly", "2026-09", ok=False, detail="周记不全，跳过")
    runs = journal.runs()
    assert len(runs) == 2 and runs[0]["kind"] == "monthly" and runs[0]["ok"] is False
    assert journal.latest_note("daily")["period_key"] == "2026-10-02"
    assert journal.note_map("daily")["2026-10-02"]["status"] == "running"
    journal.close()


def test_period_key_helpers():
    assert week_key(dt.date(2026, 10, 26)) == "2026-W44"  # 周一
    assert week_key(dt.date(2026, 10, 30)) == "2026-W44"  # 周五
    assert week_end(dt.date(2026, 10, 26)) == dt.date(2026, 11, 1)  # 该周周日
    assert month_key(dt.date(2026, 10, 15)) == "2026-10"
    assert month_end(dt.date(2026, 10, 15)) == dt.date(2026, 10, 31)
    assert month_end(dt.date(2026, 12, 1)) == dt.date(2026, 12, 31)


def test_due_daily_past_today_and_late_activity(tmp_path):
    journal, dialogue = _stores(tmp_path)
    for day in ("2026-10-01", "2026-10-02", "2026-10-03"):
        dialogue.append(1, "user", f"内容 {day}", now=dt.datetime.fromisoformat(day + "T12:00:00").astimezone())
    today = dt.date(2026, 10, 3)
    # 未到整理时刻：今天不算到期
    due = due_periods(journal, dialogue, today=today, now_hhmm="08:00", daily_time="23:30", max_catchup=3)
    assert due == [("daily", "2026-10-01"), ("daily", "2026-10-02")]
    # 到点：今天也到期
    due = due_periods(journal, dialogue, today=today, now_hhmm="23:31", daily_time="23:30", max_catchup=3)
    assert ("daily", "2026-10-03") in due
    # done + 无新活动：不再到期；done + 迟到消息：重新到期
    journal.claim("daily", "2026-10-02")
    journal.finish("daily", "2026-10-02", "笔记", covered_to_id=dialogue.max_id_for_day("2026-10-02"))
    due = due_periods(journal, dialogue, today=today, now_hhmm="23:31", daily_time="23:30", max_catchup=3)
    assert ("daily", "2026-10-02") not in due
    dialogue.append(1, "user", "23:40 的迟到消息", now=dt.datetime.fromisoformat("2026-10-02T23:40:00").astimezone())
    due = due_periods(journal, dialogue, today=today, now_hhmm="23:31", daily_time="23:30", max_catchup=3)
    assert ("daily", "2026-10-02") in due
    journal.close(); dialogue.close()


def test_due_catchup_is_bounded_oldest_first(tmp_path):
    journal, dialogue = _stores(tmp_path)
    for day in ("2026-10-01", "2026-10-02", "2026-10-03", "2026-10-04", "2026-10-05"):
        dialogue.append(1, "user", "x", now=dt.datetime.fromisoformat(day + "T12:00:00").astimezone())
    due = due_periods(journal, dialogue, today=dt.date(2026, 10, 6), now_hhmm="09:00",
                      daily_time="23:30", max_catchup=2)
    assert [p for k, p in due if k == "daily"] == ["2026-10-01", "2026-10-02"]
    journal.close(); dialogue.close()


def test_due_weekly_waits_for_all_daily(tmp_path):
    journal, dialogue = _stores(tmp_path)
    # 第 44 周（10-26 周一 ~ 11-01 周日）两天有活动；今天是下一周
    dialogue.append(1, "user", "a", now=dt.datetime.fromisoformat("2026-10-26T12:00:00").astimezone())
    dialogue.append(1, "user", "b", now=dt.datetime.fromisoformat("2026-10-28T12:00:00").astimezone())
    today = dt.date(2026, 11, 3)
    due = due_periods(journal, dialogue, today=today, now_hhmm="09:00", daily_time="23:30", max_catchup=5)
    assert ("weekly", "2026-W44") not in due  # 日笔记还没成稿
    for day in ("2026-10-26", "2026-10-28"):
        journal.claim("daily", day)
        journal.finish("daily", day, "笔记", covered_to_id=dialogue.max_id_for_day(day))
    due = due_periods(journal, dialogue, today=today, now_hhmm="09:00", daily_time="23:30", max_catchup=5)
    assert ("weekly", "2026-W44") in due
    journal.claim("weekly", "2026-W44")
    journal.finish("weekly", "2026-W44", "周记")
    due = due_periods(journal, dialogue, today=today, now_hhmm="09:00", daily_time="23:30", max_catchup=5)
    assert ("weekly", "2026-W44") not in due
    journal.close(); dialogue.close()


def test_due_monthly_consumes_weekly_with_straddle(tmp_path):
    journal, dialogue = _stores(tmp_path)
    # 2026-10-01（周四）属 W40（周一 09-28 ~ 周日 10-04）：跨界周计入 2026-09 与 2026-10 两个月
    dialogue.append(1, "user", "a", now=dt.datetime.fromisoformat("2026-10-01T12:00:00").astimezone())
    journal.claim("daily", "2026-10-01")
    journal.finish("daily", "2026-10-01", "笔记", covered_to_id=1)
    today = dt.date(2026, 11, 3)
    due = due_periods(journal, dialogue, today=today, now_hhmm="09:00", daily_time="23:30", max_catchup=5)
    # W40 周记没成稿 → 两个月记都不到期
    assert ("monthly", "2026-09") not in due and ("monthly", "2026-10") not in due
    journal.claim("weekly", "2026-W40")
    journal.finish("weekly", "2026-W40", "周记")
    due = due_periods(journal, dialogue, today=today, now_hhmm="09:00", daily_time="23:30", max_catchup=5)
    assert ("monthly", "2026-09") in due and ("monthly", "2026-10") in due
    for m in ("2026-09", "2026-10"):
        journal.claim("monthly", m)
        journal.finish("monthly", m, "月度大纲")
    due = due_periods(journal, dialogue, today=today, now_hhmm="09:00", daily_time="23:30", max_catchup=5)
    assert all(("monthly", m) not in due for m in ("2026-09", "2026-10"))
    journal.close(); dialogue.close()


def test_stats_counts_by_kind(tmp_path):
    journal, _ = _stores(tmp_path)
    journal.claim("daily", "2026-10-01")
    journal.finish("daily", "2026-10-01", "n")
    journal.claim("weekly", "2026-W40")
    journal.fail("weekly", "2026-W40", "e")
    stats = journal.stats()
    assert stats["daily"]["done"] == 1 and stats["weekly"]["failed"] == 1
    journal.close()
