"""个人能力：resident；工具仅使用注入依赖，不构造运行时。"""
from __future__ import annotations

from typing import Any

from ..registry import Risk, Tool, ToolRegistry


def register_resident_tools(registry: ToolRegistry, resident: Any) -> None:
    """resident_memory（SAFE）：常驻小记忆（Hermes memory 工具的精简版）。

    数据层统一后 `resident` 是 OwnerMemory 门面（常驻条目=owner 人物的
    resident 条目），view/add/replace/remove 接口与旧 ResidentMemory 同形。
    与 remember（事实检索层）的分工：这条写的是**每轮都进系统提示词**的
    常驻笔记（进行中的约定/教训/下一步），宁少勿多；写超限会被拒绝并
    要求先合并（限额逼自整理，Hermes 同款协议）。
    """

    def resident_memory(args: dict[str, Any]) -> str:
        op = str(args.get("operation", "view"))
        if op == "view":
            return resident.view()
        # evidence = 原文摘录：后台整理（回合后复盘/夜间日记）写入时必填，
        # 由存储层逐字比对来源材料——没有出处的内容不写入长期记忆。
        evidence = str(args.get("evidence", "") or "").strip()
        if op == "add":
            return resident.add(str(args.get("content", "")), evidence=evidence)
        if op == "replace":
            try:
                entry_id = int(args.get("id", 0))
            except (TypeError, ValueError):
                return "错误：id 必须是整数（view 里的编号）"
            return resident.replace(
                entry_id, str(args.get("content", "")), evidence=evidence
            )
        if op == "remove":
            try:
                entry_id = int(args.get("id", 0))
            except (TypeError, ValueError):
                return "错误：id 必须是整数（view 里的编号）"
            return resident.remove(entry_id)
        return f"错误：未知操作 {op}（view/add/replace/remove）"

    registry.register(
        Tool(
            name="resident_memory",
            description=(
                "维护常驻工作笔记（每轮对话都会注入你的系统提示词，跨会话生效）。"
                "operation: view 查看 / add 新增 / replace 改写第 id 条 / remove 删除第 id 条。"
                "只放'当下持续有用'的内容（进行中的约定、重要教训、下一步）；"
                "写满会被拒绝，需先合并旧条目。稳定的人物特征请用 profile_manage。"
                "后台整理（回合后复盘/夜间日记）写入时必须带 evidence：从原文逐字照抄一段作为依据，"
                "会被逐字比对，照抄不过就写不进去"
            ),
            parameters={
                "type": "object",
                "properties": {
                    "operation": {
                        "type": "string",
                        "enum": ["view", "add", "replace", "remove"],
                    },
                    "content": {"type": "string", "description": "条目内容（add/replace 用）"},
                    "id": {"type": "integer", "description": "条目编号（replace/remove 用，view 里有）"},
                    "evidence": {
                        "type": "string",
                        "description": (
                            "原文摘录（add/replace 用）：从你正在整理的原文里逐字照抄一段，"
                            "不改写、不拼接、不概括。后台整理写入时必填，否则拒绝写入"
                        ),
                    },
                },
                "required": ["operation"],
            },
            func=resident_memory,
            toolset="core",
            risk=Risk.SAFE,
        )
    )
