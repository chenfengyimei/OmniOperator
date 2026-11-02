"""技能变更「已读」标记仓储（D18，`state.db`）。

待审阅 = 标记之后由 agent/review 写下的修订。标记只前进不后退（`MAX`），
刷新页面不会把已读变回未读；用户自己的编辑器保存与磁盘外部改动的快照
本来就不进待审阅队列，标记也不因它们变化。

表结构声明见 `memory/state_schema.py`；连接、事务与迁移执行归
`memory/state.py::StateStore`，本模块不新建数据库。
"""

from __future__ import annotations

from typing import TYPE_CHECKING

if TYPE_CHECKING:
    # 仅用于类型标注；运行期由 state.py 单向导入本模块，避免循环依赖。
    from .state import StateStore


class SkillAckStore:
    """技能修订的「已读」标记：只前进不后退。"""

    def __init__(self, state: StateStore) -> None:
        self._state = state

    def ack(self, skill: str, rev: int) -> int:
        """标记已读到 rev（只前进）；返回标记后的值。"""
        import datetime as dt

        from .jobs import to_iso

        now = to_iso(dt.datetime.now().astimezone())
        with self._state._tx() as conn:
            conn.execute(
                "INSERT INTO skill_acks(skill, rev, acked_at) VALUES (?,?,?)"
                " ON CONFLICT(skill) DO UPDATE SET"
                " rev = MAX(skill_acks.rev, excluded.rev), acked_at = excluded.acked_at",
                (skill, max(0, int(rev)), now),
            )
            row = conn.execute("SELECT rev FROM skill_acks WHERE skill = ?", (skill,)).fetchone()
            return int(row[0])

    def acked(self, skill: str) -> int:
        rows = self._state._read("SELECT rev FROM skill_acks WHERE skill = ?", (skill,))
        return int(rows[0][0]) if rows else 0

    def acked_all(self) -> dict[str, int]:
        rows = self._state._read("SELECT skill, rev FROM skill_acks")
        return {str(r[0]): int(r[1]) for r in rows}

    def forget(self, skill: str) -> None:
        """技能归档时清掉已读标记：同名技能以后重建时不会被旧标记吞掉。"""
        with self._state._tx() as conn:
            conn.execute("DELETE FROM skill_acks WHERE skill = ?", (skill,))
