"""Bounded Streamable HTTP JSON-RPC transport with explicit bearer/OAuth tokens."""

from __future__ import annotations

import asyncio
import inspect
import json
import logging
from collections.abc import Awaitable, Callable
from typing import Any, cast

import httpx

from ...domain.mcp import (
    CATALOG_BUDGET_S,
    CLIENT_NAME,
    HANDSHAKE_TIMEOUT_S,
    MAX_FRAME_BYTES,
    MAX_PAGES,
    MAX_TOOLS,
    PROTOCOL_VERSION,
    MCPDispatchError,
    MCPUncertain,
    ServerTarget,
)

log = logging.getLogger("omni.v2.mcp")
Packet = dict[str, Any]

CONNECT_TIMEOUT_S = 10.0
MAX_SSE_EVENTS = 256


class HttpSession:
    """One remote MCP endpoint; a response is matched to its request id and never guessed."""

    kind = "streamable_http"

    def __init__(
        self,
        target: ServerTarget,
        authorization: Callable[[], Awaitable[str]],
        *,
        client_factory: Callable[..., httpx.AsyncClient] | None = None,
    ) -> None:
        self.target = target
        self._authorization = authorization
        self._client_factory = client_factory or httpx.AsyncClient
        self._client: httpx.AsyncClient | None = None
        self._session_id = ""
        self._lock = asyncio.Lock()
        self._next_id = 1
        self._closed = False
        self._handshake: Packet = {}

    def _session(self) -> httpx.AsyncClient:
        if self._client is None:
            self._client = self._client_factory(
                trust_env=False,
                follow_redirects=False,
                limits=httpx.Limits(max_connections=2, max_keepalive_connections=1),
            )
        return self._client

    async def close(self, timeout_s: float = 5) -> bool:
        self._closed = True
        if self._client is not None:
            await self._client.aclose()
            self._client = None
        return True

    def health(self) -> Packet:
        return {
            "transport": self.kind,
            "running": self._client is not None and not self._closed,
            "session": bool(self._session_id),
            "cleanup_pending": False,
            "closed": self._closed,
            "handshake": self._handshake,
        }

    # ---------- transport ----------

    async def _headers(self) -> dict[str, str]:
        headers = {
            "Content-Type": "application/json",
            "Accept": "application/json, text/event-stream",
            "MCP-Protocol-Version": PROTOCOL_VERSION,
            "User-Agent": "OmniOperator/2",
        }
        if self._session_id:
            headers["Mcp-Session-Id"] = self._session_id
        token = self._authorization()
        if inspect.isawaitable(token):
            token = await token
        if token:
            headers["Authorization"] = "Bearer " + token
        return headers

    @staticmethod
    def _matching(value: object, identity: int) -> Packet | None:
        if (
            isinstance(value, dict)
            and cast(Packet, value).get("id") == identity
            and cast(Packet, value).get("jsonrpc") == "2.0"
        ):
            return cast(Packet, value)
        if isinstance(value, list):
            for item in cast(list[object], value):
                if (
                    isinstance(item, dict)
                    and cast(Packet, item).get("id") == identity
                    and cast(Packet, item).get("jsonrpc") == "2.0"
                ):
                    return cast(Packet, item)
        return None

    async def _read_sse(self, response: httpx.Response, identity: int) -> Packet:
        data: list[str] = []
        events = 0
        total = 0
        async for line in response.aiter_lines():
            total += len(line) + 1
            if total > MAX_FRAME_BYTES:
                raise MCPUncertain("mcp_response_too_large", "MCP event stream exceeded the transport bound")
            if line == "":
                if not data:
                    continue
                events += 1
                if events > MAX_SSE_EVENTS:
                    raise MCPUncertain("mcp_protocol_error", "MCP event stream exceeded its event bound")
                payload = "\n".join(data)
                data = []
                try:
                    parsed = json.loads(payload)
                except (ValueError, UnicodeError, RecursionError) as exc:
                    raise MCPUncertain("mcp_protocol_error", "MCP event stream carried invalid JSON") from exc
                matched = self._matching(parsed, identity)
                if matched is not None:
                    return matched
                continue
            if line.startswith("data:"):
                data.append(line[5:].lstrip(" "))
        if data:
            try:
                parsed = json.loads("\n".join(data))
            except (ValueError, UnicodeError, RecursionError) as exc:
                raise MCPUncertain("mcp_protocol_error", "MCP event stream carried invalid JSON") from exc
            matched = self._matching(parsed, identity)
            if matched is not None:
                return matched
        raise MCPUncertain("mcp_protocol_error", "MCP event stream ended without a matching response")

    async def _post(
        self, payload: Packet, identity: int, timeout_s: float, *, uncertain_on_loss: bool, strict: bool = True
    ) -> Packet:
        if self._closed:
            raise MCPDispatchError("mcp_not_connected", "MCP session is closed")
        client = self._session()
        headers = await self._headers()
        response_received = False
        try:
            async with asyncio.timeout(timeout_s + CONNECT_TIMEOUT_S):
                async with client.stream(
                    "POST",
                    self.target.endpoint,
                    json=payload,
                    headers=headers,
                    timeout=httpx.Timeout(timeout_s, connect=CONNECT_TIMEOUT_S, write=CONNECT_TIMEOUT_S),
                ) as response:
                    response_received = True
                    session_id = response.headers.get("mcp-session-id", "")
                    if session_id:
                        self._session_id = session_id[:128]
                    if response.status_code == 202:
                        return {}
                    if response.status_code in {401, 403}:
                        raise MCPDispatchError(
                            "mcp_unauthorized",
                            "This MCP server rejected the stored authorization; start OAuth explicitly if it is required",
                            sent=False,
                            status=401,
                        )
                    if response.status_code >= 400:
                        if response.status_code >= 500:
                            raise MCPUncertain("mcp_http_status", f"MCP server returned HTTP {response.status_code}")
                        raise MCPDispatchError(
                            "mcp_http_status",
                            f"MCP server returned HTTP {response.status_code}",
                            sent=False,
                        )
                    content_type = response.headers.get("content-type", "").lower()
                    if "text/event-stream" in content_type:
                        matched = await self._read_sse(response, identity)
                    else:
                        body = bytearray()
                        async for part in response.aiter_bytes():
                            body.extend(part)
                            if len(body) > MAX_FRAME_BYTES:
                                raise MCPUncertain(
                                    "mcp_response_too_large", "MCP response exceeded the transport bound"
                                )
                        try:
                            parsed = json.loads(body)
                        except (ValueError, UnicodeError, RecursionError) as exc:
                            raise MCPUncertain("mcp_protocol_error", "MCP response was not valid JSON") from exc
                        matched = self._matching(parsed, identity)
                        if matched is None:
                            if not strict:
                                return {}
                            raise MCPUncertain("mcp_protocol_error", "MCP response identity did not match its request")
        except TimeoutError as exc:
            if uncertain_on_loss:
                raise MCPUncertain(
                    "mcp_call_timeout", "The dispatched MCP request timed out; delivery is unknown"
                ) from exc
            raise MCPDispatchError("mcp_timeout", "The MCP request exceeded its time budget") from exc
        except (httpx.TimeoutException, httpx.TransportError) as exc:
            # Entering a stream waits for response headers, after the outgoing
            # request. Only connection/pool failures prove it could not send.
            not_sent = not response_received and isinstance(
                exc, (httpx.ConnectError, httpx.ConnectTimeout, httpx.PoolTimeout)
            )
            if uncertain_on_loss and not not_sent:
                raise MCPUncertain(
                    "mcp_connection_lost", "The dispatched MCP request lost its response; delivery is unknown"
                ) from exc
            raise MCPDispatchError("mcp_connection_lost", "The MCP endpoint is unreachable") from exc
        except asyncio.CancelledError:
            if uncertain_on_loss:
                raise MCPUncertain(
                    "mcp_cancelled", "The dispatched MCP request was cancelled; delivery is unknown"
                ) from None
            raise
        if "error" in matched:
            error = matched["error"]
            code = str(cast(Packet, error).get("code", ""))[:40] if isinstance(error, dict) else ""
            raise MCPDispatchError("mcp_server_error", f"MCP server rejected the request ({code})".strip())
        result = matched.get("result")
        if not isinstance(result, dict):
            raise MCPUncertain("mcp_protocol_error", "MCP response has no bounded result object")
        return cast(Packet, result)

    async def _request(self, method: str, params: Packet, timeout_s: float, *, uncertain_on_loss: bool) -> Packet:
        async with self._lock:
            identity = self._next_id
            self._next_id += 1
            return await self._post(
                {"jsonrpc": "2.0", "id": identity, "method": method, "params": params},
                identity,
                timeout_s,
                uncertain_on_loss=uncertain_on_loss,
            )

    async def _notify(self, method: str, params: Packet) -> None:
        async with self._lock:
            await self._post(
                {"jsonrpc": "2.0", "method": method, "params": params},
                0,
                CONNECT_TIMEOUT_S,
                uncertain_on_loss=False,
                strict=False,
            )

    # ---------- MCP operations ----------

    async def start(self) -> Packet:
        result = await self._request(
            "initialize",
            {
                "protocolVersion": PROTOCOL_VERSION,
                "capabilities": {},
                "clientInfo": {"name": CLIENT_NAME, "version": "2"},
            },
            HANDSHAKE_TIMEOUT_S,
            uncertain_on_loss=False,
        )
        server_info = result.get("serverInfo")
        info: Packet = cast(Packet, server_info) if isinstance(server_info, dict) else {}
        self._handshake = {
            "protocol_version": str(result.get("protocolVersion") or PROTOCOL_VERSION)[:40],
            "server_name": str(info.get("name", ""))[:100],
            "server_version": str(info.get("version", ""))[:40],
        }
        await self._notify("notifications/initialized", {})
        return self._handshake

    async def catalog(self) -> list[Packet]:
        tools: list[Packet] = []
        cursor = ""
        started = asyncio.get_running_loop().time()
        for _ in range(MAX_PAGES):
            remaining = CATALOG_BUDGET_S - (asyncio.get_running_loop().time() - started)
            if remaining <= 0:
                raise MCPDispatchError("mcp_catalog_timeout", "MCP catalog discovery exceeded its budget")
            page = await self._request(
                "tools/list", {"cursor": cursor} if cursor else {}, remaining, uncertain_on_loss=False
            )
            listed = cast(list[Packet], page.get("tools"))
            if not isinstance(cast(object, listed), list) or len(listed) > MAX_TOOLS:
                raise MCPDispatchError("mcp_catalog_invalid", "MCP catalog page was not bounded")
            tools.extend(item for item in listed if isinstance(cast(object, item), dict))
            if len(tools) > MAX_TOOLS:
                raise MCPDispatchError("mcp_catalog_too_large", "The MCP catalog exceeds its tool bound")
            cursor = page.get("nextCursor") or ""
            if not isinstance(cursor, str) or not cursor:
                return tools
        raise MCPDispatchError("mcp_catalog_too_large", "MCP catalog pagination exceeded its page bound")

    async def call(
        self,
        name: str,
        arguments: Packet,
        timeout_s: float,
        *,
        before_send: Callable[[], Awaitable[None]] | None = None,
    ) -> Packet:
        if before_send is not None:
            await before_send()
        return await self._request(
            "tools/call", {"name": name, "arguments": arguments}, timeout_s, uncertain_on_loss=True
        )


__all__ = ["HttpSession"]
