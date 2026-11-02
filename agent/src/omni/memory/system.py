"""记忆系统门面：六个记忆模块对外的单点聚合（组合根纪律，无业务逻辑）。

职责：
- 聚合对话全量 / 会话全量 / 日·周·月笔记 / 核心人物四个记忆单元的引用，
  给工作台总览、Web API 与整理编排一个统一入口；
- `status()` 输出各模块的只读状态（条数/天数/笔记成稿/人物数）。

明确不做：不写任何数据（写入归各记忆单元）；不做检索/整理编排
（检索归工具层，整理归 core/journal.py）；不替代 Runtime 持有各单元。
"""

from __future__ import annotations

from dataclasses import dataclass

from .dialogue import DialogueLog
from .journal import JournalStore
from .people import PersonStore
from .session_log import SessionLog


@dataclass
class MemorySystem:
    dialogue: DialogueLog
    session_log: SessionLog
    journal: JournalStore
    people: PersonStore

    def status(self) -> dict:
        """各模块只读状态（总览页与 /api/memory-system/overview 的数据源）。"""
        try:
            journal_stats = self.journal.stats()
        except Exception:  # noqa: BLE001 —— 状态聚合不允许被单模块故障拖垮
            journal_stats = {}
        for kind in ("daily", "weekly", "monthly"):
            journal_stats.setdefault(kind, {"total": 0, "done": 0, "failed": 0, "running": 0})
        notes: dict = {}
        for kind in ("daily", "weekly", "monthly"):
            latest = self.journal.latest_note(kind)
            if latest:
                notes[kind] = {
                    "period_key": latest["period_key"],
                    "status": latest["status"],
                    "finished_at": latest["finished_at"],
                    "chars": len(latest["content"]),
                }
        return {
            "modules": {
                "dialogue": self.dialogue.stats(),
                "session_log": self.session_log.stats(),
                "journal": journal_stats,
                "people": self.people.stats(),
            },
            "latest_notes": notes,
        }

    def close(self) -> None:
        for store in (self.dialogue, self.session_log, self.journal, self.people):
            try:
                store.close()
            except Exception:  # noqa: BLE001, S110  —— 关闭尽力而为，不互相阻塞
                pass
