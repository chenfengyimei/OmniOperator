"""Bind MCP authorization, transport recovery and the per-run capability provider."""

from __future__ import annotations

import json
import logging
from typing import cast

from ...adapters.mcp.hosts import MCPHosts
from ...adapters.mcp.oauth import OAuthFlow
from ...application.capabilities import RunCapabilityProvider
from ...application.mcp import MCPService
from ...domain.mcp import ServerTarget
from . import FeatureBinding, FeatureContext


async def install_mcp(context: FeatureContext) -> FeatureBinding:
    transport = context.mcp_transport
    if transport is None:
        oauth = OAuthFlow(context.secrets, redact=context.redact_secret)

        async def authorize(target: ServerTarget) -> str:
            if target.auth_mode == "none":
                return ""
            if target.auth_mode == "oauth":
                token = await oauth.tokens.access_token(target.server_id)
                if token:
                    return token
            if not target.credential_ref:
                return ""
            raw = await context.secrets.get(target.credential_ref)
            if not raw:
                return ""
            try:
                parsed: object = json.loads(raw)
            except (ValueError, TypeError):
                return raw
            if isinstance(parsed, dict):
                fields = cast(dict[str, object], parsed)
                return str(fields.get("access_token") or "")
            return raw

        transport = MCPHosts(authorize, cwd=str(context.control), oauth=oauth)

    # The transport has one shutdown owner even if service construction fails.
    context.lifetime.own("mcp", 0, transport.close, transport.stop_accepting)
    service = MCPService(context.database, transport, context.clock)
    context.lifetime.own("mcp", 0, service.close, service.stop_accepting)
    recovered = await service.recover()
    if any(recovered.values()):
        logging.getLogger("omni.v2.startup").warning(
            "MCP records from the previous process were reconciled",
            extra={"event": "mcp.restart_reconciled", **recovered},
        )
    return FeatureBinding(service, run_provider=RunCapabilityProvider("mcp", service, lambda _: service.quiescent()))
