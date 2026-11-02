"""工作区文件的修订快照（D13）：写前备份、可回滚。

为什么不用 `memory/revisions.py`：那套按"键段"分目录，键段只允许 ASCII 字母数字，
中文文件名直接写不进去；而且它的语义是"技能/常驻笔记的版本链"，与工作区文件
（可能有成千上万个）不是一回事——这里按路径哈希分目录，天然允许任意文件名。

只保留最近 `keep` 版：目标是"改坏了能找回上一版"，不是给整个工作区做版本控制。
"""

from __future__ import annotations

import base64
import datetime as dt
import hashlib
import json
from dataclasses import dataclass
from pathlib import Path

from ...system.atomic import atomic_write_text

MAX_SNAPSHOT_BYTES = 4 * 1024 * 1024
DEFAULT_KEEP = 20


@dataclass(frozen=True)
class Snapshot:
    rel: str
    rev: int
    created_at: str
    sha256: str
    size: int
    summary: str
    author: str

    def brief(self) -> str:
        return (
            f"rev {self.rev}：{self.summary or '无摘要'}（{self.created_at} · {self.size} 字节 · {self.sha256[:12]}）"
        )


class SnapshotTooLarge(RuntimeError):
    """文件大到不适合做快照：拒绝写入比"写了但回不去"更诚实。"""


class FileHistory:
    def __init__(self, root: Path, *, keep: int = DEFAULT_KEEP) -> None:
        self._root = Path(root)
        self._keep = max(int(keep), 1)

    @property
    def root(self) -> Path:
        return self._root

    def _dir(self, rel: str) -> Path:
        digest = hashlib.sha1(rel.casefold().encode("utf-8")).hexdigest()[:16]
        return self._root / digest

    # ---------------------------------------------------------------- 写

    def commit(self, rel: str, data: bytes, *, summary: str = "", author: str = "agent") -> Snapshot:
        if len(data) > MAX_SNAPSHOT_BYTES:
            raise SnapshotTooLarge(
                f"文件 {len(data)} 字节，超过可备份上限 {MAX_SNAPSHOT_BYTES} 字节：无法保证回滚，因此不写入"
            )
        digest = hashlib.sha256(data).hexdigest()
        latest = self.latest(rel)
        if latest is not None and latest.sha256 == digest:
            return latest
        rev = latest.rev + 1 if latest is not None else 1
        record = {
            "rel": rel,
            "rev": rev,
            "created_at": dt.datetime.now().astimezone().isoformat(timespec="seconds"),
            "sha256": digest,
            "size": len(data),
            "summary": summary,
            "author": author,
            "content_b64": base64.b64encode(data).decode("ascii"),
        }
        directory = self._dir(rel)
        directory.mkdir(parents=True, exist_ok=True)
        atomic_write_text(
            directory / f"{rev:04d}-{digest[:8]}.json",
            json.dumps(record, ensure_ascii=False),
        )
        self._prune(directory)
        return Snapshot(
            rel=rel,
            rev=rev,
            created_at=record["created_at"],
            sha256=digest,
            size=len(data),
            summary=summary,
            author=author,
        )

    def _prune(self, directory: Path) -> None:
        files = sorted(directory.glob("[0-9][0-9][0-9][0-9]-*.json"))
        for stale in files[: max(len(files) - self._keep, 0)]:
            try:
                stale.unlink()
            except OSError:
                pass

    # ---------------------------------------------------------------- 读

    def list(self, rel: str, limit: int | None = None) -> list[Snapshot]:
        directory = self._dir(rel)
        if not directory.is_dir():
            return []
        out: list[Snapshot] = []
        for path in sorted(directory.glob("[0-9][0-9][0-9][0-9]-*.json"), reverse=True):
            record = _load(path)
            if record is None or str(record.get("rel", "")) != rel:
                continue
            out.append(
                Snapshot(
                    rel=rel,
                    rev=int(record.get("rev", 0)),
                    created_at=str(record.get("created_at", "")),
                    sha256=str(record.get("sha256", "")),
                    size=int(record.get("size", 0)),
                    summary=str(record.get("summary", "")),
                    author=str(record.get("author", "")),
                )
            )
            if limit is not None and len(out) >= limit:
                break
        return out

    def latest(self, rel: str) -> Snapshot | None:
        found = self.list(rel, limit=1)
        return found[0] if found else None

    def read(self, rel: str, rev: int) -> bytes | None:
        directory = self._dir(rel)
        if not directory.is_dir():
            return None
        for path in sorted(directory.glob(f"{int(rev):04d}-*.json")):
            record = _load(path)
            if record is None or int(record.get("rev", 0)) != int(rev):
                continue
            payload = record.get("content_b64")
            if not isinstance(payload, str):
                return None
            try:
                return base64.b64decode(payload.encode("ascii"))
            except (ValueError, TypeError):
                return None
        return None


def _load(path: Path) -> dict | None:
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, UnicodeError, json.JSONDecodeError):
        return None
    return data if isinstance(data, dict) else None
