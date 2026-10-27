"""每日写操作预算：写走 UI 的第一道闸（bili-whale 的 quota 精神，xuexi/NOTES.md §4.3）。

状态存在 `state.db` 的 `budgets(scope, day, used)`：跨进程只靠一条条件更新
（`UPDATE ... WHERE used < limit`）判定，两个进程同时抢最后一份额度时只有一个
rowcount=1——这是 A09 要求的"额度预留/结算"，不再是读 JSON、加一、整份覆盖。

fail-closed：旧额度文件损坏、状态库忙、目录只读时一律**拒绝写**，并把原因说清楚；
绝不把"读不出来"当成 0 已用。
"""
from __future__ import annotations

import datetime as dt
from pathlib import Path

from ...memory.state import BROWSER_WRITE_SCOPE, StateBusy, StateStore, StateUnavailable


class WriteBudget:
    def __init__(
        self,
        state: StateStore,
        *,
        max_per_day: int = 50,
        scope: str = BROWSER_WRITE_SCOPE,
    ) -> None:
        self._state = state
        self._max = int(max_per_day)
        self._scope = scope

    @property
    def db_path(self) -> Path:
        """额度所在的数据库（测试与 doctor 用）。"""
        return self._state.path

    @property
    def max_per_day(self) -> int:
        return self._max

    def try_consume(self) -> tuple[bool, str]:
        """消耗一次写额度；返回 (是否成功, 说明)。"""
        today = dt.date.today().isoformat()
        reason = self._state.degraded_reason("budget")
        if reason:
            return False, (
                f"写操作已停止：{reason}。"
                "为避免把损坏的额度状态当成 0 已用，这里不做任何自动重置"
            )
        try:
            hit = self._state.consume_budget(self._scope, today, self._max)
        except StateBusy as exc:
            return False, f"写操作暂时不可用（{exc}）；请稍后重试，本次没有消耗额度"
        except StateUnavailable as exc:
            return False, f"写操作已停止：{exc}"
        if hit.ok:
            return True, f"写操作 {hit.used}/{hit.limit}（今日）"
        if hit.limit <= 0:
            return False, (
                "今日浏览器写操作额度上限为 0（未授权任何写操作）；"
                "可在 config.yaml 调整 max_writes_per_day"
            )
        return False, (
            f"今日浏览器写操作额度已用完（{hit.used}/{hit.limit}），"
            "为防失控已停止；明天自动恢复。也可在 config.yaml 调整 max_writes_per_day"
        )

    def usage_today(self) -> tuple[int, int]:
        """(已用, 上限)。状态不可用时按"已用满"上报，不显示成 0。"""
        if self._state.degraded_reason("budget"):
            return self._max, self._max
        try:
            return self._state.budget_used(self._scope, dt.date.today().isoformat()), self._max
        except StateUnavailable:
            return self._max, self._max
