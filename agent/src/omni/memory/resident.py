"""常驻小记忆（Hermes MEMORY.md 式，NOTES §1.3a）：跨会话冻结快照注入系统提示词。

与档案（USER.md 等价物）的分工：档案=用户是谁（稳定特征），常驻记忆=
agent 的工作笔记（进行中的事、约定、教训）。

限额协议（抄 Hermes memory_tool_store）：
- 条目以 "§" 独行分隔，整文件限额（默认 2200 字符）；
- 写超限 → 报错并回传当前全部条目+超出量，逼调用方**当轮**先合并再重试
  （"限额 + 结构化报错 = 自整理"，比无限追加更会长出纪律）；
- 系统提示词里以「冻结快照 + 用量头」注入；本轮对话中途写入只落盘、
  不改本轮 prompt（下一轮生效），保住单轮内系统提示词字节稳定。

D08 补的一致性：主对话、后台 review、工作台是**不同线程/不同进程**，
"读全部条目 → 改一条 → 写回全部" 中间被插一刀就会丢写。现在整段读改写
在跨进程文件锁里完成，写入走唯一临时文件 + 原子替换；改文件前把当前内容
记进历史，删错了还能找回来。读不到文件时**拒绝写**（绝不把"读失败"当"空文件"覆盖）。
"""

from __future__ import annotations

import os
import re
from pathlib import Path

from ..config import DATA_DIR
from ..system.atomic import atomic_write_text
from ..system.filelock import FileLock, LockTimeout, lock_beside
from ..system.path_guard import digest as _digest
from .revisions import Revision, RevisionError, RevisionStore

ENTRY_DELIMITER = "\n§\n"
BLOCK_HEADER = "MEMORY（你的常驻工作笔记：跨会话生效；写满会被拒绝并要求先合并）"

# 写入前扫描（Hermes _scan_memory_content 同思路，保守清单——只拦确定性模式）：
# 常驻记忆每轮注入系统提示词，投毒条目会跨会话持续生效，必须在入口拦下。
_INJECTION_RE = re.compile(
    r"("
    r"ignore (?:all |any |your )?(?:previous|prior|above|earlier|original) "
    r"(?:instructions|prompts|rules|directions)"
    r"|disregard (?:all |any )?(?:previous|prior|above) (?:instructions|rules|guidance)"
    r"|忽略(?:并?无视)?(?:之前|以上|上面|先前|前面)的?(?:所有|全部)?(?:系统|安全)?(?:指令|提示词?|规则)"
    r"|无视(?:之前|以上|先前|前面)的?(?:所有)?(?:系统|安全)?(?:指令|提示词?|规则)"
    r"|(?:输出|透露|打印|复述|泄露)(?:一?下)?你的?(?:系统提示词?|系统指令|system prompt)"
    r"|reveal.{0,20}system prompt|print.{0,20}system prompt"
    r")",
    re.IGNORECASE,
)


def injection_risk(content: str) -> str:
    """命中注入/外泄模式返回拒绝说明，否则空串。"""
    m = _INJECTION_RE.search(content)
    if m:
        return (
            f"内容疑似提示注入（{m.group(0)[:50]!r}）：常驻记忆每轮注入系统提示词，"
            "此类条目会跨会话持续生效，拒绝写入。如确属正当内容请改写措辞"
        )
    return ""


class _Unreadable(RuntimeError):
    """文件存在但读不出来（权限/编码/占用）：写操作必须就此停手。"""


class ResidentMemory:
    """文件存储（data/MEMORY.md），跨进程锁 + 原子写 + 修订历史。"""

    def __init__(
        self,
        path: Path | None = None,
        char_limit: int = 2200,
        *,
        history_root: Path | None = None,
        owner: str = "",
        lock_timeout_s: float = 8.0,
        history_keep: int = 50,
    ) -> None:
        self._path = Path(path or (DATA_DIR / "MEMORY.md"))
        self._limit = char_limit
        self._owner = owner or f"pid:{os.getpid()}"
        self._lock_timeout = float(lock_timeout_s)
        self._history = RevisionStore(
            Path(history_root) if history_root else self._path.parent / "history",
            keep=history_keep,
        )
        self._seen: str | None = None  # 上次看到的内容指纹（按编号改条目时的 CAS 基线）

    # ---------- 基础 ----------

    def _read_raw(self) -> bytes | None:
        """None 表示文件不存在（合法空）；读不出来则抛 _Unreadable。"""
        try:
            return self._path.read_bytes()
        except FileNotFoundError:
            return None
        except OSError as exc:
            raise _Unreadable(f"读取常驻记忆失败（{exc}）") from exc

    def _read_entries(self, *, strict: bool = False) -> list[str]:
        try:
            raw = self._read_raw()
        except _Unreadable:
            if strict:
                raise
            return []  # 只读展示降级；写路径必须用 strict=True
        if raw is None:
            return []
        try:
            text = raw.decode("utf-8")
        except UnicodeError as exc:
            if strict:
                raise _Unreadable("常驻记忆不是 UTF-8 文本") from exc
            return []
        self._seen = _digest(raw)
        return [e.strip() for e in text.split(ENTRY_DELIMITER) if e.strip()]

    def _lock(self) -> FileLock:
        return FileLock(lock_beside(self._path), timeout_s=self._lock_timeout, owner=self._owner)

    def _write_entries(
        self,
        entries: list[str],
        *,
        author: str,
        source: str,
        summary: str,
        content: str | None = None,
    ) -> Revision:
        text = content if content is not None else ENTRY_DELIMITER.join(entries)
        try:
            atomic_write_text(self._path, text)
        except OSError as exc:
            raise _Unreadable(f"写入常驻记忆失败（{exc}）；原文件保持原样") from exc
        rev = self._history.commit(self._key(), text, author=author, source=source, summary=summary)
        self._seen = _digest(text.encode("utf-8"))
        return rev

    def _char_count(self, entries: list[str]) -> int:
        return len(ENTRY_DELIMITER.join(entries))

    def _usage(self, entries: list[str]) -> str:
        used = self._char_count(entries)
        pct = min(100, int(used / self._limit * 100)) if self._limit else 0
        return f"{pct}% — {used}/{self._limit} 字符"

    # ---------- 历史 ----------

    def _key(self) -> str:
        return f"resident/{self._path.name}"

    @property
    def history_root(self) -> Path:
        return self._history.root

    def history(self, limit: int | None = None) -> list[Revision]:
        try:
            return self._history.list(self._key(), limit=limit)
        except RevisionError:
            return []

    def revision(self, rev: int) -> tuple[Revision, str] | None:
        try:
            return self._history.read(self._key(), int(rev))
        except RevisionError:
            return None

    def rollback(self, rev: int, *, author: str = "agent", source: str = "resident_rollback") -> str:
        """回到某个历史修订（按整文件内容恢复，作为**新修订**记入历史）。"""
        try:
            with self._lock():
                try:
                    self._read_entries(strict=True)
                except _Unreadable as exc:
                    return f"错误：{exc}，本次未修改"
                found = self.revision(rev)
                if found is None:
                    return f"错误：没有 rev {rev}（先看常驻记忆历史）"
                text = found[1]
                entries = [e.strip() for e in text.split(ENTRY_DELIMITER) if e.strip()]
                problem = self._check_entries(entries)
                if problem:
                    return f"错误：rev {rev} 未通过校验，拒绝回滚：{problem}"
                self._write_entries(
                    entries,
                    author=author,
                    source=source,
                    summary=f"回滚到 rev {rev}",
                    content=text,
                )
        except (LockTimeout, _Unreadable) as exc:
            return f"错误：{exc}"
        return f"已把常驻记忆回滚到 rev {rev}（{self._usage(entries)}）"

    # ---------- 注入（冻结快照） ----------

    def block(self) -> str:
        """系统提示词用：空则空串，否则 头+用量+全部条目（§ 分隔）。"""
        try:
            entries = self._read_entries()
        except _Unreadable as exc:  # 纯读路径也如实说，而不是让记忆"凭空消失"
            return f"MEMORY（常驻工作笔记读取失败：{exc}）"
        if not entries:
            return ""
        return f"{BLOCK_HEADER}（{self._usage(entries)}）\n" + ENTRY_DELIMITER.join(entries)

    # ---------- 工具面 ----------

    def entries(self) -> list[tuple[int, str]]:
        """工作台展示用：带 1 起始编号的条目。"""
        return [(i, e) for i, e in enumerate(self._read_entries(), 1)]

    @property
    def limit(self) -> int:
        return self._limit

    def usage(self) -> str:
        return self._usage(self._read_entries())

    def view(self) -> str:
        """agent 视角的清单：带编号（replace/remove 用编号寻址）。"""
        entries = self._read_entries()
        if not entries:
            return f"常驻记忆为空（限额 {self._limit} 字符）。用 add 记录跨会话仍需要的事：进行中的约定、教训、下一步。"
        lines = [f"常驻记忆（{self._usage(entries)}）："]
        lines += [f"[{i}] {e}" for i, e in enumerate(entries, 1)]
        return "\n".join(lines)

    def add(
        self,
        content: str,
        *,
        author: str = "agent",
        source: str = "resident_memory",
        evidence: str = "",
    ) -> str:
        """`evidence`（原文摘录）为兼容 owner 门面而存在，这里没有出处列可落，接受但忽略。

        旧文件式常驻记忆只服务迁移与测试；生产路径走 OwnerMemory
        （条目落在 person_entries，带 source_ref/evidence）。
        """
        content = (content or "").strip()
        if not content:
            return "错误：content 不能为空"
        if risk := injection_risk(content):
            return f"错误：{risk}"
        try:
            with self._lock():
                entries, problem = self._load_for_write(check_stale=False, author=author, source=source)
                if problem:
                    return problem
                merged = ENTRY_DELIMITER.join(entries + [content])
                if len(merged) > self._limit:
                    over = len(merged) - self._limit
                    return (
                        f"错误：常驻记忆已 {self._usage(entries)}；新增这条（{len(content)} 字符）"
                        f"将超出限额 {over} 字符。请先用 replace 合并/精简下面这些旧条目腾出空间，"
                        f"再重试 add——限额按最终结果检查：\n"
                        + "\n§\n".join(f"[{i}] {e}" for i, e in enumerate(entries, 1))
                    )
                self._write_entries(entries + [content], author=author, source=source, summary="add")
        except (LockTimeout, _Unreadable) as exc:
            return f"错误：{exc}"
        return f"已写入常驻记忆（{self._usage(self._read_entries())}）"

    def replace(
        self,
        entry_id: int,
        content: str,
        *,
        author: str = "agent",
        source: str = "resident_memory",
        evidence: str = "",
    ) -> str:
        """`evidence` 同 add：为兼容 owner 门面的方法面而存在，这里接受但忽略。"""
        content = (content or "").strip()
        if not content:
            return "错误：content 不能为空（要删条目用 remove）"
        if risk := injection_risk(content):
            return f"错误：{risk}"
        try:
            with self._lock():
                entries, problem = self._load_for_write(check_stale=True, author=author, source=source)
                if problem:
                    return problem
                if not (1 <= entry_id <= len(entries)):
                    return f"错误：不存在第 {entry_id} 条（先 view 查看编号）"
                entries[entry_id - 1] = content
                if self._char_count(entries) > self._limit:
                    return (
                        f"错误：替换后 {self._char_count(entries)}/{self._limit} 字符超限，未写入。请给出更精简的内容"
                    )
                self._write_entries(entries, author=author, source=source, summary=f"replace [{entry_id}]")
        except (LockTimeout, _Unreadable) as exc:
            return f"错误：{exc}"
        return f"已更新第 {entry_id} 条（{self._usage(entries)}）"

    def remove(self, entry_id: int, *, author: str = "agent", source: str = "resident_memory") -> str:
        try:
            with self._lock():
                entries, problem = self._load_for_write(check_stale=True, author=author, source=source)
                if problem:
                    return problem
                if not (1 <= entry_id <= len(entries)):
                    return f"错误：不存在第 {entry_id} 条（先 view 查看编号）"
                removed = entries.pop(entry_id - 1)
                self._write_entries(entries, author=author, source=source, summary=f"remove [{entry_id}]")
        except (LockTimeout, _Unreadable) as exc:
            return f"错误：{exc}"
        return f"已删除第 {entry_id} 条：{removed[:60]}（{self._usage(entries)}）"

    # ---------- 写路径内部 ----------

    def _load_for_write(self, *, check_stale: bool, author: str, source: str) -> tuple[list[str], str]:
        """在临界区内读现状并做校验；返回 (条目, 错误消息)——错误消息非空时调用方直接返回。

        先在**读之前**记下 `_seen` 基线：读操作本身会刷新它，用它比较就永远相等了。
        """
        baseline = self._seen
        try:
            raw = self._read_raw()
        except _Unreadable as exc:
            return [], f"错误：{exc}，本次未修改"
        if raw is None:
            text = ""
        else:
            try:
                text = raw.decode("utf-8")
            except UnicodeError:
                return [], "错误：常驻记忆不是 UTF-8 文本，本次未修改（请先手工修复）"
        entries = [e.strip() for e in text.split(ENTRY_DELIMITER) if e.strip()]
        # 外部改动（后台整理/工作台/手工编辑）先落历史：即使下面拒绝写入也不丢
        self._snapshot(text, author=author, source=source)
        if check_stale and baseline is not None and _digest(raw or b"") != baseline:
            return [], (
                "错误：常驻记忆在你读取之后被改动过（可能是后台整理或工作台），"
                "按编号修改会改错条目。请先重新 view 查看编号后再操作；本次未修改"
            )
        self._seen = _digest(raw or b"")
        return entries, ""

    def _snapshot(self, text: str, *, author: str, source: str) -> None:
        """把磁盘上的现状记进历史（失败不影响主流程：历史是补充，不是写入前提）。"""
        if not text:
            return
        digest = _digest(text.encode("utf-8"))
        latest = self._history.latest(self._key())
        if latest is not None and latest.sha256 == digest:
            return
        self._history.commit(
            self._key(),
            text,
            author="external",
            source="detected-change",
            summary="写入前检测到的内容（可能是另一个进程或手工编辑）",
        )

    def _check_entries(self, entries: list[str]) -> str:
        if self._char_count(entries) > self._limit:
            return f"共 {self._char_count(entries)} 字符，超过限额 {self._limit}"
        return ""
