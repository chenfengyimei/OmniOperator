"""社区工具适配；HTTP/发布逻辑由注入客户端承担。"""
from __future__ import annotations

from typing import Any

from ...tools.registry import Risk, Tool, ToolRegistry
from .client import CommunityClient


def register_community_tools(registry: ToolRegistry, client: CommunityClient) -> None:
    """社区工具。搜索/详情/安装 SAFE（安装只落收件箱+隔离区）；发布 CONFIRM+人工确认。"""

    def community_config(args: dict[str, Any]) -> str:
        has_url = "server_url" in args
        has_token = "token" in args
        if not has_url and not has_token:
            current = client.config()
            return (
                f"当前社区配置：server={current['server_url'] or '（未设置）'}，"
                f"token {'已配置' if current['token'] else '未配置'}。"
                "用 community_config(server_url=…, token=…) 设置"
            )
        _ok, msg = client.configure(
            server_url=str(args.get("server_url", "")) if has_url else None,
            token=str(args.get("token", "")) if has_token else None,
        )
        return msg

    def community_search(args: dict[str, Any]) -> str:
        _ok, msg = client.search(
            str(args.get("query", "")), max(1, min(int(args.get("limit") or 10), 50))
        )
        return msg

    def community_info(args: dict[str, Any]) -> str:
        _ok, msg = client.info(str(args.get("id", "")))
        return msg

    def community_install(args: dict[str, Any]) -> str:
        _ok, msg = client.install(
            str(args.get("id", "")), str(args.get("version", "") or "")
        )
        return msg

    def community_publish(args: dict[str, Any]) -> str:
        path = str(args.get("path", "") or "").strip()
        package = str(args.get("package", "") or "").strip()
        if bool(path) == bool(package):
            return "错误：path（工作区插件目录）与 package（inbox 里的 zip）必须二选一"
        if path:
            _ok, msg = client.publish_from_directory(path)
        else:
            _ok, msg = client.publish_zip(package)
        return msg

    registry.register(
        Tool(
            name="community_config",
            description="查看/设置插件社区服务器（server_url 与发布 token；传空串清除）",
            parameters={
                "type": "object",
                "properties": {
                    "server_url": {"type": "string", "description": "社区服务器 http(s)://host:port"},
                    "token": {"type": "string", "description": "发布 token（发布需要；查询与安装不需要）"},
                },
            },
            func=community_config,
            toolset="core",
            risk=Risk.SAFE,
        )
    )
    registry.register(
        Tool(
            name="community_search",
            description="搜索插件社区里的插件（按 id/名称/描述/作者匹配）",
            parameters={
                "type": "object",
                "properties": {
                    "query": {"type": "string", "description": "搜索关键词"},
                    "limit": {"type": "integer", "description": "最多返回几条（默认 10）"},
                },
                "required": ["query"],
            },
            func=community_search,
            toolset="core",
            risk=Risk.SAFE,
            read_only=True,
        )
    )
    registry.register(
        Tool(
            name="community_info",
            description="查看社区里某个插件的详情（版本列表/下载量/最新包指纹）",
            parameters={
                "type": "object",
                "properties": {"id": {"type": "string", "description": "插件 id"}},
                "required": ["id"],
            },
            func=community_info,
            toolset="core",
            risk=Risk.SAFE,
            read_only=True,
        )
    )
    registry.register(
        Tool(
            name="community_install",
            description=(
                "从社区下载插件包到收件箱并走隔离安装（sha256 校验；"
                "激活仍需人工确认，社区不背书安全性）"
            ),
            parameters={
                "type": "object",
                "properties": {
                    "id": {"type": "string", "description": "社区里的插件 id"},
                    "version": {"type": "string", "description": "版本号（默认最新）"},
                },
                "required": ["id"],
            },
            func=community_install,
            toolset="core",
            risk=Risk.SAFE,
        )
    )
    registry.register(
        Tool(
            name="community_publish",
            description=(
                "把插件发布到社区（需用户确认——发布是项目红线）：path=工作区插件目录"
                "（自动打包，跳过 __pycache__）或 package=inbox 里的 zip"
            ),
            parameters={
                "type": "object",
                "properties": {
                    "path": {"type": "string", "description": "工作区里的插件目录（与 package 二选一）"},
                    "package": {"type": "string", "description": "inbox 里的 zip 文件名（与 path 二选一）"},
                },
            },
            func=community_publish,
            toolset="core",
            risk=Risk.CONFIRM,
            requires_human=True,
        )
    )
