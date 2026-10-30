"""核心人物记忆（M6）：重要人物的账号、常驻档案、常驻记忆与临时事件。

职责：
- 人物 = 一个"人"的记忆单元：名字、重要度（owner/important/known）、账号
  （QQ / B站 UID，全局唯一——账号是"现在在跟谁说话"的注入查找键）、
  三类条目（archive=常驻档案：稳定特征；resident=常驻记忆：进行中的事；
  event=临时事件：可过期）；
- 每类条目有独立限额，超限报错并回传当前内容与超出量（照档案"限额+报错
  逼合并"协议）；archive/resident 要进提示词，写入前必须过防注入扫描；
- `card()` 产出注入用的压缩人物卡，体积有硬上限，超限从事件开始裁剪。

明确不做：不做删除的墓碑语义（人物记忆是工作笔记不是事实库）；模型侧不提供
删除人物/条目的工具（删除是工作台用户动作）；不做跨人物关系图。
"""
from __future__ import annotations

import datetime as dt
import re
import sqlite3
import threading
from pathlib import Path

from ..config import DATA_DIR
from .resident import injection_risk

ENTRY_KINDS = ("archive", "resident", "event")
IMPORTANCE = ("owner", "important", "known")
ACCOUNT_CHANNELS = ("qq", "bilibili")
_ACCOUNT_VALUE = re.compile(r"^\d{1,20}$")

_SCHEMA = """
CREATE TABLE IF NOT EXISTS people (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    name TEXT NOT NULL,
    importance TEXT NOT NULL DEFAULT 'known',
    note TEXT NOT NULL DEFAULT '',
    created_at TEXT NOT NULL,
    updated_at TEXT
);
CREATE TABLE IF NOT EXISTS person_accounts (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    person_id INTEGER NOT NULL REFERENCES people(id) ON DELETE CASCADE,
    channel TEXT NOT NULL,
    value TEXT NOT NULL,
    label TEXT NOT NULL DEFAULT '',
    created_at TEXT NOT NULL,
    UNIQUE(channel, value)
);
CREATE INDEX IF NOT EXISTS idx_person_accounts_person ON person_accounts(person_id);
CREATE TABLE IF NOT EXISTS person_entries (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    person_id INTEGER NOT NULL REFERENCES people(id) ON DELETE CASCADE,
    entry_kind TEXT NOT NULL,
    content TEXT NOT NULL,
    source TEXT NOT NULL DEFAULT 'chat',
    created_at TEXT NOT NULL,
    updated_at TEXT,
    expires_at TEXT
);
CREATE INDEX IF NOT EXISTS idx_person_entries_person ON person_entries(person_id, entry_kind, id);
"""


class PersonError(ValueError):
    """人物模块的业务错误：限额/重复/非法输入，消息可直接回显给用户或模型。"""


def _now() -> str:
    return dt.datetime.now().astimezone().isoformat(timespec="seconds")


def _locked(method):
    import functools

    @functools.wraps(method)
    def wrapper(self: "PersonStore", *args, **kwargs):
        with self._lock:
            return method(self, *args, **kwargs)

    return wrapper


def _entry_expired(expires_at: str | None, *, now: dt.datetime | None = None) -> bool:
    if not expires_at:
        return False
    try:
        limit = dt.datetime.fromisoformat(str(expires_at))
    except ValueError:
        return False
    current = now or dt.datetime.now().astimezone()
    if limit.tzinfo is None:
        limit = limit.replace(tzinfo=current.tzinfo)
    return limit <= current


class PersonStore:
    """核心人物的记忆单元（memory.db 新表）。"""

    def __init__(
        self,
        db_path: Path | None = None,
        *,
        archive_max_chars: int = 2000,
        resident_max_chars: int = 1200,
        event_max_chars: int = 500,
    ) -> None:
        path = db_path or (DATA_DIR / "memory.db")
        path.parent.mkdir(parents=True, exist_ok=True)
        self._limits = {
            "archive": int(archive_max_chars),
            "resident": int(resident_max_chars),
            "event": int(event_max_chars),
        }
        self._lock = threading.RLock()
        self._conn = sqlite3.connect(path, check_same_thread=False)
        self._conn.execute("PRAGMA journal_mode=WAL")
        self._conn.execute("PRAGMA busy_timeout=5000")
        self._conn.executescript(_SCHEMA)
        self._conn.execute("PRAGMA foreign_keys=ON")
        self._conn.commit()

    # ---------------------------------------------------------------- 人物

    @_locked
    def create_person(self, name: str, *, importance: str = "known", note: str = "") -> int:
        name = str(name or "").strip()
        if not name or len(name) > 60:
            raise PersonError("人物名字不能为空且不超过 60 字")
        if importance not in IMPORTANCE:
            raise PersonError(f"重要度必须是 {IMPORTANCE} 之一")
        if len(note) > 2000:
            raise PersonError("人物备注超过 2000 字上限")
        cur = self._conn.execute(
            "INSERT INTO people(name, importance, note, created_at) VALUES (?, ?, ?, ?)",
            (name, importance, str(note or ""), _now()),
        )
        self._conn.commit()
        return int(cur.lastrowid)

    @_locked
    def get_person(self, person_id: int) -> dict | None:
        row = self._conn.execute(
            "SELECT id, name, importance, note, created_at, COALESCE(updated_at,'')"
            " FROM people WHERE id = ?",
            (int(person_id),),
        ).fetchone()
        if not row:
            return None
        person = self._person_row(row)
        person["accounts"] = self._accounts_of(person_id)
        return person

    def _person_row(self, r) -> dict:
        return {
            "id": int(r[0]),
            "name": str(r[1]),
            "importance": str(r[2]),
            "note": str(r[3]),
            "created_at": str(r[4]),
            "updated_at": str(r[5]),
        }

    def _accounts_of(self, person_id: int) -> list[dict]:
        rows = self._conn.execute(
            "SELECT id, channel, value, COALESCE(label,'') FROM person_accounts"
            " WHERE person_id = ? ORDER BY id",
            (int(person_id),),
        ).fetchall()
        return [
            {"id": int(r[0]), "channel": str(r[1]), "value": str(r[2]), "label": str(r[3])}
            for r in rows
        ]

    @_locked
    def list_people(self) -> list[dict]:
        rows = self._conn.execute(
            "SELECT p.id, p.name, p.importance, p.note, p.created_at, COALESCE(p.updated_at,''),"
            " (SELECT COUNT(*) FROM person_accounts a WHERE a.person_id = p.id),"
            " (SELECT COUNT(*) FROM person_entries e WHERE e.person_id = p.id)"
            " FROM people p ORDER BY p.importance DESC, p.id ASC"
        ).fetchall()
        return [
            {
                "id": int(r[0]),
                "name": str(r[1]),
                "importance": str(r[2]),
                "note": str(r[3]),
                "created_at": str(r[4]),
                "updated_at": str(r[5]),
                "accounts": int(r[6]),
                "entries": int(r[7]),
            }
            for r in rows
        ]

    @_locked
    def update_person(
        self, person_id: int, *, name: str | None = None,
        importance: str | None = None, note: str | None = None,
    ) -> bool:
        if not self.get_person(int(person_id)):
            return False
        sets, params = [], []
        if name is not None:
            name = str(name).strip()
            if not name or len(name) > 60:
                raise PersonError("人物名字不能为空且不超过 60 字")
            sets.append("name = ?")
            params.append(name)
        if importance is not None:
            if importance not in IMPORTANCE:
                raise PersonError(f"重要度必须是 {IMPORTANCE} 之一")
            sets.append("importance = ?")
            params.append(importance)
        if note is not None:
            if len(note) > 2000:
                raise PersonError("人物备注超过 2000 字上限")
            sets.append("note = ?")
            params.append(str(note))
        if not sets:
            return True
        sets.append("updated_at = ?")
        params.extend([_now(), int(person_id)])
        self._conn.execute(f"UPDATE people SET {', '.join(sets)} WHERE id = ?", params)
        self._conn.commit()
        return True

    @_locked
    def delete_person(self, person_id: int) -> bool:
        cur = self._conn.execute("DELETE FROM people WHERE id = ?", (int(person_id),))
        self._conn.commit()
        return int(cur.rowcount) > 0

    @_locked
    def find_by_name(self, query: str) -> list[dict]:
        rows = self._conn.execute(
            "SELECT id, name, importance, note, created_at, COALESCE(updated_at,'')"
            " FROM people WHERE name LIKE ? ORDER BY importance DESC, id ASC LIMIT 20",
            (f"%{str(query).strip()}%",),
        ).fetchall()
        return [self._person_row(r) for r in rows]

    # ---------------------------------------------------------------- 账号

    @_locked
    def link_account(self, person_id: int, channel: str, value: str, *, label: str = "") -> int:
        if channel not in ACCOUNT_CHANNELS:
            raise PersonError(f"账号类型必须是 {ACCOUNT_CHANNELS} 之一")
        value = str(value or "").strip()
        if not _ACCOUNT_VALUE.match(value):
            raise PersonError("账号值必须是 1–20 位数字（QQ 号 / B站 UID）")
        if not self.get_person(int(person_id)):
            raise PersonError(f"人物 {person_id} 不存在")
        existing = self._conn.execute(
            "SELECT p.name FROM person_accounts a JOIN people p ON p.id = a.person_id"
            " WHERE a.channel = ? AND a.value = ?",
            (channel, value),
        ).fetchone()
        if existing:
            raise PersonError(f"{channel} 账号 {value} 已属于「{existing[0]}」")
        cur = self._conn.execute(
            "INSERT INTO person_accounts(person_id, channel, value, label, created_at)"
            " VALUES (?, ?, ?, ?, ?)",
            (int(person_id), channel, value, str(label or "")[:100], _now()),
        )
        self._conn.execute(
            "UPDATE people SET updated_at = ? WHERE id = ?", (_now(), int(person_id))
        )
        self._conn.commit()
        return int(cur.lastrowid)

    @_locked
    def unlink_account(self, account_id: int) -> bool:
        cur = self._conn.execute(
            "DELETE FROM person_accounts WHERE id = ?", (int(account_id),)
        )
        self._conn.commit()
        return int(cur.rowcount) > 0

    @_locked
    def lookup_account(self, channel: str, value: str) -> dict | None:
        """注入与工具查找的钥匙：按账号找人物（带完整人物卡素材）。"""
        row = self._conn.execute(
            "SELECT p.id FROM people p JOIN person_accounts a ON a.person_id = p.id"
            " WHERE a.channel = ? AND a.value = ? LIMIT 1",
            (str(channel), str(value).strip()),
        ).fetchone()
        return self.get_person(int(row[0])) if row else None

    # ---------------------------------------------------------------- 条目

    def _check_entry(self, entry_kind: str, content: str) -> str:
        if entry_kind not in ENTRY_KINDS:
            raise PersonError(f"条目类型必须是 {ENTRY_KINDS} 之一")
        content = str(content or "").strip()
        if not content:
            raise PersonError("条目内容不能为空")
        limit = self._limits[entry_kind]
        if len(content) > limit:
            raise PersonError(
                f"该条目超过 {entry_kind} 限额（{limit} 字，本次 {len(content)} 字）："
                "请先合并或精简现有条目再写入"
            )
        if entry_kind in ("archive", "resident"):
            risk = injection_risk(content)
            if risk:
                raise PersonError(risk)
        return content

    @_locked
    def add_entry(
        self, person_id: int, entry_kind: str, content: str, *,
        source: str = "chat", expires_at: str | None = None,
    ) -> int:
        if not self.get_person(int(person_id)):
            raise PersonError(f"人物 {person_id} 不存在")
        content = self._check_entry(entry_kind, content)
        if entry_kind == "event" and expires_at is not None:
            try:
                parsed = dt.datetime.fromisoformat(str(expires_at))
            except ValueError as exc:
                raise PersonError("过期时间必须是 ISO 时间（如 2026-11-05T09:00:00+08:00）") from exc
            if parsed <= dt.datetime.now().astimezone():
                raise PersonError("临时事件的过期时间必须在未来")
        cur = self._conn.execute(
            "INSERT INTO person_entries(person_id, entry_kind, content, source, created_at, expires_at)"
            " VALUES (?, ?, ?, ?, ?, ?)",
            (int(person_id), entry_kind, content, str(source or "chat"), _now(), expires_at),
        )
        self._conn.commit()
        return int(cur.lastrowid)

    @_locked
    def edit_entry(self, entry_id: int, content: str) -> bool:
        row = self._conn.execute(
            "SELECT person_id, entry_kind FROM person_entries WHERE id = ?",
            (int(entry_id),),
        ).fetchone()
        if not row:
            return False
        content = self._check_entry(str(row[1]), content)
        self._conn.execute(
            "UPDATE person_entries SET content = ?, updated_at = ? WHERE id = ?",
            (content, _now(), int(entry_id)),
        )
        self._conn.commit()
        return True

    @_locked
    def remove_entry(self, entry_id: int) -> bool:
        cur = self._conn.execute(
            "DELETE FROM person_entries WHERE id = ?", (int(entry_id),)
        )
        self._conn.commit()
        return int(cur.rowcount) > 0

    @_locked
    def entries(self, person_id: int, *, include_expired: bool = False) -> list[dict]:
        rows = self._conn.execute(
            "SELECT id, entry_kind, content, source, created_at, COALESCE(updated_at,''),"
            " COALESCE(expires_at,'') FROM person_entries WHERE person_id = ?"
            " ORDER BY entry_kind ASC, id ASC",
            (int(person_id),),
        ).fetchall()
        result: list[dict] = []
        for r in rows:
            expired = _entry_expired(str(r[6]) or None)
            if expired and not include_expired:
                continue
            result.append(
                {
                    "id": int(r[0]),
                    "entry_kind": str(r[1]),
                    "content": str(r[2]),
                    "source": str(r[3]),
                    "created_at": str(r[4]),
                    "updated_at": str(r[5]),
                    "expires_at": str(r[6]),
                    "expired": expired,
                }
            )
        return result

    @_locked
    def prune_expired_events(self, *, now: dt.datetime | None = None) -> int:
        """删除已过期的临时事件（整理循环周期调用；读侧本来就过滤）。"""
        current = now or dt.datetime.now().astimezone()
        removed = 0
        rows = self._conn.execute(
            "SELECT id, expires_at FROM person_entries WHERE entry_kind='event'"
            " AND expires_at IS NOT NULL AND expires_at != ''"
        ).fetchall()
        for entry_id, expires_at in rows:
            if _entry_expired(str(expires_at), now=current):
                self._conn.execute(
                    "DELETE FROM person_entries WHERE id = ?", (int(entry_id),)
                )
                removed += 1
        if removed:
            self._conn.commit()
        return removed

    # ---------------------------------------------------------------- 注入卡

    @_locked
    def card(self, person_id: int, char_limit: int, *, now: dt.datetime | None = None) -> dict | None:
        """压缩人物卡：超限从事件裁起——注入提示词的体积必须有硬上限。"""
        person = self.get_person(int(person_id))
        if person is None:
            return None
        limit = max(50, int(char_limit))
        items = self.entries(int(person_id))
        resident = [e["content"] for e in items if e["entry_kind"] == "resident"]
        archive = [e["content"] for e in items if e["entry_kind"] == "archive"]
        events = [
            f"{e['content']}（{e['expires_at'][:16] if e['expires_at'] else '未设过期'}）"
            for e in items
            if e["entry_kind"] == "event"
        ][:3]  # 最近三条就够注入用，多了占体积

        resident_text = "\n".join(resident)
        archive_text = "\n".join(archive)
        events_text = "\n".join(events)
        card = {
            "id": person["id"],
            "name": person["name"],
            "importance": person["importance"],
            "resident": resident_text,
            "archive": archive_text,
            "events": events_text,
        }

        def size() -> int:
            return sum(len(v) for k, v in card.items() if isinstance(v, str) and k != "id")

        if size() <= limit:
            return card
        # 体积超限：按 events → archive → resident 的顺序裁剪（resident 最"像人"，留到最后）
        card["events"] = ""
        if size() > limit and card["archive"]:
            rest = size() - len(card["archive"])
            budget = max(0, limit - rest)
            card["archive"] = card["archive"][:budget] + ("…" if budget < len(card["archive"]) else "")
        if size() > limit and card["resident"]:
            rest = size() - len(card["resident"])
            budget = max(0, limit - rest)
            card["resident"] = card["resident"][:budget] + ("…" if budget < len(card["resident"]) else "")
        return card

    @_locked
    def stats(self) -> dict:
        row = self._conn.execute("SELECT COUNT(*) FROM people").fetchone()
        accounts = self._conn.execute("SELECT COUNT(*) FROM person_accounts").fetchone()
        events = self._conn.execute(
            "SELECT COUNT(*) FROM person_entries WHERE entry_kind='event'"
        ).fetchone()
        return {
            "people": int(row[0]),
            "accounts": int(accounts[0]),
            "events": int(events[0]),
        }

    def close(self) -> None:
        with self._lock:
            self._conn.close()
