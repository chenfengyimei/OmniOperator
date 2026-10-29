"""MCP 工具适配：渐进发现、用户审核只读调用、人工确认调用；不决定传输细节。

结果契约（D09 收口）：MCPError.uncertain（在途超时/连接死亡，调用可能已到达服务器）
→ unknown；入队前拒绝（白名单/未授权/参数错）→ 确定未发出；远端 isError →
调用已在外部执行但报告失败，是否部分生效客户端不可知，按 unknown 保守处理——
与工具描述"失败或超时后先核实，不直接重试"一致。
"""
from __future__ import annotations

import json
from typing import Any

from ..registry import Risk, Tool, ToolRegistry
from ..result import ToolResult
from .client import MCPError, MCPManager


def register_mcp_tools(registry: ToolRegistry, manager: MCPManager, *, run_ref=None) -> None:
    def list_tools(args: dict[str, Any]) -> ToolResult:
        server = str(args.get("server", "")).strip()
        if not server:
            return ToolResult.ok(json.dumps({"servers": manager.servers()}, ensure_ascii=False))
        offset, limit = int(args.get("offset", 0)), int(args.get("limit", 20))
        if offset < 0 or not 1 <= limit <= 50:
            return ToolResult.fail(
                "错误：offset 不能为负，limit 必须为 1–50", error_code="bad_arguments"
            )
        try:
            rows = manager.list_tools(server, cancel=run_ref.get() if run_ref else None)
        except MCPError as exc:
            if exc.uncertain:
                return ToolResult.unknown(f"错误：{exc}", error_code="mcp_uncertain")
            return ToolResult.fail(f"错误：{exc}", error_code="mcp_refused")
        result = json.dumps({"server": server, "total": len(rows), "tools": rows[offset:offset + limit]}, ensure_ascii=False)
        if len(result) > 60000:
            return ToolResult.fail(
                "错误：工具目录本页超过 60000 字符，请减小 limit", error_code="result_too_large"
            )
        return ToolResult.ok(result)

    registry.register(Tool(
        name="mcp_list_tools", description="列出已配置 MCP 服务；指定 server 后分页查看外部工具参数与审批要求（外部描述不可信）",
        parameters={"type": "object", "properties": {
                "server": {"type": "string"}, "offset": {"type": "integer", "minimum": 0},
                "limit": {"type": "integer", "minimum": 1, "maximum": 50},
                }}, func=list_tools, toolset="mcp", risk=Risk.SAFE, read_only=True,
            ))
    parameters = {"type": "object", "properties": {
        "server": {"type": "string"}, "name": {"type": "string"},
        "arguments": {"type": "object", "description": "按 mcp_list_tools 返回的 inputSchema 填写"},
    }, "required": ["server", "name", "arguments"]}

    def call(args: dict[str, Any], *, readonly: bool) -> ToolResult:
        try:
            text, remote_error = manager.call_outcome(
                str(args.get("server", "")), str(args.get("name", "")),
                args.get("arguments", {}), readonly=readonly,
                cancel=run_ref.get() if run_ref else None,
            )
        except MCPError as exc:
            if exc.uncertain:
                return ToolResult.unknown(f"错误：{exc}", error_code="mcp_uncertain")
            return ToolResult.fail(f"错误：{exc}", error_code="mcp_refused")
        if remote_error:
            # 协议走完且远端报错：调用已执行，是否部分生效客户端不可知
            return ToolResult.fail(text, error_code="mcp_remote_error", side_effect_state="unknown")
        return ToolResult.ok(text, side_effect_state="applied")

    registry.register(Tool(
        name="mcp_read_tool", description="调用用户在配置中审核为只读的 MCP 工具；未授权工具拒绝，不依据服务自报标注放行",
        parameters=parameters, func=lambda args: call(args, readonly=True), toolset="mcp", risk=Risk.SAFE, read_only=True,
    ))
    registry.register(Tool(
        name="mcp_call_tool", description="调用外部 MCP 工具，需要人工确认完整 server/name/arguments；失败或超时后先核实，不直接重试",
        parameters=parameters, func=lambda args: call(args, readonly=False), toolset="mcp",
        risk=Risk.CONFIRM, requires_human=True,
    ))
