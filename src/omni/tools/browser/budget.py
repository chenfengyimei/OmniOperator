"""每日写操作预算：写走 UI 的第一道闸（bili-whale 的 quota 精神，xuexi/NOTES.md §4.3）。

data/browser_budget.json 只记 {date, used}，跨进程共享（心跳/工作台/CLI 共用）。
超预算时返回明确错误并提示明天再试——宁可停也不硬闯。
"""
from __future__ import annotations

import datetime as dt
import json
import threading
from pathlib import Path

from ...config import DATA_DIR


class WriteBudget:
    _lock = threading.Lock()

    def __init__(self, path: Path | None = None, max_per_day: int = 50) -> None:
        self._path = path or (DATA_DIR / "browser_budget.json")
        self._max = max_per_day

    def try_consume(self) -> tuple[bool, str]:
        """消耗一次写额度；返回 (是否成功, 说明)。"""
        today = dt.date.today().isoformat()
        with self._lock:
            state = self._load()
            if state.get("date") != today:
                state = {"date": today, "used": 0}
            used = int(state.get("used", 0))
            if used >= self._max:
                return False, (
                    f"今日浏览器写操作额度已用完（{used}/{self._max}），"
                    "为防失控已停止；明天自动恢复。也可在 config.yaml 调整 max_writes_per_day"
                )
            state["used"] = used + 1
            self._save(state)
            return True, f"写操作 {used + 1}/{self._max}（今日）"

    def usage_today(self) -> tuple[int, int]:
        today = dt.date.today().isoformat()
        state = self._load()
        if state.get("date") != today:
            return 0, self._max
        return int(state.get("used", 0)), self._max

    def _load(self) -> dict:
        try:
            data = json.loads(self._path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            return {}
        return data if isinstance(data, dict) else {}

    def _save(self, state: dict) -> None:
        self._path.parent.mkdir(parents=True, exist_ok=True)
        self._path.write_text(
            json.dumps(state, ensure_ascii=False), encoding="utf-8"
        )
