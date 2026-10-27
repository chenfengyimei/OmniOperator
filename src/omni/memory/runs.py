"""运行记录（run）与事件流（`state.db`）：一次"问—想—做—答"的可查账本。

为什么需要（D09）：

- 现在一次回合只活在一个线程栈里：网页关掉就看不见它，进程重启就查不到它，
  出了问题只能说"当时大概是那样"；
- 工具结果是一句话字符串，既分不清"没批准"和"执行失败"，也没法告诉界面
  "这一步有没有可能已经发生副作用"；
- 取消只能靠杀线程。

这里落三件事：**run 行**（谁、哪个会话、什么状态、停在哪一步）、
**事件流**（按 run 单调递增的 seq，客户端用 `after=` 续读，不重放回合）、
**重启对账**（上次没写完的 running 一律归入 interrupted，不假装成功）。
"""
from __future__ import annotations

import datetime as dt
import json
from dataclasses import dataclass
from typing import Any

from .state import StateError, StateStore

# 状态机：queued → running →（done | failed | cancelled | interrupted）
# waiting_approval 是 running 的子状态，单独记是为了界面上能直接显示"在等人点头"
STATUSES = frozenset(
    {"queued", "running", "waiting_approval", "done", "failed", "cancelled", "interrupted"}
)
TERMINAL = frozenset({"done", "failed", "cancelled", "interrupted"})


def _now() -> str:
    return dt.datetime.now().astimezone().isoformat(timespec="seconds")


@dataclass(frozen=True)
class Run:
    run_id: str
    session_id: int | None
    channel: str
    principal: str
    status: str
    created_at: str
    updated_at: str
    parent_run_id: str | None = None
    owner: str = ""
    phase: str = ""
    ended_at: str | None = None
    error_code: str = ""
    error_text: str = ""
    last_seq: int = 0

    @property
    def finished(self) -> bool:
        return self.status in TERMINAL

    def brief(self) -> dict[str, Any]:
        return {
            "run_id": self.run_id,
            "parent_run_id": self.parent_run_id,
            "session_id": self.session_id,
            "channel": self.channel,
            "principal": self.principal,
            "status": self.status,
            "phase": self.phase,
            "created_at": self.created_at,
            "updated_at": self.updated_at,
            "ended_at": self.ended_at,
            "error_code": self.error_code,
            "error_text": self.error_text,
            "last_seq": self.last_seq,
        }


@dataclass(frozen=True)
class RunEvent:
    run_id: str
    seq: int
    at: str
    kind: str
    data: dict[str, Any]

    def brief(self) -> dict[str, Any]:
        return {"seq": self.seq, "at": self.at, "kind": self.kind, "data": self.data}


class RunStore:
    """run 与事件的仓储。只做持久化，不知道 Agent/LLM 的存在。"""

    def __init__(self, state: StateStore) -> None:
        self._state = state

    # ---------------------------------------------------------------- 写

    def create(
        self,
        *,
        run_id: str,
        session_id: int | None,
        channel: str,
        principal: str,
        owner: str,
        parent_run_id: str | None = None,
        status: str = "running",
    ) -> Run:
        if status not in STATUSES:
            raise ValueError(f"未知运行状态：{status}")
        stamp = _now()
        with self._state._tx() as conn:
            conn.execute(
                "INSERT INTO runs(run_id, parent_run_id, session_id, channel, principal, owner,"
                " status, phase, created_at, updated_at) VALUES (?,?,?,?,?,?,?,?,?,?)",
                (
                    run_id,
                    parent_run_id,
                    None if session_id is None else int(session_id),
                    channel,
                    principal,
                    owner,
                    status,
                    "",
                    stamp,
                    stamp,
                ),
            )
        run = self.get(run_id)
        assert run is not None
        return run

    def append_event(
        self, run_id: str, kind: str, data: dict[str, Any] | None = None, **extra: Any
    ) -> int:
        """追加事件并返回它的 seq。

        `seq` 由同一写事务里的 `last_seq = last_seq + 1` 决定：并发追加不会撞号，
        也不会出现"事件比运行记录新"的中间态。
        """
        merged = dict(data or {}, **extra)
        payload = json.dumps(merged, ensure_ascii=False, sort_keys=True)
        stamp = _now()
        with self._state._tx() as conn:
            cur = conn.execute(
                "UPDATE runs SET last_seq = last_seq + 1, updated_at = ? WHERE run_id = ?",
                (stamp, run_id),
            )
            if cur.rowcount == 0:
                raise StateError(f"运行 {run_id} 不存在，无法记录事件")
            row = conn.execute(
                "SELECT last_seq FROM runs WHERE run_id = ?", (run_id,)
            ).fetchone()
            seq = int(row["last_seq"])
            conn.execute(
                "INSERT INTO run_events(run_id, seq, at, kind, data) VALUES (?,?,?,?,?)",
                (run_id, seq, stamp, kind, payload),
            )
        return seq

    def touch(
        self,
        run_id: str,
        *,
        status: str | None = None,
        phase: str | None = None,
        error_code: str | None = None,
        error_text: str | None = None,
    ) -> bool:
        if status is not None and status not in STATUSES:
            raise ValueError(f"未知运行状态：{status}")
        fields = ["updated_at = ?"]
        params: list[Any] = [_now()]
        for column, value in (
            ("status", status),
            ("phase", phase),
            ("error_code", error_code),
            ("error_text", error_text),
        ):
            if value is not None:
                fields.append(f"{column} = ?")
                params.append(value)
        params.append(run_id)
        with self._state._tx() as conn:
            return (
                conn.execute(
                    f"UPDATE runs SET {', '.join(fields)} WHERE run_id = ?", params
                ).rowcount
                > 0
            )

    def finish(
        self,
        run_id: str,
        status: str,
        *,
        error_code: str = "",
        error_text: str = "",
    ) -> bool:
        if status not in TERMINAL:
            raise ValueError(f"{status} 不是终态")
        stamp = _now()
        with self._state._tx() as conn:
            return (
                conn.execute(
                    "UPDATE runs SET status = ?, ended_at = ?, updated_at = ?,"
                    " error_code = ?, error_text = ?, phase = '' WHERE run_id = ?",
                    (status, stamp, stamp, error_code, error_text, run_id),
                ).rowcount
                > 0
            )

    def interrupt_stale(self, *, owner_kind: str, current_owner: str) -> list[str]:
        """启动对账：把"本类进程的上一次启动"留下的非终态运行归入 interrupted。

        为什么按 `owner_kind`（web/qq/cli）而不是全局：网页进程重启时，心跳进程手里的
        运行还在正常跑，不能替它宣布失败。同类进程全局只有一个，所以这个判定是可靠的。
        """
        prefix = f"{owner_kind}:"
        with self._state._tx() as conn:
            rows = conn.execute(
                "SELECT run_id, owner, status FROM runs WHERE status IN"
                " ('queued','running','waiting_approval') AND owner LIKE ?",
                (f"{prefix}%",),
            ).fetchall()
            holders = {str(row["run_id"]): str(row["owner"]) for row in rows}
            stale = [rid for rid, holder in holders.items() if holder != current_owner]
            stamp = _now()
            for run_id in stale:
                conn.execute(
                    "UPDATE runs SET status = 'interrupted', ended_at = ?, updated_at = ?,"
                    " error_code = 'process_restart',"
                    " error_text = '进程在运行未结束时退出：结果未知，未自动重试',"
                    " phase = '' WHERE run_id = ?",
                    (stamp, stamp, run_id),
                )
                conn.execute(
                    "UPDATE runs SET last_seq = last_seq + 1 WHERE run_id = ?", (run_id,)
                )
                row = conn.execute(
                    "SELECT last_seq FROM runs WHERE run_id = ?", (run_id,)
                ).fetchone()
                conn.execute(
                    "INSERT INTO run_events(run_id, seq, at, kind, data) VALUES (?,?,?,?,?)",
                    (
                        run_id,
                        int(row["last_seq"]),
                        stamp,
                        "interrupted",
                        json.dumps(
                            {
                                "reason": "process_restart",
                                "previous_owner": holders.get(run_id, ""),
                                "note": "重启不重放未知副作用的写操作",
                            },
                            ensure_ascii=False,
                        ),
                    ),
                )
        return stale

    # ---------------------------------------------------------------- 读

    def get(self, run_id: str) -> Run | None:
        rows = self._state._read("SELECT * FROM runs WHERE run_id = ?", (run_id,))
        return _row_to_run(rows[0]) if rows else None

    def list_recent(self, *, session_id: int | None = None, limit: int = 20) -> list[Run]:
        limit = max(1, min(int(limit), 200))
        if session_id is None:
            rows = self._state._read(
                "SELECT * FROM runs ORDER BY created_at DESC, rowid DESC LIMIT ?", (limit,)
            )
        else:
            rows = self._state._read(
                "SELECT * FROM runs WHERE session_id = ?"
                " ORDER BY created_at DESC, rowid DESC LIMIT ?",
                (int(session_id), limit),
            )
        return [_row_to_run(row) for row in rows]

    def events(
        self, run_id: str, *, after: int = 0, limit: int = 200
    ) -> list[RunEvent]:
        """续读：只返回 seq > after 的事件，客户端断线后不会重复消费。"""
        limit = max(1, min(int(limit), 1000))
        rows = self._state._read(
            "SELECT * FROM run_events WHERE run_id = ? AND seq > ? ORDER BY seq LIMIT ?",
            (run_id, int(after), limit),
        )
        out: list[RunEvent] = []
        for row in rows:
            try:
                data = json.loads(row["data"])
            except (TypeError, json.JSONDecodeError):
                data = {"_raw": str(row["data"])}
            out.append(
                RunEvent(
                    run_id=str(row["run_id"]),
                    seq=int(row["seq"]),
                    at=str(row["at"]),
                    kind=str(row["kind"]),
                    data=data if isinstance(data, dict) else {"_value": data},
                )
            )
        return out


def _row_to_run(row) -> Run:
    return Run(
        run_id=str(row["run_id"]),
        session_id=None if row["session_id"] is None else int(row["session_id"]),
        channel=str(row["channel"]),
        principal=str(row["principal"]),
        status=str(row["status"]),
        created_at=str(row["created_at"]),
        updated_at=str(row["updated_at"]),
        parent_run_id=None if row["parent_run_id"] is None else str(row["parent_run_id"]),
        owner=str(row["owner"]),
        phase=str(row["phase"]),
        ended_at=None if row["ended_at"] is None else str(row["ended_at"]),
        error_code=str(row["error_code"]),
        error_text=str(row["error_text"]),
        last_seq=int(row["last_seq"]),
    )
