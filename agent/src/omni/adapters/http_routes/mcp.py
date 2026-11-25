"""Owner operations to connect, authorize and inspect agent-scoped MCP providers."""

from __future__ import annotations

import uuid
from collections.abc import Callable
from typing import cast

from fastapi import APIRouter, Query, Request
from pydantic import Field

from ...application.mcp import MCPService
from ...domain.contracts import BusinessError
from . import RuntimeLookup, StrictInput, bounded_configuration, configuration_input


class MCPConnectInput(StrictInput):
    revision: int = Field(ge=1, strict=True)


class MCPToolAuthorizationInput(StrictInput):
    tool_id: uuid.UUID
    read_only: bool = Field(strict=True)


class MCPAuthorizationInput(StrictInput):
    catalog_fingerprint: str = Field(pattern=r"^[a-f0-9]{64}$")
    tools: list[MCPToolAuthorizationInput] = Field(
        default_factory=cast(Callable[[], list[MCPToolAuthorizationInput]], list), max_length=256
    )
    evidence: str = Field(min_length=1, max_length=200)


class MCPToolDecisionInput(StrictInput):
    catalog_fingerprint: str = Field(pattern=r"^[a-f0-9]{64}$")
    read_only: bool = Field(default=False, strict=True)
    authorized: bool = Field(default=True, strict=True)
    evidence: str = Field(min_length=1, max_length=200)


class MCPOAuthStartInput(StrictInput):
    redirect_uri: str = Field(default="", max_length=2000)


def mcp_router(runtime: RuntimeLookup) -> APIRouter:
    router = APIRouter(prefix="/agents/{agent_id}/mcp")

    def mcp_service(request: Request) -> MCPService:
        service = runtime(request).feature_services.get("mcp")
        if not isinstance(service, MCPService):
            raise BusinessError("mcp_unavailable", "MCP capability is unavailable", status=503)
        return service

    @router.get("/servers")
    async def mcp_servers(agent_id: uuid.UUID, request: Request):
        return await mcp_service(request).servers(str(agent_id))

    @router.get("/servers/{config_id}")
    async def mcp_server(agent_id: uuid.UUID, config_id: uuid.UUID, request: Request):
        return await mcp_service(request).server(str(agent_id), str(config_id))

    @router.post("/servers/{config_id}/connect")
    async def mcp_connect(agent_id: uuid.UUID, config_id: uuid.UUID, request: Request):
        data = configuration_input(MCPConnectInput, await bounded_configuration(request, 1024, strict=True))
        return await mcp_service(request).connect(str(agent_id), str(config_id), data.revision)

    @router.post("/servers/{config_id}/disconnect")
    async def mcp_disconnect(agent_id: uuid.UUID, config_id: uuid.UUID, request: Request):
        return await mcp_service(request).disconnect(str(agent_id), str(config_id))

    @router.post("/servers/{config_id}/reset")
    async def mcp_reset(agent_id: uuid.UUID, config_id: uuid.UUID, request: Request):
        return await mcp_service(request).reset(str(agent_id), str(config_id))

    @router.get("/servers/{config_id}/catalog")
    async def mcp_catalog(
        agent_id: uuid.UUID,
        config_id: uuid.UUID,
        request: Request,
        after: int = Query(default=-1, ge=-1),
        limit: int = Query(default=20, ge=1, le=100),
    ):
        return await mcp_service(request).catalog(str(agent_id), str(config_id), after, limit)

    @router.put("/servers/{config_id}/authorization")
    async def mcp_authorize(agent_id: uuid.UUID, config_id: uuid.UUID, request: Request):
        data = configuration_input(MCPAuthorizationInput, await bounded_configuration(request, 65536, strict=True))
        return await mcp_service(request).authorize(
            str(agent_id),
            str(config_id),
            {
                "catalog_fingerprint": data.catalog_fingerprint,
                "tools": [{"tool_id": str(item.tool_id), "read_only": item.read_only} for item in data.tools],
                "evidence": data.evidence,
            },
        )

    @router.put("/servers/{config_id}/tools/{tool_id}/authorization")
    async def mcp_authorize_tool(agent_id: uuid.UUID, config_id: uuid.UUID, tool_id: uuid.UUID, request: Request):
        data = configuration_input(MCPToolDecisionInput, await bounded_configuration(request, 4096, strict=True))
        return await mcp_service(request).authorize_tool(
            str(agent_id),
            str(config_id),
            str(tool_id),
            authorized=data.authorized,
            read_only=data.read_only,
            catalog_fingerprint=data.catalog_fingerprint,
            evidence=data.evidence,
        )

    @router.get("/servers/{config_id}/calls")
    async def mcp_calls(
        agent_id: uuid.UUID,
        config_id: uuid.UUID,
        request: Request,
        after: uuid.UUID | None = None,
        limit: int = Query(default=50, ge=1, le=100),
    ):
        return await mcp_service(request).calls(str(agent_id), str(config_id), str(after) if after else "", limit)

    @router.post("/servers/{config_id}/oauth/start")
    async def mcp_oauth_start(agent_id: uuid.UUID, config_id: uuid.UUID, request: Request):
        data = configuration_input(MCPOAuthStartInput, await bounded_configuration(request, 4096, strict=True))
        redirect = data.redirect_uri or str(request.base_url).rstrip("/") + "/oauth/callback"
        return await mcp_service(request).oauth_start(str(agent_id), str(config_id), redirect)

    @router.get("/health")
    async def mcp_health(agent_id: uuid.UUID, request: Request):
        return mcp_service(request).health()

    return router
