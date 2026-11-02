"""人工发布草稿与发送身份账本；进程中断/结果未知不自动二次发布。"""

from __future__ import annotations

import hashlib
import json
import sqlite3
import time
import uuid

from .state import StateStore


class PublicationError(ValueError):
    pass


def content_hash(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


class PublicationStore:
    def __init__(self, state: StateStore, *, clock=time.time) -> None:
        self.state, self.clock = state, clock

    def create(self, *, session_id: int, kind: str, target: str, account: str, content: str, observation: dict) -> dict:
        identity, now = uuid.uuid4().hex, self.clock()
        with self.state._tx() as conn:
            if (
                conn.execute(
                    "SELECT COUNT(*) FROM publication_drafts WHERE status IN ('draft','sending','unknown')"
                ).fetchone()[0]
                >= 100
            ):
                raise PublicationError("未处理草稿已达 100 条，请先核查或丢弃")
            conn.execute(
                "INSERT INTO publication_drafts(id,session_id,kind,target,account,content,content_hash,observation,created_at,updated_at) VALUES(?,?,?,?,?,?,?,?,?,?)",
                (
                    identity,
                    session_id,
                    kind,
                    target,
                    account,
                    content,
                    content_hash(content),
                    json.dumps(observation, ensure_ascii=False),
                    now,
                    now,
                ),
            )
        return self.get(identity)

    def get(self, identity: str) -> dict | None:
        rows = self.state._read("SELECT * FROM publication_drafts WHERE id=?", (identity,))
        return dict(rows[0]) if rows else None

    def list(self, limit: int = 50) -> list[dict]:
        return [
            dict(row)
            for row in self.state._read(
                "SELECT * FROM publication_drafts ORDER BY created_at DESC LIMIT ?", (max(1, min(limit, 100)),)
            )
        ]

    def edit(self, identity: str, expected_hash: str, content: str) -> dict:
        if not content.strip() or len(content) > 2000:
            raise PublicationError("草稿内容不能为空或超过 2000 字")
        with self.state._tx() as conn:
            if (
                conn.execute(
                    "UPDATE publication_drafts SET content=?,content_hash=?,updated_at=? WHERE id=? AND status='draft' AND content_hash=?",
                    (content, content_hash(content), self.clock(), identity, expected_hash),
                ).rowcount
                != 1
            ):
                raise PublicationError("草稿已变化或进入发送阶段，禁止覆盖")
        return self.get(identity)

    def claim(self, identity: str, *, session_id: int, expected_hash: str, ttl_s: float) -> dict:
        token, now = uuid.uuid4().hex, self.clock()
        try:
            with self.state._tx() as conn:
                row = conn.execute("SELECT * FROM publication_drafts WHERE id=?", (identity,)).fetchone()
                if (
                    not row
                    or row["session_id"] != session_id
                    or row["content_hash"] != expected_hash
                    or row["status"] != "draft"
                ):
                    raise PublicationError("草稿身份/内容已变化或已尝试发送；禁止重复提交")
                if now - row["created_at"] > ttl_s:
                    raise PublicationError("草稿观察已过期，请重新观察并准备")
                if content_hash(row["content"]) != expected_hash:
                    raise PublicationError("草稿内容校验失败")
                conn.execute(
                    "UPDATE publication_drafts SET status='sending',attempt_token=?,updated_at=? WHERE id=?",
                    (token, now, identity),
                )
        except sqlite3.IntegrityError as exc:
            raise PublicationError("同账号/目标/内容已有发送或未知记录，先核查，不能自动重发") from exc
        return self.get(identity)

    def finish(self, identity: str, token: str, status: str, *, evidence: str = "", error: str = "") -> bool:
        if status not in {"sent", "unknown", "blocked"}:
            raise PublicationError("非法发布结果")
        with self.state._tx() as conn:
            return (
                conn.execute(
                    "UPDATE publication_drafts SET status=?,evidence=?,error=?,updated_at=?,attempt_token=NULL WHERE id=? AND status='sending' AND attempt_token=?",
                    (status, evidence[:4000], error[:500], self.clock(), identity, token),
                ).rowcount
                == 1
            )

    def resolve(self, identity: str, expected_hash: str, verdict: str) -> bool:
        """仅人工入口调用；未发也不复用旧审批，需新草稿和新批准。"""
        if verdict not in {"sent", "not_sent", "discarded"}:
            raise PublicationError("核查结果必须为 sent/not_sent/discarded")
        with self.state._tx() as conn:
            row = conn.execute("SELECT * FROM publication_drafts WHERE id=?", (identity,)).fetchone()
            if not row or row["content_hash"] != expected_hash:
                return False
            allowed = row["status"] == "unknown" or (
                row["status"] == "sending" and self.clock() - row["updated_at"] > 300
            )
            if verdict == "discarded":
                allowed = row["status"] in {"draft", "blocked", "not_sent"}
            if not allowed:
                return False
            conn.execute(
                "UPDATE publication_drafts SET status=?,attempt_token=NULL,updated_at=?,error='用户人工核查' WHERE id=?",
                (verdict, self.clock(), identity),
            )
        return True
