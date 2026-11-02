"""MCP 管理 HTTP 适配；使用注入 Runtime，保留既有权限、路径与响应契约。"""

from __future__ import annotations

from typing import TYPE_CHECKING, Any

from fastapi import APIRouter, HTTPException

from ...tools.mcp.client import MCPError
from .schemas import MCPSchemaConfirmIn

if TYPE_CHECKING:
    from ...bootstrap import Runtime


def create_mcp_router(runtime: Runtime) -> APIRouter:
    router = APIRouter(tags=["mcp"])

    @router.get("/api/mcp/{server}/health")
    def mcp_health(server: str):
        try:
            return {**runtime.mcp.health(server), "configuration_matches": runtime.mcp.configuration_matches()}
        except MCPError as exc:
            raise HTTPException(404, str(exc)) from exc

    @router.get("/api/mcp/{server}/logs")
    def mcp_logs(server: str):
        try:
            return runtime.mcp.logs(server)
        except (MCPError, OSError) as exc:
            raise HTTPException(409, str(exc)) from exc

    @router.post("/api/mcp/{server}/connect")
    def mcp_connect(server: str):
        try:
            tools = runtime.mcp.list_tools(server)
            return {"state": "ready", "tool_count": len(tools)}
        except MCPError as exc:
            raise HTTPException(409, str(exc)) from exc

    @router.post("/api/mcp/{server}/disconnect")
    def mcp_disconnect(server: str):
        try:
            return runtime.mcp.disconnect(server)
        except MCPError as exc:
            raise HTTPException(409, str(exc)) from exc

    @router.get("/api/mcp/{server}/catalog")
    def mcp_catalog(server: str, offset: int = 0, limit: int = 20):
        if offset < 0 or not 1 <= limit <= 50:
            raise HTTPException(422, "目录分页范围非法")
        try:
            rows = runtime.mcp.list_tools(server)
        except MCPError as exc:
            raise HTTPException(409, str(exc)) from exc
        schema = runtime.mcp_schemas.status(server) if runtime.mcp_schemas else None
        return {
            "tools": rows[offset : offset + limit],
            "total": len(rows),
            "offset": offset,
            "fingerprint": schema["fingerprint"] if schema else "",
            "confirmed": bool(schema and schema["confirmed_at"]),
        }

    @router.get("/api/mcp/overview")
    def mcp_overview() -> dict[str, Any]:
        """全部已配置 MCP 服务的状态：纯派生，**不启动任何服务**。

        停用的服务也在这里可见（面板能看到"配置过但停了"，而不是查无此人）。
        带上 schema 指纹状态：`schema_state` = absent（还没发现过工具）/
        confirmed（当前指纹已确认）/ drifted（指纹变了，只读授权该复查）。
        """
        servers = runtime.mcp.status()
        for row in servers:
            schema = runtime.mcp_schemas.status(row["name"]) if runtime.mcp_schemas is not None else None
            if schema is None:
                row["schema_state"] = "absent"
                row["schema_fingerprint"] = ""
            else:
                row["schema_fingerprint"] = schema["fingerprint"]
                row["schema_state"] = "confirmed" if schema["confirmed_at"] else "drifted"
        return {"servers": servers}

    @router.get("/api/mcp/{server}/tools")
    def mcp_tools(server: str, limit: int = 50) -> dict[str, Any]:
        """工具目录：会**真实启动该服务**做一次只读发现（用户点了按钮才算数）。

        返回的 approval 标记用户配置的白名单判定：safe=只读白名单（模型直调），
        human=需要人工确认；服务自报的 readOnlyHint 不参与授权。
        发现时顺带登记 schema 指纹（D19 漂移检测）：变了就在响应里带 drift 提醒。
        """
        try:
            rows = runtime.mcp.list_tools(server)
        except MCPError as exc:
            message = str(exc)
            if "未配置" in message or "未启用" in message:
                raise HTTPException(status_code=404, detail=message)
            raise HTTPException(status_code=409, detail=message)
        drift = ""
        schema = runtime.mcp_schemas.status(server) if runtime.mcp_schemas else None
        if schema is not None and not schema["confirmed_at"]:
            drift = "工具目录或连接身份已变化：只读调用已暂停。请复查当前目录与只读白名单后确认"
        return {
            "server": server,
            "total": len(rows),
            "tools": rows[: max(1, min(limit, 100))],
            "drift": drift,
            "fingerprint": schema["fingerprint"] if schema else "",
        }

    @router.post("/api/mcp/{server}/confirm-schema")
    def mcp_confirm_schema(server: str, payload: MCPSchemaConfirmIn) -> dict[str, Any]:
        """用户复查后确认当前工具目录：清除漂移提醒（确认的就是当前这份）。"""
        if runtime.mcp_schemas is None:
            raise HTTPException(status_code=503, detail="schema 记录不可用")
        schema = runtime.mcp_schemas.status(server)
        if schema is None:
            raise HTTPException(status_code=404, detail="该服务还没有工具目录记录（先「查看工具」）")
        try:
            runtime.mcp.list_tools(server)  # 复查期间若再次升级，旧页面的确认必须失败。
        except MCPError as exc:
            raise HTTPException(status_code=409, detail=str(exc)) from exc
        if not runtime.mcp_schemas.confirm(server, payload.fingerprint):
            raise HTTPException(status_code=409, detail="指纹又变了，请重新「查看工具」后再确认")
        return {"ok": True}

    @router.post("/api/mcp/{server}/reset")
    def mcp_reset(server: str) -> dict[str, Any]:
        """手动重连：失败/已关闭的连接给一次干净的握手（配置不变）。"""
        try:
            status = runtime.mcp.reset(server)
        except MCPError as exc:
            message = str(exc)
            if "未配置" in message or "未启用" in message:
                raise HTTPException(status_code=404, detail=message)
            raise HTTPException(status_code=409, detail=message)
        return {"ok": True, "state": status["state"]}

    @router.post("/api/mcp/{server}/authorize", status_code=202)
    def mcp_authorize(server: str) -> dict:
        try:
            return runtime.mcp.authorize(server)
        except (ValueError, MCPError) as exc:
            raise HTTPException(409, str(exc)) from exc

    @router.post("/api/mcp/{server}/oauth-callback")
    def mcp_oauth_callback(server: str, payload: dict) -> dict:
        try:
            return runtime.mcp.authorization_callback(
                server, str(payload.get("code", "")), str(payload.get("state", "")), payload.get("iss")
            )
        except (ValueError, MCPError) as exc:
            raise HTTPException(409, "授权回调无效或已过期") from exc

    @router.post("/api/mcp/oauth-callback")
    def oauth_callback(payload: dict) -> dict:
        try:
            return runtime.mcp.complete_authorization(
                str(payload.get("code", "")), str(payload.get("state", "")), payload.get("iss")
            )
        except (ValueError, MCPError) as exc:
            raise HTTPException(409, "没有匹配的待授权请求或回调已过期") from exc

    return router
