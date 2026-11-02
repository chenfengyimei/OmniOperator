"""个人能力：profile；工具仅使用注入依赖，不构造运行时。"""

from __future__ import annotations

from typing import Any

from ...memory.capacity import TIER_OK, CapacityUsage, tier_label
from ...memory.store import MemoryStore
from ..registry import Risk, Tool, ToolRegistry


def register_profile_tools(
    registry: ToolRegistry,
    owner,  # OwnerMemory 门面：档案=owner 人物的 archive 条目（profile_lines/... 同形）
    *,
    store: MemoryStore | None = None,  # 仅供 guard 的遗忘墓碑检查（profile 类不依赖，保持注入形状)
    guard: Any = None,
    source: str = "chat",
) -> None:
    def _gate(kind: str, text: str) -> str | None:
        if guard is None:
            return None
        decision = guard.check(kind, text, store=store)
        return None if decision.allowed else f"未写入档案：{decision.reason}"

    def _capacity_line() -> str:
        """档案容量的档位提示：到了 80% 就告诉模型该合并了。

        为什么放在 view 里而不是写入被拒时：写入被拒只说明"这次写不进去"，
        而**八成就该动手合并**——等写不进去再处理，用户已经积累了一堆碎片。
        抄 surfcore 记忆卡的 80/90/100 三档判定（`memory/capacity.py`）。
        """
        try:
            used = int(owner.profile_usage_chars())
            total = int(owner.archive_total_limit() or 0)
        except Exception:  # noqa: BLE001 —— 容量读不出来不该让档案查看整个失败
            return ""
        if total <= 0:
            return ""
        usage = CapacityUsage("用户档案", used, total)
        if usage.tier == TIER_OK:
            return ""
        return (
            f"⚠ 档案容量：{tier_label(usage.tier)}"
            f"（{used}/{total} 字符）。"
            "写新条目前先合并或删减现有条目，别等写不进去再处理。"
        )

    def profile_manage(args: dict[str, Any]) -> str:
        op = str(args.get("operation", "view"))
        if op == "view":
            lines = owner.profile_lines()
            usage = owner.profile_usage_chars()
            if not lines:
                return f"用户档案为空（{usage} 字符额度）。用 add 添加第一条。"
            body = "\n".join(f"[{lid}] {text}" for lid, text in lines)
            out = f"用户档案（{usage} 字符已用）：\n{body}"
            evidence = owner.evidence_lines("archive")
            if evidence:
                out += "\n\n出处（每条的来源与原文依据，供核对）：\n" + "\n".join(evidence)
            capacity = _capacity_line()
            if capacity:
                out += "\n\n" + capacity
            return out
        # evidence = 原文摘录：后台整理（回合后复盘/夜间日记）写入时必填，
        # 由存储层逐字比对来源材料——没有出处的内容不写入长期记忆。
        evidence = str(args.get("evidence", "") or "").strip()
        if op == "add":
            content = str(args.get("content", "")).strip()
            blocked = _gate("profile", content)
            if blocked:
                return blocked
            return owner.profile_add(content, source=source or "manual", evidence=evidence)[1]
        if op == "edit":
            try:
                line_id = int(args.get("line_id", 0))
            except (TypeError, ValueError):
                return "错误：line_id 必须是整数"
            content = str(args.get("content", "")).strip()
            if not content:
                return "错误：edit 需要非空 content"
            blocked = _gate("profile", content)
            if blocked:
                return blocked
            _ok, message = owner.profile_edit_checked(line_id, content, evidence=evidence or None)
            return message
        if op == "delete":
            try:
                line_id = int(args.get("line_id", 0))
            except (TypeError, ValueError):
                return "错误：line_id 必须是整数"
            ok = owner.profile_delete(line_id)
            return "已删除。" if ok else f"错误：不存在第 {line_id} 条（先 view 查看编号）"
        return f"错误：未知操作 {op}（支持 view/add/edit/delete）"

    registry.register(
        Tool(
            name="profile_manage",
            description=(
                "维护用户长期档案（职位、稳定偏好、性格等核心特征；有字符额度限制）。"
                "operation: view 查看 / add 添加 / edit 修改 / delete 删除；"
                "写满时 add 会返回当前档案与剩余额度，此时应先合并再写。"
                "后台整理（回合后复盘/夜间日记）写入时必须带 evidence：从原文逐字照抄一段作为依据，"
                "会被逐字比对，照抄不过就写不进去"
            ),
            parameters={
                "type": "object",
                "properties": {
                    "operation": {
                        "type": "string",
                        "enum": ["view", "add", "edit", "delete"],
                        "description": "操作类型",
                    },
                    "content": {"type": "string", "description": "档案行内容（add/edit 用）"},
                    "line_id": {
                        "type": "integer",
                        "description": "行编号（edit/delete 用，view 里有编号）",
                    },
                    "evidence": {
                        "type": "string",
                        "description": (
                            "原文摘录（add/edit 用）：从你正在整理的原文里逐字照抄一段，"
                            "不改写、不拼接、不概括。后台整理写入时必填，否则拒绝写入"
                        ),
                    },
                },
                "required": ["operation"],
            },
            func=profile_manage,
            toolset="core",
            risk=Risk.SAFE,
        )
    )
