"""MCP 工具目录指纹仓储（D19，`state.db`）。

服务升级换 schema 后，旧只读授权要重新检查。只读授权是用户按**当时**的
工具与参数形态给的；服务升级后同名工具可能完全换了解释。指纹不判断
"变好还是变坏"——变化即提醒，复查是人做的事。

表结构声明见 `memory/state_schema.py`；连接、事务与迁移执行归
`memory/state.py::StateStore`，本模块不新建数据库。
"""

from __future__ import annotations

import datetime as dt
from typing import TYPE_CHECKING

from .jobs import to_iso

if TYPE_CHECKING:
    # 仅用于类型标注；运行期由 state.py 单向导入本模块，避免循环依赖。
    from .state import StateStore


class MCPSchemaStore:
    """MCP 工具目录指纹：服务升级换 schema 后，旧只读授权要重新检查。"""

    def __init__(self, state: StateStore) -> None:
        self._state = state

    def observe(self, server: str, fingerprint: str, tool_count: int) -> str | None:
        """记录当前观察到的指纹；与存量不一致时返回旧指纹（= 漂移提醒），首次返回 None。"""
        now = to_iso(dt.datetime.now().astimezone())
        # 首次登记和漂移撤权在同一事务，不能让并发观察看到新指纹配旧确认。
        with self._state._tx() as conn:
            row = conn.execute(
                "SELECT fingerprint, confirmed_at FROM mcp_schemas WHERE server = ?",
                (server,),
            ).fetchone()
            if row is None:
                conn.execute(
                    "INSERT INTO mcp_schemas(server, fingerprint, tool_count,"
                    " confirmed_at, seen_at) VALUES (?,?,?,?,?)",
                    (server, fingerprint, int(tool_count), now, now),
                )
                return None  # 首次使用显式配置的只读名单，后续变化必须复查。
            stored = str(row[0])
            conn.execute(
                "UPDATE mcp_schemas SET fingerprint = ?, tool_count = ?, seen_at = ?,"
                " confirmed_at = CASE WHEN fingerprint = ? THEN confirmed_at ELSE NULL END"
                " WHERE server = ?",
                (fingerprint, int(tool_count), now, fingerprint, server),
            )
            return stored if stored != fingerprint else None

    def revoke(self, server: str) -> None:
        """重新授权可能更换远程账号；相同目录也必须重新复查只读许可。"""
        with self._state._tx() as conn:
            conn.execute("UPDATE mcp_schemas SET confirmed_at = NULL WHERE server = ?", (server,))

    def confirm(self, server: str, fingerprint: str) -> bool:
        """用户复查后确认当前指纹（清除漂移提醒）。指纹又变了就拒绝——确认的必须是看到的那份。"""
        now = to_iso(dt.datetime.now().astimezone())
        with self._state._tx() as conn:
            cursor = conn.execute(
                "UPDATE mcp_schemas SET confirmed_at = ? WHERE server = ? AND fingerprint = ?",
                (now, server, fingerprint),
            )
            return cursor.rowcount > 0

    def status(self, server: str) -> dict | None:
        rows = self._state._read(
            "SELECT fingerprint, tool_count, confirmed_at, seen_at FROM mcp_schemas WHERE server = ?",
            (server,),
        )
        if not rows:
            return None
        return {
            "fingerprint": str(rows[0][0]),
            "tool_count": int(rows[0][1]),
            "confirmed_at": rows[0][2],
            "seen_at": rows[0][3],
        }
