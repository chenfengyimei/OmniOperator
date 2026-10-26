"""技能工具集（toolset=core）：技能的渐进披露与创建。

三件套对应 Hermes 的渐进披露设计（省 token）：
- skills_list：名字+描述（千级 token 内浏览全部技能）
- skill_view：按需展开单个技能全文
- skill_create：把流程沉淀成新技能（linter 守门在 SkillStore）
"""
from __future__ import annotations

from typing import Any

from ..memory.skills import SkillStore
from .registry import Risk, Tool, ToolRegistry


def register_skill_tools(registry: ToolRegistry, skills: SkillStore) -> None:
    def skills_list(_: dict[str, Any]) -> str:
        items = skills.list()
        if not items:
            return "还没有任何技能。完成一个多步任务后，可以用 skill_create 把流程沉淀成技能。"
        return "\n".join(
            f"- {s['name']}（v{s['version']}）：{s['description']}" for s in items
        )

    def skill_view(args: dict[str, Any]) -> str:
        name = str(args.get("name", "")).strip()
        content = skills.view(name)
        if content is None:
            return f"错误：没有技能 {name or '（空）'}（先用 skills_list 查看名字）"
        return content

    def skill_create(args: dict[str, Any]) -> str:
        ok, msg = skills.create(
            str(args.get("name", "")),
            str(args.get("description", "")),
            str(args.get("content", "")),
        )
        return msg

    registry.register(
        Tool(
            name="skills_list",
            description="列出所有可用技能（名字+一句话描述）",
            parameters={"type": "object", "properties": {}, "required": []},
            func=skills_list,
            toolset="core",
            risk=Risk.SAFE,
        )
    )
    registry.register(
        Tool(
            name="skill_view",
            description="查看某个技能的完整内容（When to Use / Procedure / Pitfalls / Verification）",
            parameters={
                "type": "object",
                "properties": {"name": {"type": "string", "description": "技能名（skills_list 里有）"}},
                "required": ["name"],
            },
            func=skill_view,
            toolset="core",
            risk=Risk.SAFE,
        )
    )
    registry.register(
        Tool(
            name="skill_create",
            description=(
                "创建技能：把'怎么做某类事'的可复用流程沉淀下来。"
                "适用时机：多步任务成功后、踩坑找到可行路径后、用户纠正方法后。"
                "content 按 When to Use（何时用）/ Procedure（步骤）/ Pitfalls（坑）/"
                " Verification（怎么验证做对了）四节组织；名字用英文小写连字符"
            ),
            parameters={
                "type": "object",
                "properties": {
                    "name": {"type": "string", "description": "技能名，如 bilibili-watch"},
                    "description": {"type": "string", "description": "一句话描述（≤60字）"},
                    "content": {"type": "string", "description": "技能正文（Markdown，含四个小节）"},
                },
                "required": ["name", "description", "content"],
            },
            func=skill_create,
            toolset="core",
            risk=Risk.SAFE,
        )
    )
