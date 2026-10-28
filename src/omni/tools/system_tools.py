"""系统工具集（toolset=system）：AI 自查本地服务状态、引导用户处置。

设计（用户需求："AI 自己自查自操作，然后给用户二维码"）：
- system_status：SAFE，任何上下文可用——发现异常时给出面向用户的处置指引
  （如"请打开工作台 xxx 扫码"），把"操作权"交还给人；
- system_service：CONFIRM——启停属于本地系统变更，须用户确认
  （QQ/心跳等无人值守上下文会被 DenyApprover 直接拒绝，符合 fail-closed）。

结果契约（D09 收口）：启停成功记 side_effect_state=applied；
ServiceError.uncertain=True（进程可能已启动/停止未确认）记 unknown，其余错误记 none。
"""
from __future__ import annotations

from typing import Any

from ..system.services import ALL_SERVICES, ServiceError, ServiceManager
from .registry import Risk, Tool, ToolRegistry
from .result import SIDE_EFFECT_APPLIED, SIDE_EFFECT_NONE, SIDE_EFFECT_UNKNOWN, ToolResult


def _service_error(exc: ServiceError) -> ToolResult:
    """ServiceError → 结构化结果：uncertain 表示状态可能已部分改变（人工核查）。"""
    return ToolResult.fail(
        f"错误：{exc}",
        error_code="service_error",
        side_effect_state=SIDE_EFFECT_UNKNOWN if exc.uncertain else SIDE_EFFECT_NONE,
    )


def register_system_tools(registry: ToolRegistry, services: ServiceManager) -> None:

    def system_status(_args: dict[str, Any]) -> ToolResult:
        overview = services.overview()
        lines = ["本地服务状态："]
        for svc in overview["services"]:
            mark = "✅ 运行中" if svc["running"] else "⛔ 已停止"
            detail = f"（{svc['detail']}）" if svc["detail"] else ""
            lines.append(f"- {svc['label']}：{mark}{detail}")
        napcat = overview["napcat"]
        if not napcat["installed"]:
            lines.append("⚠ NapCat 未安装：工作台无法管理 QQ 通道")
        elif not napcat["logged_in"]:
            lines.append(
                "⚠ QQ 机器人未登录：请用户打开 Web 工作台（http://127.0.0.1:8000）"
                "的「服务」面板，点击「扫码登录」用手机 QQ（小号）扫码"
            )
        return ToolResult.ok("\n".join(lines))

    def system_service(args: dict[str, Any]) -> ToolResult:
        action = str(args.get("action", "")).strip()
        name = str(args.get("service", "")).strip()
        if action not in ("start", "stop"):
            return ToolResult.fail("错误：action 只支持 start / stop", error_code="bad_arguments")
        if name not in ALL_SERVICES:
            return ToolResult.fail(
                f"错误：service 只支持 {', '.join(ALL_SERVICES)}", error_code="bad_arguments"
            )
        if action == "start" and name == "napcat" and args.get("qq"):
            qq = str(args["qq"]).strip()
            if not (qq.isdigit() and len(qq) <= 12):
                return ToolResult.fail("错误：qq 必须是 QQ 号数字", error_code="bad_arguments")
            try:
                result = services.start(name, qq=qq)
            except ServiceError as exc:
                return _service_error(exc)
            return ToolResult.ok(
                f"已启动 {name}（pid {result['pid']}）。若是首次登录或快速登录被风控，"
                "会进入扫码模式：请让用户打开 Web 工作台「服务」面板扫码",
                side_effect_state=SIDE_EFFECT_APPLIED,
            )
        try:
            result = services.start(name) if action == "start" else services.stop(name)
        except ServiceError as exc:
            return _service_error(exc)
        verb = "已启动" if action == "start" else "已停止"
        pid = result.get("pid", "")
        return ToolResult.ok(
            f"{verb} {name}" + (f"（pid {pid}）" if pid else ""),
            side_effect_state=SIDE_EFFECT_APPLIED,
        )

    registry.register(
        Tool(
            name="system_status",
            description="自查本地服务状态（NapCat/QQ通道/心跳是否在运行、QQ 是否已登录），并给出处置建议",
            parameters={"type": "object", "properties": {}, "required": []},
            func=system_status,
            toolset="system",
            risk=Risk.SAFE,
            read_only=True,
        )
    )
    registry.register(
        Tool(
            name="system_service",
            description=(
                "启动/停止本地服务（napcat / omni-qq / omni-heartbeat）。"
                "启动 napcat 可带 qq 号快速登录（新设备可能被风控，会回退扫码，"
                "届时引导用户打开 Web 工作台扫码）。属系统变更，需用户确认"
            ),
            parameters={
                "type": "object",
                "properties": {
                    "action": {"type": "string", "enum": ["start", "stop"]},
                    "service": {
                        "type": "string",
                        "enum": ["napcat", "omni-qq", "omni-heartbeat"],
                    },
                    "qq": {"type": "string", "description": "（可选）napcat 快速登录的 QQ 号"},
                },
                "required": ["action", "service"],
            },
            func=system_service,
            toolset="system",
            risk=Risk.CONFIRM,
        )
    )
