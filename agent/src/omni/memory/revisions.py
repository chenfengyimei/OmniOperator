"""内容修订历史（D08）：技能与常驻记忆的"可回滚快照"。

为什么需要：`SKILL.md` 只有 `version: N` 这个整数，正文改坏了就找不回来；
模型一次 `skill_rewrite` 就足以覆盖掉用户手工写的整节内容。这里保存**每个真实存在过的
内容**（按 sha256 去重），所以：

- 回滚 = 取旧修订内容重新写入，**产生新修订**（`version` 继续加，不改小）；
- 外部编辑（用户用编辑器改了文件）也会在下次写入前被快照进来，不会因为"没走工具"就丢；
- 每个修订同时记录当时的 `references/` 清单（文件 → sha256），能说清"附件是否跟着这一版"。

存储形态：`<root>/<key 各段>/<rev:04d>-<sha 前 8 位>.json`，一个修订一个文件，
写入走 `atomic_write_text`（不是追加日志）——追加日志写坏一行会让整份历史不可读。
"""
from __future__ import annotations

import datetime as dt
import hashlib
import json
import re
from collections.abc import Iterable
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from ..system.atomic import atomic_write_text

KEY_SEGMENT_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]{0,63}$")
MAX_CONTENT_BYTES = 512 * 1024
DEFAULT_KEEP = 50


class RevisionError(ValueError):
    """修订记录的输入不合法（键名/内容过大）。"""


@dataclass(frozen=True)
class Revision:
    """一条修订的元数据（不含正文；正文用 `RevisionStore.read` 取）。"""

    key: str
    rev: int
    created_at: str
    author: str
    source: str
    summary: str
    sha256: str
    size: int
    version: int | None = None
    applied: bool = True
    references: dict[str, str] = field(default_factory=dict)

    def brief(self) -> str:
        """给工具输出用的单行摘要。"""
        mark = "" if self.applied else "（未落盘）"
        ver = f"v{self.version} " if self.version is not None else ""
        return (
            f"rev {self.rev}{mark}：{ver}{self.summary or '无摘要'}"
            f"（{self.created_at} · {self.author}/{self.source or '未知来源'} · {self.size} 字节）"
        )


def _now() -> str:
    return dt.datetime.now().astimezone().isoformat(timespec="seconds")


class RevisionStore:
    """按 key 分目录保存内容快照；调用方负责在持有资源锁时使用。"""

    def __init__(self, root: Path, *, keep: int = DEFAULT_KEEP) -> None:
        self._root = Path(root)
        self._keep = max(int(keep), 1)

    @property
    def root(self) -> Path:
        return self._root

    # ---------------------------------------------------------------- 路径

    def _key_parts(self, key: str) -> list[str]:
        parts = [p for p in str(key or "").split("/") if p]
        if not parts or len(parts) > 4:
            raise RevisionError(f"修订键不合法：{key!r}（应为 1–4 段相对路径）")
        for part in parts:
            if not KEY_SEGMENT_RE.match(part) or part in {".", ".."}:
                raise RevisionError(f"修订键不合法：{key!r}（段 {part!r} 含不允许的字符）")
        return parts

    def _dir(self, key: str) -> Path:
        return self._root.joinpath(*self._key_parts(key))

    # ---------------------------------------------------------------- 写

    def commit(
        self,
        key: str,
        content: str,
        *,
        author: str = "agent",
        source: str = "",
        summary: str = "",
        version: int | None = None,
        applied: bool = True,
        references: dict[str, str] | None = None,
    ) -> Revision:
        """保存一条修订；内容与最近一条完全相同则不重复记（历史记"内容版本"）。"""
        text = content if isinstance(content, str) else str(content)
        data = text.encode("utf-8")
        if len(data) > MAX_CONTENT_BYTES:
            raise RevisionError(f"内容超过 {MAX_CONTENT_BYTES} 字节，拒绝存历史")
        directory = self._dir(key)
        digest = hashlib.sha256(data).hexdigest()
        latest = self.latest(key)
        if (
            latest is not None
            and latest.sha256 == digest
            and latest.version == version
            and latest.applied == applied
        ):
            return latest
        rev = (latest.rev + 1) if latest is not None else 1
        record = {
            "key": key,
            "rev": rev,
            "created_at": _now(),
            "author": author,
            "source": source,
            "summary": summary,
            "sha256": digest,
            "size": len(data),
            "version": version,
            "applied": bool(applied),
            "references": dict(references or {}),
            "content": text,
        }
        directory.mkdir(parents=True, exist_ok=True)
        atomic_write_text(
            directory / f"{rev:04d}-{digest[:8]}.json",
            json.dumps(record, ensure_ascii=False, indent=1),
        )
        self._prune(directory)
        return Revision(
            key=key,
            rev=rev,
            created_at=record["created_at"],
            author=author,
            source=source,
            summary=summary,
            sha256=digest,
            size=len(data),
            version=version,
            applied=bool(applied),
            references=dict(references or {}),
        )

    def _prune(self, directory: Path) -> None:
        """只保留最近 `keep` 条：历史是"能回滚"，不是"永久归档"。"""
        files = sorted(directory.glob("[0-9][0-9][0-9][0-9]-*.json"))
        for stale in files[: max(len(files) - self._keep, 0)]:
            try:
                stale.unlink()
            except OSError:
                pass

    # ---------------------------------------------------------------- 读

    def list(self, key: str, limit: int | None = None) -> list[Revision]:
        """新→旧；损坏的记录跳过（不让一条坏文件毁掉整段历史）。"""
        directory = self._dir(key)
        if not directory.is_dir():
            return []
        out: list[Revision] = []
        for path in sorted(directory.glob("[0-9][0-9][0-9][0-9]-*.json"), reverse=True):
            record = _load(path)
            if record is None:
                continue
            out.append(_to_revision(record))
            if limit is not None and len(out) >= limit:
                break
        return out

    def latest(self, key: str) -> Revision | None:
        found = self.list(key, limit=1)
        return found[0] if found else None

    def read(self, key: str, rev: int) -> tuple[Revision, str] | None:
        directory = self._dir(key)
        if not directory.is_dir():
            return None
        for path in sorted(directory.glob(f"{int(rev):04d}-*.json")):
            record = _load(path)
            if record is None:
                continue
            return _to_revision(record), str(record.get("content", ""))
        return None


def _load(path: Path) -> dict[str, Any] | None:
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, UnicodeError, json.JSONDecodeError):
        return None
    return data if isinstance(data, dict) else None


def _to_revision(record: dict[str, Any]) -> Revision:
    refs = record.get("references")
    return Revision(
        key=str(record.get("key", "")),
        rev=int(record.get("rev", 0)),
        created_at=str(record.get("created_at", "")),
        author=str(record.get("author", "")),
        source=str(record.get("source", "")),
        summary=str(record.get("summary", "")),
        sha256=str(record.get("sha256", "")),
        size=int(record.get("size", 0)),
        version=record.get("version"),
        applied=bool(record.get("applied", True)),
        references={str(k): str(v) for k, v in (refs or {}).items()},
    )


def reference_drift(
    recorded: dict[str, str], current: dict[str, str]
) -> list[str]:
    """比对"修订时的附件清单"与"现在的附件清单"；返回可读差异列表。"""
    drift: list[str] = []
    for path in sorted(set(recorded) | set(current)):
        old = recorded.get(path)
        new = current.get(path)
        if old is None:
            drift.append(f"新增 {path}")
        elif new is None:
            drift.append(f"删除 {path}")
        elif old != new:
            drift.append(f"改动 {path}")
    return drift


def manifest(files: Iterable[tuple[str, bytes]]) -> dict[str, str]:
    """(相对路径, 内容) → {相对路径: sha256}。"""
    return {rel: hashlib.sha256(data).hexdigest() for rel, data in files}
