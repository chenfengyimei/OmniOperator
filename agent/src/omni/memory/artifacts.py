"""产物存储（D15 / A13）：截图、文件、报告统一按 artifact_id 管理。

为什么截图工具以前只返回路径（A13）：

- 模型从未收到过图像内容，"看图办事"全是空话；
- 路径就是一台"任意读文件"的后门：把 `C:\\Users\\me\\.ssh\\id_rsa` 当截图路径
  塞进结果里，下一轮它就在上下文里了；
- 散落的 `latest.png` 互相覆盖，没有保留期、没有归属、没有清理。

这里的纪律：

1. 产物只从**工具产生**（截图/导出），字节由代码写入 `data/artifacts/<id>.<ext>`，
   消费方拿到的永远是 16 进制 id，不是路径——想读文件得走 `ArtifactStore`，
   而 `ArtifactStore` 只认自己登记过的行；
2. 元数据进 `state.db`（v6）：sha256、大小、宽高、来源、归属会话/run、过期时间；
   读回时校验指纹，磁盘上被动过的产物直接判损坏，不当新数据用；
3. 跨会话读取被拒绝：会话 A 的截图不进会话 B 的上下文；
4. 过期清理只删登记在案的文件，"清理不删除仍使用中的产物"由调用方传入
   在用 id 集合（活跃 run 的消息里引用着的）来保证；
5. 总量有上限：超了就拒绝保存新产物（老的还在，别悄悄删用户的东西）。
"""

from __future__ import annotations

import base64
import datetime as dt
import hashlib
import re
import secrets
import sqlite3
from collections.abc import Iterable
from dataclasses import dataclass
from pathlib import Path

from .state import StateStore, StateUnavailable, parse_iso, to_iso


def _now_iso() -> str:
    return dt.datetime.now().astimezone().isoformat(timespec="seconds")


def _as_dt(text: str) -> dt.datetime:
    """把 ISO 串解析成**带时区**的时间点。

    库里的过期时间可能来自不同写法（`to_iso` 带偏移、手工 UPDATE 不带偏移、
    旧版本落库的裸串）。naive 与 aware 直接比会抛 TypeError，而过期判断
    一旦抛错，产物读取就整个挂掉——这里统一补本地时区，宁可比错也不让读崩。
    """
    value = parse_iso(text)
    if value.tzinfo is None:
        value = value.astimezone()
    return value


# 消息里的产物引用记号：工具结果文本携带，Agent 扫描后转成图像块
MARKER_RE = re.compile(r"\[artifact:([0-9a-f]{8,32})\]")

_MIME_EXT = {"image/png": ".png", "image/jpeg": ".jpg", "image/webp": ".webp", "image/gif": ".gif"}
_KNOWN_MIMES = frozenset(_MIME_EXT)


def marker(artifact_id: str) -> str:
    return f"[artifact:{artifact_id}]"


@dataclass(frozen=True)
class Artifact:
    artifact_id: str
    session_id: int | None
    run_id: str | None
    scope: str  # private / group
    kind: str  # screenshot / image / export / report
    source: str  # browser_screenshot / android_screenshot / ...
    mime: str
    size: int
    width: int | None
    height: int | None
    sha256: str
    created_at: str
    expires_at: str | None

    def brief(self) -> str:
        dims = f"，{self.width}x{self.height}" if self.width and self.height else ""
        return f"{self.kind}（{self.mime}{dims}，{self.size} 字节）{marker(self.artifact_id)}"


class ArtifactError(RuntimeError):
    def __init__(self, message: str, *, code: str = "artifact_error") -> None:
        self.code = code
        super().__init__(message)


class ArtifactStore:
    """产物登记与字节存取。`store` 是共享 state.db；`root` 是产物文件目录。"""

    def __init__(
        self, store: StateStore, root: Path, *, retention_days: int = 7, max_total_bytes: int = 500 * 1024 * 1024
    ) -> None:
        self._store = store
        self.root = Path(root)
        self.retention_days = max(1, int(retention_days))
        self.max_total_bytes = max(1024 * 1024, int(max_total_bytes))
        self.root.mkdir(parents=True, exist_ok=True)

    # ---------------------------------------------------------------- 保存

    def save_image(
        self,
        data: bytes,
        mime: str,
        *,
        session_id: int | None,
        run_id: str | None,
        scope: str = "private",
        kind: str = "screenshot",
        source: str = "",
        width: int | None = None,
        height: int | None = None,
    ) -> Artifact:
        if mime not in _KNOWN_MIMES:
            raise ArtifactError(f"不支持的图像类型：{mime}", code="bad_mime")
        if not data:
            raise ArtifactError("空内容不是产物", code="empty")
        artifact_id = secrets.token_hex(8)
        digest = hashlib.sha256(data).hexdigest()
        ext = _MIME_EXT[mime]
        path = self.root / f"{artifact_id}{ext}"
        total = self._total_bytes() + len(data)
        if total > self.max_total_bytes:
            raise ArtifactError(
                f"产物总量超上限（现有 {self._total_bytes() // 1048576}MB + "
                f"本次 {len(data) // 1024}KB > {self.max_total_bytes // 1048576}MB）；"
                "先在产物面板清理旧的，或调大 artifacts.max_total_mb",
                code="quota",
            )
        try:
            path.write_bytes(data)
        except OSError as exc:
            raise ArtifactError(f"产物写盘失败：{exc}", code="write_failed") from exc
        expires = None
        if self.retention_days > 0:
            expires = to_iso(parse_iso(_now_iso()) + dt.timedelta(days=self.retention_days))
        created = _now_iso()
        try:
            with self._store._tx() as conn:
                conn.execute(
                    "INSERT INTO artifacts(artifact_id, session_id, run_id, scope, kind,"
                    " source, mime, size, width, height, sha256, created_at, expires_at)"
                    " VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?)",
                    (
                        artifact_id,
                        session_id,
                        run_id,
                        scope,
                        kind,
                        source,
                        mime,
                        len(data),
                        width,
                        height,
                        digest,
                        created,
                        expires,
                    ),
                )
        except (sqlite3.Error, StateUnavailable) as exc:
            path.unlink(missing_ok=True)  # 登记失败就不留孤儿文件
            raise ArtifactError(f"产物登记失败：{exc}", code="db") from exc
        return Artifact(
            artifact_id=artifact_id,
            session_id=session_id,
            run_id=run_id,
            scope=scope,
            kind=kind,
            source=source,
            mime=mime,
            size=len(data),
            width=width,
            height=height,
            sha256=digest,
            created_at=created,
            expires_at=expires,
        )

    # ---------------------------------------------------------------- 读取

    def get(
        self, artifact_id: str, *, session_id: int | None = None, now: str | None = None, allow_expired: bool = False
    ) -> Artifact:
        """按 id 取产物。`session_id` 给了就做归属校验（跨会话拒绝）。

        `allow_expired=True` 用于"清理时被保留的产物"：它还在活跃 run 的上下文里，
        物理上没删，就不能因为时钟过了保留期而读不出来——那会让正在进行的对话
        中途断图。默认 False：过期就是过期，别拿陈年截图当现状。
        """
        aid = str(artifact_id).strip().lower()
        if not re.fullmatch(r"[0-9a-f]{8,32}", aid):
            raise ArtifactError(f"产物 id 不合法：{artifact_id}", code="bad_id")
        rows = self._store._read(
            "SELECT artifact_id, session_id, run_id, scope, kind, source, mime, size,"
            " width, height, sha256, created_at, expires_at FROM artifacts"
            " WHERE artifact_id = ?",
            (aid,),
        )
        if not rows:
            raise ArtifactError(f"产物不存在或已清理：{aid}", code="not_found")
        r = rows[0]
        art = Artifact(
            artifact_id=str(r[0]),
            session_id=r[1],
            run_id=r[2],
            scope=str(r[3]),
            kind=str(r[4]),
            source=str(r[5]),
            mime=str(r[6]),
            size=int(r[7] or 0),
            width=r[8],
            height=r[9],
            sha256=str(r[10]),
            created_at=str(r[11]),
            expires_at=r[12],
        )
        if art.session_id is not None and session_id is not None and art.session_id != session_id:
            raise ArtifactError(f"产物 {aid} 不属于会话 {session_id}（跨会话读取被拒绝）", code="cross_session")
        if not allow_expired and art.expires_at and _as_dt(art.expires_at) < _as_dt(now or _now_iso()):
            raise ArtifactError(f"产物 {aid} 已过期（{art.expires_at}）", code="expired")
        return art

    def read_bytes(self, artifact: Artifact) -> bytes:
        """读产物字节并校验指纹：磁盘上被换过/动过的文件不当新数据用。"""
        ext = _MIME_EXT.get(artifact.mime, ".bin")
        path = self.root / f"{artifact.artifact_id}{ext}"
        if not path.exists():
            raise ArtifactError(
                f"产物文件缺失：{artifact.artifact_id}（登记在库但文件不在，可能被手动删除）",
                code="file_missing",
            )
        data = path.read_bytes()
        digest = hashlib.sha256(data).hexdigest()
        if digest != artifact.sha256:
            raise ArtifactError(
                f"产物指纹不符：{artifact.artifact_id}（文件在磁盘上被改动过，拒绝当作可信内容）",
                code="hash_mismatch",
            )
        return data

    def data_url(self, artifact: Artifact) -> str:
        data = self.read_bytes(artifact)
        encoded = base64.b64encode(data).decode("ascii")
        return f"data:{artifact.mime};base64,{encoded}"

    # ---------------------------------------------------------------- 列表 / 清理

    def list_for_session(self, session_id: int, *, limit: int = 50) -> list[Artifact]:
        rows = self._store._read(
            "SELECT artifact_id, session_id, run_id, scope, kind, source, mime, size,"
            " width, height, sha256, created_at, expires_at FROM artifacts"
            " WHERE session_id = ? ORDER BY created_at DESC, artifact_id DESC LIMIT ?",
            (session_id, max(1, int(limit))),
        )
        return [self._row_to_artifact(r) for r in rows]

    def list_all(self, *, limit: int = 200) -> list[Artifact]:
        rows = self._store._read(
            "SELECT artifact_id, session_id, run_id, scope, kind, source, mime, size,"
            " width, height, sha256, created_at, expires_at FROM artifacts"
            " ORDER BY created_at DESC, artifact_id DESC LIMIT ?",
            (max(1, int(limit)),),
        )
        return [self._row_to_artifact(r) for r in rows]

    def cleanup(self, *, keep_ids: Iterable[str] = (), now: str | None = None) -> dict[str, int]:
        """删过期产物（文件 + 行）。`keep_ids` 是仍被引用的产物，过期也不删。"""
        current = _as_dt(now or _now_iso())
        keep = {str(k).lower() for k in keep_ids}
        rows = self._store._read("SELECT artifact_id, mime, expires_at FROM artifacts WHERE expires_at IS NOT NULL")
        removed = kept = 0
        for row in rows:
            aid = str(row[0])
            if not row[2] or _as_dt(str(row[2])) >= current:
                continue
            if aid in keep:
                kept += 1
                continue
            ext = _MIME_EXT.get(str(row[1]), ".bin")
            (self.root / f"{aid}{ext}").unlink(missing_ok=True)
            with self._store._tx() as conn:
                conn.execute("DELETE FROM artifacts WHERE artifact_id = ?", (aid,))
            removed += 1
        # 顺手清掉没有任何登记指向的孤儿文件（文件名是合法 id 才动）
        orphans = 0
        for path in self.root.iterdir():
            if not path.is_file():
                continue
            stem = path.stem
            if not re.fullmatch(r"[0-9a-f]{8,32}", stem):
                continue  # 不认识的文件名绝不删：宁可留着也不误删用户的东西
            hit = self._store._read("SELECT 1 FROM artifacts WHERE artifact_id = ?", (stem,))
            if not hit:
                path.unlink(missing_ok=True)
                orphans += 1
        return {"removed": removed, "kept_in_use": kept, "orphans": orphans}

    def delete(self, artifact_id: str) -> Artifact:
        """手动删除单个产物（工作台产物面板用）。

        过期的也允许删——清理面板不该因为保留期挡用户动作；
        顺序是先删登记行再删文件：中途失败顶多留一个孤儿文件，
        下次 cleanup 会收走（反过来会出现"有行无文件"的坏账）。
        """
        artifact = self.get(artifact_id, allow_expired=True)
        with self._store._tx() as conn:
            conn.execute("DELETE FROM artifacts WHERE artifact_id = ?", (artifact.artifact_id,))
        ext = _MIME_EXT.get(artifact.mime, ".bin")
        (self.root / f"{artifact.artifact_id}{ext}").unlink(missing_ok=True)
        return artifact

    def _total_bytes(self) -> int:
        rows = self._store._read("SELECT COALESCE(SUM(size), 0) FROM artifacts")
        return int(rows[0][0]) if rows else 0

    def _row_to_artifact(self, r: Iterable) -> Artifact:
        row = list(r)
        return Artifact(
            artifact_id=str(row[0]),
            session_id=row[1],
            run_id=row[2],
            scope=str(row[3]),
            kind=str(row[4]),
            source=str(row[5]),
            mime=str(row[6]),
            size=int(row[7] or 0),
            width=row[8],
            height=row[9],
            sha256=str(row[10]),
            created_at=str(row[11]),
            expires_at=row[12],
        )
