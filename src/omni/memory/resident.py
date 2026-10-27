"""常驻小记忆（Hermes MEMORY.md 式，NOTES §1.3a）：跨会话冻结快照注入系统提示词。

与档案（USER.md 等价物）的分工：档案=用户是谁（稳定特征），常驻记忆=
agent 的工作笔记（进行中的事、约定、教训）。

限额协议（抄 Hermes memory_tool_store）：
- 条目以 "§" 独行分隔，整文件限额（默认 2200 字符）；
- 写超限 → 报错并回传当前全部条目+超出量，逼调用方**当轮**先合并再重试
  （"限额 + 结构化报错 = 自整理"，比无限追加更会长出纪律）；
- 系统提示词里以「冻结快照 + 用量头」注入；本轮对话中途写入只落盘、
  不改本轮 prompt（下一轮生效），保住单轮内系统提示词字节稳定。
"""
from __future__ import annotations

import threading

from ..config import DATA_DIR

ENTRY_DELIMITER = "\n§\n"
BLOCK_HEADER = "MEMORY（你的常驻工作笔记：跨会话生效；写满会被拒绝并要求先合并）"


class ResidentMemory:
    """文件存储（data/MEMORY.md），原子写 + 线程锁（主对话与 review 并发写）。"""

    def __init__(self, path=None, char_limit: int = 2200) -> None:
        self._path = path or (DATA_DIR / "MEMORY.md")
        self._limit = char_limit
        self._lock = threading.Lock()

    # ---------- 基础 ----------

    def _read_entries(self) -> list[str]:
        try:
            raw = self._path.read_text(encoding="utf-8")
        except OSError:
            return []
        return [e.strip() for e in raw.split(ENTRY_DELIMITER) if e.strip()]

    def _write_entries(self, entries: list[str]) -> None:
        self._path.parent.mkdir(parents=True, exist_ok=True)
        tmp = self._path.with_suffix(".tmp")
        tmp.write_text(ENTRY_DELIMITER.join(entries), encoding="utf-8")
        tmp.replace(self._path)

    def _char_count(self, entries: list[str]) -> int:
        return len(ENTRY_DELIMITER.join(entries))

    def _usage(self, entries: list[str]) -> str:
        used = self._char_count(entries)
        pct = min(100, int(used / self._limit * 100)) if self._limit else 0
        return f"{pct}% — {used}/{self._limit} 字符"

    # ---------- 注入（冻结快照） ----------

    def block(self) -> str:
        """系统提示词用：空则空串，否则 头+用量+全部条目（§ 分隔）。"""
        entries = self._read_entries()
        if not entries:
            return ""
        return f"{BLOCK_HEADER}（{self._usage(entries)}）\n" + ENTRY_DELIMITER.join(
            entries
        )

    # ---------- 工具面 ----------

    def view(self) -> str:
        """agent 视角的清单：带编号（replace/remove 用编号寻址）。"""
        entries = self._read_entries()
        if not entries:
            return (
                f"常驻记忆为空（限额 {self._limit} 字符）。"
                "用 add 记录跨会话仍需要的事：进行中的约定、教训、下一步。"
            )
        lines = [f"常驻记忆（{self._usage(entries)}）："]
        lines += [f"[{i}] {e}" for i, e in enumerate(entries, 1)]
        return "\n".join(lines)

    def add(self, content: str) -> str:
        content = (content or "").strip()
        if not content:
            return "错误：content 不能为空"
        with self._lock:
            entries = self._read_entries()
            merged = ENTRY_DELIMITER.join(entries + [content])
            if len(merged) > self._limit:
                over = len(merged) - self._limit
                return (
                    f"错误：常驻记忆已 {self._usage(entries)}；新增这条（{len(content)} 字符）"
                    f"将超出限额 {over} 字符。请先用 replace 合并/精简下面这些旧条目腾出空间，"
                    f"再重试 add——限额按最终结果检查：\n"
                    + "\n§\n".join(f"[{i}] {e}" for i, e in enumerate(entries, 1))
                )
            self._write_entries(entries + [content])
        return f"已写入常驻记忆（{self._usage(self._read_entries())}）"

    def replace(self, entry_id: int, content: str) -> str:
        content = (content or "").strip()
        if not content:
            return "错误：content 不能为空（要删条目用 remove）"
        with self._lock:
            entries = self._read_entries()
            if not (1 <= entry_id <= len(entries)):
                return f"错误：不存在第 {entry_id} 条（先 view 查看编号）"
            entries[entry_id - 1] = content
            if len(ENTRY_DELIMITER.join(entries)) > self._limit:
                return (
                    f"错误：替换后 {len(ENTRY_DELIMITER.join(entries))}/{self._limit} 字符"
                    "超限，未写入。请给出更精简的内容"
                )
            self._write_entries(entries)
        return f"已更新第 {entry_id} 条（{self._usage(entries)}）"

    def remove(self, entry_id: int) -> str:
        with self._lock:
            entries = self._read_entries()
            if not (1 <= entry_id <= len(entries)):
                return f"错误：不存在第 {entry_id} 条（先 view 查看编号）"
            removed = entries.pop(entry_id - 1)
            self._write_entries(entries)
        return f"已删除第 {entry_id} 条：{removed[:60]}（{self._usage(entries)}）"
