"""技能工具集（toolset=core）：技能的渐进披露与维护。

渐进披露 + 生命周期（Hermes 设计，省 token）：
- skills_list：名字+描述（千级 token 内浏览全部技能）
- skill_view：按需展开单个技能全文
- skill_create：把流程沉淀成新技能（linter 守门在 SkillStore）
- skill_patch：只改一个小节（踩坑后往 Pitfalls 补条目的首选，省 token）
- skill_rewrite：结构级变化时全文重写（重新过 linter）
- skill_history / skill_rollback：看修订历史、回到某一版（回滚产生新修订）

写入类工具都支持 expected_hash：把 skill_view 给的指纹传回来，文件在你读过之后
被别人（用户编辑器、后台整理）改过就会冲突而不是静默覆盖。
"""
from __future__ import annotations

from typing import Any

from ...memory.skill_inbox import SkillInbox
from ...memory.skills import SkillStore
from ..registry import Risk, Tool, ToolRegistry


def _hash_arg(args: dict[str, Any]) -> str | None:
    """expected_hash 允许缺省（缺省时用本进程上次 view 看到的指纹）。"""
    value = args.get("expected_hash")
    text = str(value).strip() if value is not None else ""
    return text or None


_EXPECTED_HASH_PARAM = {
    "type": "string",
    "description": "可选：skill_view 给出的内容指纹（前 16 位即可）。传了就要求文件未被改动，防覆盖别人的编辑",
}


def register_skill_tools(
    registry: ToolRegistry,
    skills: SkillStore,
    *,
    guard: Any = None,
    lifecycle: bool = True,
) -> None:
    """技能工具。`guard` 存在时，创建/改写/回滚共用记忆写入额度（D12）。

    `lifecycle=False` 用于 review 注册表：自动整理可以沉淀/修订技能，
    但不能归档技能（归档需要用户明确确认，属设置中心/主注册表动作）。
    """

    def _gate(content: str) -> str | None:
        if guard is None:
            return None
        decision = guard.check("skill", content)
        return None if decision.allowed else f"未写入技能：{decision.reason}"

    def skills_list(_: dict[str, Any]) -> str:
        items = skills.list()
        if not items:
            return "还没有任何技能。完成一个多步任务后，可以用 skill_create 把流程沉淀成技能。"
        return "\n".join(
            f"- {s['name']}（v{s['version']}）：{s['description']}" for s in items
        )

    def skill_view(args: dict[str, Any]) -> str:
        name = str(args.get("name", "")).strip()
        path = args.get("path")
        try:
            content = skills.view(name, str(path) if path is not None else None)
        except ValueError as exc:
            return f"错误：{exc}"
        if content is None:
            return f"错误：没有技能或参考文件 {name or '（空）'}（先用 skills_list 查看名字）"
        if path is None:
            references = skills.references(name)
            if references:
                content += "\n\n参考文件（按需用 skill_view 的 path 读取，最多列出 60 项）：\n"
                content += "\n".join(f"- {p}" for p in references)
            loaded = skills.read(name)
            if loaded is not None:
                content += (
                    f"\n\n内容指纹：{loaded[1][:16]}"
                    "（改动时传 expected_hash=该值；文件被别人改过会冲突而不是覆盖）"
                )
        return content

    def skill_create(args: dict[str, Any]) -> str:
        blocked = _gate(str(args.get("content", "")))
        if blocked:
            return blocked
        _ok, msg = skills.create(
            str(args.get("name", "")),
            str(args.get("description", "")),
            str(args.get("content", "")),
        )
        return msg

    def skill_patch(args: dict[str, Any]) -> str:
        blocked = _gate(str(args.get("content", "")))
        if blocked:
            return blocked
        _ok, msg = skills.patch(
            str(args.get("name", "")),
            str(args.get("section", "")),
            str(args.get("content", "")),
            expected_hash=_hash_arg(args),
        )
        return msg

    def skill_rewrite(args: dict[str, Any]) -> str:
        blocked = _gate(str(args.get("content", "")))
        if blocked:
            return blocked
        _ok, msg = skills.rewrite(
            str(args.get("name", "")),
            str(args.get("description", "")),
            str(args.get("content", "")),
            expected_hash=_hash_arg(args),
        )
        return msg

    def skill_history(args: dict[str, Any]) -> str:
        name = str(args.get("name", "")).strip()
        if not skills.exists(name):
            return f"错误：技能 {name or '（空）'} 不存在（先用 skills_list 查看）"
        try:
            limit = max(1, min(int(args.get("limit") or 10), 50))
        except (TypeError, ValueError):
            limit = 10
        revisions = skills.history(name, limit=limit)
        if not revisions:
            return f"技能 {name} 还没有修订记录（下一次写入会开始记录）"
        lines = [f"技能 {name} 的修订（新→旧，最多 {limit} 条）："]
        for rev in revisions:
            drift = skills.reference_drift(name, rev.rev)
            suffix = f"　附件差异：{'、'.join(drift)}" if drift else ""
            lines.append(f"- {rev.brief()}{suffix}")
        lines.append("用 skill_rollback(name, rev=N) 回到某一版（会产生新修订，版本号继续加）")
        return "\n".join(lines)

    def skill_rollback(args: dict[str, Any]) -> str:
        try:
            rev = int(args.get("rev"))
        except (TypeError, ValueError):
            return "错误：rev 必须是整数（先 skill_history 查看）"
        _ok, msg = skills.rollback(
            str(args.get("name", "")), rev, expected_hash=_hash_arg(args)
        )
        return msg

    def skill_archive(args: dict[str, Any]) -> str:
        _ok, msg = skills.archive(
            str(args.get("name", "")),
            expected_hash=_hash_arg(args),
            author="agent",
            source="skill_archive",
            reason=str(args.get("reason", "")),
        )
        return msg

    def skill_restore(args: dict[str, Any]) -> str:
        _ok, msg = skills.restore(
            str(args.get("name", "")), author="agent", source="skill_restore"
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
            read_only=True,
        )
    )
    registry.register(
        Tool(
            name="skill_view",
            description="查看技能全文及参考文件列表；提供 path 时只读该 references/ 文本（不执行附件）",
            parameters={
                "type": "object",
                "properties": {
                    "name": {"type": "string", "description": "技能名（skills_list 里有）"},
                    "path": {"type": "string", "description": "可选：如 references/guide.md；省略读 SKILL.md"},
                },
                "required": ["name"],
            },
            func=skill_view,
            toolset="core",
            risk=Risk.SAFE,
            read_only=True,
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
    registry.register(
        Tool(
            name="skill_patch",
            description=(
                "增量修改技能的一个小节（优先用这个而不是全文重写，省 token）。"
                "典型场景：踩坑后往 Pitfalls 补一条、流程变化后更新 Procedure。"
                "section 取 When to Use / Procedure / Pitfalls / Verification 之一"
            ),
            parameters={
                "type": "object",
                "properties": {
                    "name": {"type": "string", "description": "技能名"},
                    "section": {
                        "type": "string",
                        "enum": ["When to Use", "Procedure", "Pitfalls", "Verification"],
                        "description": "要替换的小节",
                    },
                    "content": {"type": "string", "description": "该小节的新内容（Markdown）"},
                    "expected_hash": _EXPECTED_HASH_PARAM,
                },
                "required": ["name", "section", "content"],
            },
            func=skill_patch,
            toolset="core",
            risk=Risk.SAFE,
        )
    )
    registry.register(
        Tool(
            name="skill_rewrite",
            description=(
                "全文重写技能（仅当 patch 装不下结构级变化时用）。"
                "description 与 content 会重新过格式检查；版本号自动 +1"
            ),
            parameters={
                "type": "object",
                "properties": {
                    "name": {"type": "string", "description": "技能名"},
                    "description": {"type": "string", "description": "新的一句话描述（≤60字）"},
                    "content": {"type": "string", "description": "新正文（含四个小节）"},
                    "expected_hash": _EXPECTED_HASH_PARAM,
                },
                "required": ["name", "description", "content"],
            },
            func=skill_rewrite,
            toolset="core",
            risk=Risk.SAFE,
        )
    )

    registry.register(
        Tool(
            name="skill_history",
            description=(
                "查看技能的修订历史（谁在什么时候、以什么来源改的，附件是否跟着变过）。"
                "技能被改坏、或想找回旧版流程时先看这个"
            ),
            parameters={
                "type": "object",
                "properties": {
                    "name": {"type": "string", "description": "技能名"},
                    "limit": {"type": "integer", "description": "最多返回几条（默认 10，上限 50）"},
                },
                "required": ["name"],
            },
            func=skill_history,
            toolset="core",
            risk=Risk.SAFE,
            read_only=True,
        )
    )
    registry.register(
        Tool(
            name="skill_rollback",
            description=(
                "把技能回滚到某个历史修订（skill_history 的 rev）。"
                "回滚会作为**新修订**写入，版本号继续加，旧修订仍可取回"
            ),
            parameters={
                "type": "object",
                "properties": {
                    "name": {"type": "string", "description": "技能名"},
                    "rev": {"type": "integer", "description": "要恢复的修订号"},
                    "expected_hash": _EXPECTED_HASH_PARAM,
                },
                "required": ["name", "rev"],
            },
            func=skill_rollback,
            toolset="core",
            risk=Risk.SAFE,
        )
    )
    if not lifecycle:
        return
    registry.register(
        Tool(
            name="skill_archive",
            description=(
                "把技能归档出技能库（不再出现在 skills_list，内容与历史保留，可恢复）。"
                "仅在用户明确要求停用/清理某个技能时使用；这是需要人工确认的动作"
            ),
            parameters={
                "type": "object",
                "properties": {
                    "name": {"type": "string", "description": "技能名"},
                    "reason": {"type": "string", "description": "可选：为什么归档（会记入归档元数据）"},
                    "expected_hash": _EXPECTED_HASH_PARAM,
                },
                "required": ["name"],
            },
            func=skill_archive,
            toolset="core",
            risk=Risk.CONFIRM,
            requires_human=True,
        )
    )
    registry.register(
        Tool(
            name="skill_restore",
            description=(
                "把已归档的技能恢复回技能库；同名技能已存在时拒绝覆盖。"
                "恢复前可用 skills_list 确认当前技能库"
            ),
            parameters={
                "type": "object",
                "properties": {
                    "name": {"type": "string", "description": "已归档的技能名"},
                },
                "required": ["name"],
            },
            func=skill_restore,
            toolset="core",
            risk=Risk.SAFE,
        )
    )


def register_skill_inbox_tools(registry: ToolRegistry, inbox: SkillInbox) -> None:
    """外来技能包工具（D18 后置项）：导入到隔离区 → 审阅 → 人工确认激活。

    风险分级即审批边界：导入/丢弃只动隔离区（对正式技能库不可见），
    SAFE 级；**激活把内容放进正式技能库，是 CONFIRM + requires_human**——
    审批流里那个人工确认就是"用户激活"这一步，无人值守（DenyApprover）
    永远激活不了外来包。
    """

    def skill_import(args: dict[str, Any]) -> str:
        _ok, msg = inbox.import_package(str(args.get("package", "")))
        return msg

    def skill_pending(_: dict[str, Any]) -> str:
        zips = inbox.inbox_files()
        pending = inbox.pending()
        lines = []
        if zips:
            lines.append("inbox 里待导入的压缩包（用 skill_import(package=文件名) 导入）：")
            lines += [f"- {z['name']}（{z['bytes']} B）" for z in zips]
        if not pending:
            lines.append("隔离区没有待激活的技能。" if not zips else "隔离区当前为空。")
            return "\n".join(lines)
        lines.append("隔离区待激活的技能（skills_list 暂不可见，确认后用 skill_activate 激活）：")
        for item in pending:
            files = "、".join(f"{f['path']}({f['bytes']}B)" for f in item["files"]) or "（清单缺失）"
            warn = "　⚠ 清单缺失：激活前先核对包内容" if item["manifest_missing"] else ""
            lines.append(
                f"- {item['name']}（v{item['version']}）：{item['description']}"
                f"　来源 zip：{item['source_zip'] or '未知'}　导入于 {item['imported_at'] or '未知'}{warn}"
            )
            lines.append(f"  文件清单：{files}")
        return "\n".join(lines)

    def skill_activate(args: dict[str, Any]) -> str:
        _ok, msg = inbox.activate(str(args.get("name", "")))
        return msg

    def skill_discard(args: dict[str, Any]) -> str:
        _ok, msg = inbox.discard(str(args.get("name", "")))
        return msg

    registry.register(
        Tool(
            name="skill_import",
            description=(
                "从 skills/.inbox/ 导入一个外来技能包 zip 到隔离区（数据化解包，"
                "绝不执行包内任何脚本；只接受 SKILL.md 与 references 文本，"
                "路径越界/脚本/二进制整包拒绝）。导入后用 skill_pending 审阅清单，"
                "由用户确认后 skill_activate 激活"
            ),
            parameters={
                "type": "object",
                "properties": {
                    "package": {
                        "type": "string",
                        "description": "skills/.inbox/ 里的 zip 文件名（不带路径）",
                    }
                },
                "required": ["package"],
            },
            func=skill_import,
            toolset="core",
            risk=Risk.SAFE,
        )
    )
    registry.register(
        Tool(
            name="skill_pending",
            description="查看技能包收件箱与隔离区：inbox 里待导入的 zip、隔离区待激活的技能与完整文件清单",
            parameters={"type": "object", "properties": {}, "required": []},
            func=skill_pending,
            toolset="core",
            risk=Risk.SAFE,
            read_only=True,
        )
    )
    registry.register(
        Tool(
            name="skill_activate",
            description=(
                "把隔离区里的外来技能包激活进正式技能库（需用户确认）。"
                "激活前请先 skill_pending 展示文件清单给用户看"
            ),
            parameters={
                "type": "object",
                "properties": {
                    "name": {"type": "string", "description": "隔离区里的技能名（skill_pending 里有）"}
                },
                "required": ["name"],
            },
            func=skill_activate,
            toolset="core",
            risk=Risk.CONFIRM,
            requires_human=True,
        )
    )
    registry.register(
        Tool(
            name="skill_discard",
            description="丢弃隔离区里的一个待激活技能包（只删隔离区，不碰正式技能库）",
            parameters={
                "type": "object",
                "properties": {
                    "name": {"type": "string", "description": "隔离区里的技能名（skill_pending 里有）"}
                },
                "required": ["name"],
            },
            func=skill_discard,
            toolset="core",
            risk=Risk.SAFE,
        )
    )
