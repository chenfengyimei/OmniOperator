"""插件生命周期工具（面向 agent）：安装 → 审阅 → 激活 → 启停 → 卸载。

风险分级即审批边界（与外来技能包同一纪律）：
- 安装/查询/启停是 SAFE——安装只把包放进隔离区，**不执行任何代码**；
- **激活与卸载是 CONFIRM + requires_human**：激活即同意执行插件代码
  （人工确认就是"用户激活"这一步），卸载删除已确认过的包与插件数据；
  无人值守（DenyApprover）永远激活/卸载不了插件。
"""
from __future__ import annotations

from typing import Any

from ..plugins.host import PluginHost
from ..tools.registry import Risk, Tool, ToolRegistry


def register_plugin_tools(registry: ToolRegistry, host: PluginHost) -> None:
    """插件生命周期工具。host 由组装根构造（bootstrap 唯一构造点）。"""

    def plugin_list(_: dict[str, Any]) -> str:
        lines: list[str] = []
        zips = host.inbox_files()
        pending = host.pending()
        installed = host.installed()
        if zips:
            lines.append("inbox 里待安装的插件包（plugin_install(package=文件名)）：")
            lines += [f"- {z['name']}（{z['bytes']} B）" for z in zips]
        if pending:
            lines.append("隔离区待激活的插件（激活前不执行任何代码）：")
            for item in pending:
                files = "、".join(f"{f['path']}({f['bytes']}B)" for f in item["files"]) or "（记录缺失）"
                warn = "　⚠ 导入记录缺失" if item["record_missing"] else ""
                lines.append(
                    f"- {item['id']} v{item['version']}：{item['description']}"
                    f"　来源：{item['source'] or '未知'}{warn}"
                )
                lines.append(f"  文件：{files}")
        if installed:
            lines.append("已安装的插件：")
            for item in installed:
                tools = "、".join(item["tools"]) or "（未注册工具）"
                status = "启用" if item["enabled"] else "停用"
                error = f"　⚠ 装载错误：{item['error']}" if item["error"] else ""
                lines.append(
                    f"- {item['id']} v{item['version']}（{status}）：{item['description']}"
                    f"　工具：{tools}　来源：{item['source'] or '未知'}{error}"
                )
        if not (zips or pending or installed):
            return "没有任何插件：inbox、隔离区与已安装列表都是空的。"
        return "\n".join(lines)

    def plugin_install(args: dict[str, Any]) -> str:
        package = str(args.get("package", "") or "").strip()
        path = str(args.get("path", "") or "").strip()
        if bool(package) == bool(path):
            return "错误：package（inbox 里的 zip 文件名）与 path（工作区插件目录）必须二选一"
        if package:
            ok, msg = host.install_package(package)
        else:
            ok, msg = host.install_directory(path)
        return msg

    def plugin_activate(args: dict[str, Any]) -> str:
        _ok, msg = host.activate(str(args.get("id", "")))
        return msg

    def plugin_enable(args: dict[str, Any]) -> str:
        _ok, msg = host.enable(str(args.get("id", "")))
        return msg

    def plugin_disable(args: dict[str, Any]) -> str:
        _ok, msg = host.disable(str(args.get("id", "")))
        return msg

    def plugin_remove(args: dict[str, Any]) -> str:
        _ok, msg = host.remove(str(args.get("id", "")))
        return msg

    registry.register(
        Tool(
            name="plugin_list",
            description=(
                "查看插件三段状态：inbox 待安装的 zip、隔离区待激活的包（含文件清单）、"
                "已安装插件（启停状态/注册的工具/装载错误）。用户或你自己开发的"
                "新功能都以插件形式接入"
            ),
            parameters={"type": "object", "properties": {}, "required": []},
            func=plugin_list,
            toolset="core",
            risk=Risk.SAFE,
            read_only=True,
        )
    )
    registry.register(
        Tool(
            name="plugin_install",
            description=(
                "安装插件包到隔离区（数据化校验，不执行任何代码）："
                "package=data/plugins/.inbox/ 里的 zip 文件名；"
                "或 path=工作区里自己开发的插件目录。"
                "安装后用 plugin_list 审阅文件清单，由用户确认后 plugin_activate 激活"
            ),
            parameters={
                "type": "object",
                "properties": {
                    "package": {"type": "string", "description": "inbox 里的插件 zip 文件名（与 path 二选一）"},
                    "path": {"type": "string", "description": "工作区里的插件目录（与 package 二选一）"},
                },
            },
            func=plugin_install,
            toolset="core",
            risk=Risk.SAFE,
        )
    )
    registry.register(
        Tool(
            name="plugin_activate",
            description=(
                "激活隔离区里的插件（需用户确认）。**激活即同意执行该插件的代码**"
                "——导入入口模块、注册它声明的工具（工具风险等级由作者声明，"
                "照常走审批链）。激活前请先 plugin_list 把文件清单展示给用户"
            ),
            parameters={
                "type": "object",
                "properties": {
                    "id": {"type": "string", "description": "隔离区里的插件 id（plugin_list 里有）"}
                },
                "required": ["id"],
            },
            func=plugin_activate,
            toolset="core",
            risk=Risk.CONFIRM,
            requires_human=True,
        )
    )
    registry.register(
        Tool(
            name="plugin_enable",
            description="启用一个已停用的插件（指纹复核通过后重新装载注册其工具）",
            parameters={
                "type": "object",
                "properties": {"id": {"type": "string", "description": "插件 id"}},
                "required": ["id"],
            },
            func=plugin_enable,
            toolset="core",
            risk=Risk.SAFE,
        )
    )
    registry.register(
        Tool(
            name="plugin_disable",
            description=(
                "停用一个插件：反注册其工具入口（模块代码仍在进程内存里，"
                "重启后彻底移除——返回消息会如实说明）"
            ),
            parameters={
                "type": "object",
                "properties": {"id": {"type": "string", "description": "插件 id"}},
                "required": ["id"],
            },
            func=plugin_disable,
            toolset="core",
            risk=Risk.SAFE,
        )
    )
    registry.register(
        Tool(
            name="plugin_remove",
            description="卸载插件（需用户确认）：停用并删除包、状态与插件数据；隔离区里的待激活包也可用它丢弃",
            parameters={
                "type": "object",
                "properties": {"id": {"type": "string", "description": "插件 id"}},
                "required": ["id"],
            },
            func=plugin_remove,
            toolset="core",
            risk=Risk.CONFIRM,
            requires_human=True,
        )
    )
