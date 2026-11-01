"""所有者记忆门面：档案行与常驻笔记统一进人物系统后的注入与维护面。

数据层统一（本批）后，"关于用户"的常驻注入记忆不再分两套存储：
- 旧 `profile` 表（memory.db）→ owner 人物的 archive 条目；
- 旧 `data/MEMORY.md` 常驻文件 → owner 人物的 resident 条目；
- 注入块字符串、限额协议（总量制+逼合并报错）、工具/端点响应形状全部保持
  与旧实现逐字节兼容，调用方（agent 注入、profile_manage / resident_memory
  工具、web 端点）只换数据源不换契约。

门面刻意实现与 ResidentMemory / MemoryStore.profile_* 相同的方法面：
Runtime.resident 字段、工具注册、web 端点都是既有的注入点，直接换实例即可。
作者参数（author）在条目模型里没有对应列，接受但忽略；来源（source）落库。
"""
from __future__ import annotations

from .anchor import describe_source
from .people import PersonError, PersonStore
from .resident import BLOCK_HEADER, ENTRY_DELIMITER, injection_risk

try:  # 旧档案总量常量（2000）以 store 为准；store 不依赖本模块，无循环 import
    from .store import PROFILE_MAX_CHARS as _PROFILE_TOTAL
except ImportError:  # pragma: no cover - 仅在重构期防御
    _PROFILE_TOTAL = 2000


class OwnerMemory:
    """owner 人物的记忆门面：resident 面（原 ResidentMemory）+ 档案面（原 profile_*）。"""

    def __init__(
        self,
        people: PersonStore,
        *,
        owner_name: str = "所有者",
        archive_total: int | None = None,
        resident_total: int | None = None,
    ) -> None:
        self._people = people
        self._owner_name = owner_name
        # 总量上限以 PersonStore 的所有者配额为唯一真源（bootstrap 从 config 注入）；
        # 这里仅用于展示，缺省兜底与旧协议同值
        self._archive_total = int(
            archive_total if archive_total is not None
            else (people.owner_total_limit("archive") or _PROFILE_TOTAL)
        )
        self._resident_total = int(
            resident_total if resident_total is not None
            else (people.owner_total_limit("resident") or 2200)
        )
        self._cached_owner: int | None = None

    # ---------------------------------------------------------------- 所有者解析

    def owner_id(self) -> int:
        """owner 人物 id（幂等创建；正常路径迁移后已存在，这里只是查询）。"""
        if self._cached_owner is not None:
            return self._cached_owner
        self._cached_owner = self._people.ensure_owner(self._owner_name)
        return self._cached_owner

    def person(self) -> dict:
        return self._people.get_person(self.owner_id()) or {}

    # ---------------------------------------------------------------- 查询

    def _entries(self, kind: str) -> list[tuple[int, str]]:
        return [
            (int(e["id"]), str(e["content"]))
            for e in self._people.entries(self.owner_id())
            if e["entry_kind"] == kind
        ]

    def _details(self, kind: str) -> list[dict]:
        """带出处的条目明细（核对面用）：来源指针 + 原文摘录。"""
        return [
            e for e in self._people.entries(self.owner_id())
            if e["entry_kind"] == kind
        ]

    def _evidence_section(self, details: list[dict]) -> list[str]:
        """出处小节：条目编号 → 来源 + 原文摘录。

        只列有出处的条目——没出处的写"（无出处）"是噪声，而"有出处但摘录为空"
        在锚定上下文里根本写不进来。
        """
        lines = []
        for e in details:
            if not e.get("source_ref"):
                continue
            ref = describe_source(e.get("source", ""), e.get("source_ref", ""))
            quote = str(e.get("evidence") or "").strip()
            quote = (quote[:60] + "…") if len(quote) > 60 else quote
            lines.append(f"[{e['id']}] {ref} · 「{quote}」" if quote else f"[{e['id']}] {ref}")
        return lines

    def evidence_lines(self, kind: str) -> list[str]:
        """出处小节（对外）：kind 取 archive（档案）/ resident（常驻）。"""
        return self._evidence_section(self._details(kind))

    # ---------------------------------------------------------------- resident 面

    @property
    def limit(self) -> int:
        return self._resident_total

    def _resident_usage(self, entries: list[tuple[int, str]]) -> str:
        used = len(ENTRY_DELIMITER.join(text for _, text in entries))
        pct = min(100, int(used / self._resident_total * 100)) if self._resident_total else 0
        return f"{pct}% — {used}/{self._resident_total} 字符"

    def usage(self) -> str:
        return self._resident_usage(self._entries("resident"))

    def resident_usage_chars(self) -> int:
        """常驻容器已用字符数（与注入块的口径一致：含条目分隔符）。

        为什么常驻与档案的用量口径不同：常驻是**多行块**注入提示词的，
        分隔符也占位置；档案是**逐条**列表注入的，不含分隔符。
        两个口径各有各的注入形态，硬凑成一个数反而两边都不准。
        """
        entries = self._entries("resident")
        return len(ENTRY_DELIMITER.join(text for _, text in entries))

    def entries(self) -> list[tuple[int, str]]:
        """工作台展示用：带编号（稳定行号，删除后不重排）。"""
        return self._entries("resident")

    def block(self) -> str:
        """系统提示词注入块：空则空串，否则 头+用量+全部条目（§ 分隔）。"""
        try:
            entries = self._entries("resident")
        except Exception as exc:  # noqa: BLE001 —— 注入块读不出来要如实说，不能让记忆"凭空消失"
            return f"MEMORY（常驻工作笔记读取失败：{exc}）"
        if not entries:
            return ""
        return (
            f"{BLOCK_HEADER}（{self._resident_usage(entries)}）\n"
            + ENTRY_DELIMITER.join(text for _, text in entries)
        )

    def view(self) -> str:
        """agent 视角的清单：带编号（replace/remove 用编号寻址）+ 出处小节。"""
        entries = self._entries("resident")
        if not entries:
            return (
                f"常驻记忆为空（限额 {self._resident_total} 字符）。"
                "用 add 记录跨会话仍需要的事：进行中的约定、教训、下一步。"
            )
        lines = [f"常驻记忆（{self._resident_usage(entries)}）："]
        lines += [f"[{i}] {e}" for i, e in entries]
        evidence = self._evidence_section(self._details("resident"))
        if evidence:
            lines.append("")
            lines.append("出处（每条的来源与原文依据，供核对）：")
            lines += evidence
        capacity = self.capacity_notice("resident")
        if capacity:
            lines.append("")
            lines.append(capacity)
        return "\n".join(lines)

    def capacity_notice(self, container: str = "resident") -> str:
        """容量档位提示：到了 80% 就提醒合并，返回空串表示一切正常。

        抄 surfcore 记忆卡的 80/90/100 三档判定（`memory/capacity.py`）。
        放在**查看**而不是**写入被拒**时：写不进去只说明这次不行，
        而八成就该动手整理——等满了再处理，碎片已经堆了一堆。
        """
        from .capacity import CapacityUsage, TIER_OK, tier_label

        try:
            if container == "archive":
                used = self.profile_usage_chars()
                total = self.archive_total_limit()
                name = "用户档案"
            else:
                used = self.resident_usage_chars()
                total = self.resident_total_limit()
                name = "常驻记忆"
        except Exception:  # noqa: BLE001 —— 容量读不出来不该让查看整个失败
            return ""
        if total <= 0:
            return ""
        usage = CapacityUsage(name, used, total)
        if usage.tier == TIER_OK:
            return ""
        return (
            f"⚠ 容量：{tier_label(usage.tier)}（{used}/{total} 字符）。"
            "写新条目前先合并或删减现有条目，别等写不进去再处理。"
        )

    def _write(
        self, op: str, content: str, entry_id: int | None, *,
        author: str, source: str, evidence: str = "",
    ) -> str:
        """统一写路径：PersonError 转旧协议的"错误：…"返回串（工具/端点直接回显）。"""
        try:
            if op == "add":
                self._people.add_entry(
                    self.owner_id(), "resident", content,
                    source=source or "resident_memory", evidence=evidence,
                )
                return f"已写入常驻记忆（{self.usage()}）"
            if op == "replace":
                if not self._people.edit_entry(
                    int(entry_id or 0), content, evidence=evidence or None
                ):
                    return f"错误：不存在第 {entry_id} 条（先 view 查看编号）"
                return f"已更新第 {entry_id} 条（{self.usage()}）"
            if op == "remove":
                entries_before = dict(self._entries("resident"))
                removed = entries_before.get(int(entry_id or 0))
                if not self._people.remove_entry(int(entry_id or 0)):
                    return f"错误：不存在第 {entry_id} 条（先 view 查看编号）"
                shown = (removed or "")[:60]
                return f"已删除第 {entry_id} 条：{shown}（{self.usage()}）"
        except PersonError as exc:
            message = str(exc)
            return message if message.startswith("错误") else f"错误：{message}"
        raise ValueError(f"未知常驻写操作 {op}")

    def add(
        self, content: str, *, author: str = "agent",
        source: str = "resident_memory", evidence: str = "",
    ) -> str:
        content = (content or "").strip()
        if not content:
            return "错误：content 不能为空"
        if risk := injection_risk(content):
            return f"错误：{risk}"
        return self._write("add", content, None, author=author, source=source, evidence=evidence)

    def replace(
        self, entry_id: int, content: str, *, author: str = "agent",
        source: str = "resident_memory", evidence: str = "",
    ) -> str:
        content = (content or "").strip()
        if not content:
            return "错误：content 不能为空（要删条目用 remove）"
        if risk := injection_risk(content):
            return f"错误：{risk}"
        return self._write(
            "replace", content, entry_id, author=author, source=source, evidence=evidence
        )

    def remove(self, entry_id: int, *, author: str = "agent", source: str = "resident_memory") -> str:
        return self._write("remove", "", entry_id, author=author, source=source)

    # ---------------------------------------------------------------- 档案面

    def profile_lines(self) -> list[tuple[int, str]]:
        return self._entries("archive")

    def profile_usage_chars(self) -> int:
        return sum(len(text) for _, text in self.profile_lines())

    def archive_total_limit(self) -> int:
        """档案容器总字符上限（<=0 表示不限）。

        公开成方法而不是让调用方摸 `_archive_total`：
        容量档位判定（`memory/capacity.py`）需要「已用 / 上限」两个数，
        而私有属性从别的模块读是坏习惯。
        """
        return int(self._archive_total)

    def resident_total_limit(self) -> int:
        """常驻容器总字符上限（<=0 表示不限）。"""
        return int(self._resident_total)

    def profile_add(
        self, content: str, *, source: str = "manual", evidence: str = ""
    ) -> tuple[bool, str]:
        """超限时返回 (False, 错误+当前档案+剩余额度)，逼调用方当轮合并。"""
        content = (content or "").strip()
        if not content:
            return False, "错误：档案内容不能为空"
        try:
            self._people.add_entry(
                self.owner_id(), "archive", content,
                source=source or "manual", evidence=evidence,
            )
        except PersonError as exc:
            return False, str(exc)
        usage = self.profile_usage_chars()
        return True, f"已加入档案：{content}（当前 {usage}/{self._archive_total} 字符）"

    def profile_edit_checked(
        self, line_id: int, content: str, *, evidence: str | None = None
    ) -> tuple[bool, str]:
        """改一行档案。总量额度在存储层重算（对应旧 A15 修复的口径）。"""
        text = (content or "").strip()
        if not text:
            return False, "错误：档案内容不能为空"
        try:
            if not self._people.edit_entry(int(line_id), text, evidence=evidence):
                return False, f"错误：不存在第 {line_id} 条（先 view 查看编号）"
        except PersonError as exc:
            return False, str(exc)
        return True, f"已更新（{self.profile_usage_chars()}/{self._archive_total} 字符）。"

    def profile_edit(self, line_id: int, content: str) -> bool:
        return self.profile_edit_checked(line_id, content)[0]

    def profile_delete(self, line_id: int) -> bool:
        return self._people.remove_entry(int(line_id))

    def profile_block(self) -> str:
        """系统提示词注入用的人物档案快照块；空档案返回空串。形状与旧实现一致。"""
        lines = self.profile_lines()
        if not lines:
            return ""
        usage = self.profile_usage_chars()
        body = "\n".join(f"- {content}" for _, content in lines)
        return (
            f"## 关于用户（长期档案，{usage}/{self._archive_total} 字符，"
            f"用 profile_manage 工具维护，写满需先合并）\n{body}"
        )
