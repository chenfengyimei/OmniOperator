"""记忆写入的确定性闸门（D12 / A15）。

为什么要有这一层：记忆写入有五个入口——用户对话里的 `remember`、review 自动写、
工作台导入、技能创建/改写、回滚。每个入口各自校验长度和额度，就一定会有一个
漏掉（A15 就是 `profile_edit` 没重算总额度）。所以校验收口到 `MemoryGuard`：

1. **输入检查**：空内容、NUL、超长（事实/档案/技能各自的长度上限）；
2. **隐私检查**：凭据形态的确定性正则（私钥/API key/Cookie/密码赋值…）——
   提示词里的"不要记密钥"只是概率约束，长期记忆会进每轮提示词，这层必须是硬约束；
3. **遗忘检查**：用户说过"不要记"的内容，任何入口都不能再写回来；
4. **共用额度**：事实 + 档案 + 技能共用一个当日额度（`state.db` 的条件更新），
   review 和用户手动操作抢的是同一份；
5. **fail-closed**：额度状态不可用（损坏/只读/库忙）时拒绝写入，绝不把
   "读不出来"当成"还没用过"。

注意边界：这一层保证"写多少、能不能写、能不能写凭据"，**不判断内容对不对**，
也不改任何权限。正则不是提示词注入防御——权限由工具风险等级与审批器用代码约束。
"""
from __future__ import annotations

import datetime as dt
from dataclasses import dataclass

from .privacy import rejection_reason as privacy_rejection_reason
from .state import StateBusy, StateStore, StateUnavailable
from .store import PROFILE_MAX_CHARS, MemoryStore

MEMORY_WRITE_SCOPE = "memory_write"

KIND_FACT = "fact"
KIND_PROFILE = "profile"
KIND_SKILL = "skill"


@dataclass(frozen=True)
class WriteDecision:
    allowed: bool
    reason: str = ""
    used: int = 0
    limit: int = 0

    def brief(self) -> dict[str, object]:
        return {"allowed": self.allowed, "reason": self.reason, "used": self.used, "limit": self.limit}


class MemoryGuard:
    """记忆写入的唯一闸门。`state=None` 表示不限额（嵌入式用法/单测）。"""

    def __init__(
        self,
        state: StateStore | None = None,
        *,
        max_writes_per_day: int = 200,
        max_fact_chars: int = 500,
        max_profile_chars: int = PROFILE_MAX_CHARS,
        max_skill_chars: int = 8000,
        source: str = "chat",
    ) -> None:
        self._state = state
        self._max = max(0, int(max_writes_per_day))
        self._limits = {
            KIND_FACT: max(1, int(max_fact_chars)),
            KIND_PROFILE: max(1, int(max_profile_chars)),
            KIND_SKILL: max(1, int(max_skill_chars)),
        }
        self.source = source

    def check(
        self, kind: str, text: str, *, store: MemoryStore | None = None
    ) -> WriteDecision:
        """按 输入 → 隐私 → 遗忘 → 额度 的顺序判定；被拒的写入**不消耗额度**。"""
        body = str(text or "").strip()
        if not body:
            return WriteDecision(False, "内容为空：没有写入任何东西")
        if "\x00" in body:
            return WriteDecision(False, "内容含 NUL 字符：拒绝写入")
        limit_chars = self._limits.get(kind, self._limits[KIND_FACT])
        if len(body) > limit_chars:
            return WriteDecision(
                False,
                f"内容太长（{len(body)} 字符，上限 {limit_chars}）："
                "请压缩成一句话再写，不要把整段对话塞进记忆",
            )
        # 隐私闸门：凭据形态的确定性正则（提示词里的"不要记密钥"只是概率约束，
        # 这层才是硬约束）。拦在额度之前——被拒的写入不消耗当日额度。
        reason = privacy_rejection_reason(body)
        if reason:
            return WriteDecision(False, reason)
        if kind == KIND_FACT and store is not None and store.is_forgotten(body):
            return WriteDecision(
                False,
                "这条内容与用户要求遗忘的记录一致：不会再写入（如需恢复请让用户明确说'记住它'）",
            )
        if self._state is None:
            return WriteDecision(True, "未配置写入额度（未持久化的嵌入式用法）")
        degraded = self._state.degraded_reason("budget")
        if degraded:
            return WriteDecision(
                False,
                f"记忆写入已停止：{degraded}。为避免把损坏的额度当成 0 已用，不做自动重置",
            )
        today = dt.date.today().isoformat()
        try:
            hit = self._state.consume_budget(MEMORY_WRITE_SCOPE, today, self._max)
        except StateBusy as exc:
            return WriteDecision(False, f"记忆写入暂时不可用（{exc}）；本次没有消耗额度")
        except StateUnavailable as exc:
            return WriteDecision(False, f"记忆写入已停止：{exc}")
        if hit.ok:
            return WriteDecision(True, f"记忆写入 {hit.used}/{hit.limit}（今日）", hit.used, hit.limit)
        if hit.limit <= 0:
            return WriteDecision(
                False,
                "今日记忆写入额度为 0（未授权任何写入）；可在 config.yaml 的 memory.max_writes_per_day 调整",
                hit.used,
                hit.limit,
            )
        return WriteDecision(
            False,
            f"今日记忆写入额度已用完（{hit.used}/{hit.limit}）；明天自动恢复，"
            "或在 config.yaml 调整 memory.max_writes_per_day",
            hit.used,
            hit.limit,
        )

    def usage_today(self) -> tuple[int, int]:
        """(已用, 上限)；状态不可用时按"用满"上报，不显示成 0。"""
        if self._state is None:
            return 0, 0
        if self._state.degraded_reason("budget"):
            return self._max, self._max
        try:
            return (
                self._state.budget_used(MEMORY_WRITE_SCOPE, dt.date.today().isoformat()),
                self._max,
            )
        except StateUnavailable:
            return self._max, self._max
