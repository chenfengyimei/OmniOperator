"""Bounded MCP session ownership keyed by server generation; no database or model access."""

from __future__ import annotations

import asyncio
from collections.abc import Awaitable, Callable
from typing import Any, Protocol

from ...domain.contracts import BusinessError
from ...domain.mcp import ServerTarget
from .http import HttpSession
from .oauth import OAuthFlow
from .stdio import StdioSession

MAX_SESSIONS = 8
MAX_PENDING = 32


class MCPSessionPort(Protocol):
    async def start(self) -> dict[str, Any]: ...

    async def catalog(self) -> list[dict[str, Any]]: ...

    async def call(
        self,
        name: str,
        arguments: dict[str, Any],
        timeout_s: float,
        *,
        before_send: Callable[[], Awaitable[None]] | None = None,
    ) -> dict[str, Any]: ...

    async def close(self, timeout_s: float = 5) -> bool: ...

    def health(self) -> dict[str, Any]: ...


class MCPHosts:
    """One live session per (agent, server, authority epoch); stale generations are closed first."""

    def __init__(
        self,
        authorization: Callable[[ServerTarget], Awaitable[str]],
        *,
        cwd: str,
        session_factory: Callable[[ServerTarget], MCPSessionPort] | None = None,
        oauth: OAuthFlow | None = None,
    ) -> None:
        self._authorization = authorization
        self._cwd = cwd
        self._factory = session_factory or self._default_session
        self._oauth = oauth
        self._sessions: dict[tuple[str, str, int], MCPSessionPort] = {}
        self._handshakes: dict[tuple[str, str, int], dict[str, Any]] = {}
        self._pending = 0
        self._lock = asyncio.Lock()
        self._closing = False

    def _default_session(self, target: ServerTarget) -> MCPSessionPort:
        if target.transport == "stdio":
            return StdioSession(target, cwd=self._cwd)

        async def authorization() -> str:
            return await self._authorization(target)

        return HttpSession(target, authorization)

    @staticmethod
    def _key(target: ServerTarget) -> tuple[str, str, int]:
        return target.agent_id, target.server_id, target.authority_epoch

    def _server_keys(self, agent_id: str, server_id: str) -> list[tuple[str, str, int]]:
        return [key for key in self._sessions if key[0] == agent_id and key[1] == server_id]

    async def connect(self, target: ServerTarget) -> dict[str, Any]:
        if self._closing:
            raise BusinessError("mcp_closing", "MCP sessions are draining", status=503)
        key = self._key(target)
        async with self._lock:
            for stale in self._server_keys(target.agent_id, target.server_id):
                if stale != key:
                    session = self._sessions.pop(stale)
                    self._handshakes.pop(stale, None)
                    if not await session.close(5):
                        raise BusinessError(
                            "mcp_cleanup_pending",
                            "The previous MCP process or connection was not reclaimed; reset this server",
                            status=503,
                        )
            session = self._sessions.get(key)
            if session is None:
                if len(self._sessions) >= MAX_SESSIONS:
                    raise BusinessError("mcp_capacity", "Active MCP sessions reached their bound", status=503)
                session = self._factory(target)
                self._sessions[key] = session
                try:
                    handshake = await session.start()
                except BaseException:
                    self._sessions.pop(key, None)
                    await session.close(5)
                    raise
                self._handshakes[key] = dict(handshake)
            return {**self._handshakes.get(key, {}), **session.health()}

    def _require(self, target: ServerTarget) -> MCPSessionPort:
        session = self._sessions.get(self._key(target))
        if session is None:
            raise BusinessError(
                "mcp_not_connected", "This MCP server is not connected; connect it explicitly first", status=503
            )
        return session

    async def catalog(self, target: ServerTarget) -> list[dict[str, Any]]:
        if self._closing:
            raise BusinessError("mcp_closing", "MCP sessions are draining", status=503)
        return await self._require(target).catalog()

    async def call(
        self,
        target: ServerTarget,
        name: str,
        arguments: dict[str, Any],
        timeout_s: float,
        *,
        before_send: Callable[[], Awaitable[None]] | None = None,
    ) -> dict[str, Any]:
        if self._closing:
            raise BusinessError("mcp_closing", "MCP sessions are draining", status=503)
        if self._pending >= MAX_PENDING:
            raise BusinessError("mcp_busy", "MCP invocation queue is full", retryable=True, status=503)
        self._pending += 1
        try:
            return await self._require(target).call(name, arguments, timeout_s, before_send=before_send)
        finally:
            self._pending -= 1

    async def oauth_start(self, target: ServerTarget, redirect_uri: str) -> dict[str, Any]:
        if self._oauth is None:
            raise BusinessError("mcp_oauth_unavailable", "OAuth support is not configured", status=503)
        return await self._oauth.start(target, redirect_uri)

    async def oauth_callback(self, state: str, code: str) -> dict[str, Any]:
        if self._oauth is None:
            raise BusinessError("mcp_oauth_unavailable", "OAuth support is not configured", status=503)
        return await self._oauth.callback(state, code)

    async def stop(self, agent_id: str, server_id: str, timeout_s: float = 5) -> bool:
        async with self._lock:
            complete = True
            for key in self._server_keys(agent_id, server_id):
                session = self._sessions[key]
                if await session.close(timeout_s):
                    self._sessions.pop(key, None)
                    self._handshakes.pop(key, None)
                else:
                    complete = False
            return complete

    def health(self) -> dict[str, Any]:
        return {
            "closing": self._closing,
            "resident": len(self._sessions),
            "pending": self._pending,
            "max_sessions": MAX_SESSIONS,
            "max_pending": MAX_PENDING,
            "oauth": self._oauth.health() if self._oauth is not None else {"available": False},
            "sessions": [
                {
                    "agent_id": key[0],
                    "server_id": key[1],
                    "authority_epoch": key[2],
                    **session.health(),
                }
                for key, session in self._sessions.items()
            ],
        }

    def stop_accepting(self) -> None:
        self._closing = True

    async def close(self, timeout_s: float = 30) -> bool:
        self.stop_accepting()
        deadline = asyncio.get_running_loop().time() + max(0, timeout_s)
        async with self._lock:
            complete = True
            for key, session in list(self._sessions.items()):
                remaining = max(0, deadline - asyncio.get_running_loop().time())
                if await session.close(remaining):
                    self._sessions.pop(key, None)
                    self._handshakes.pop(key, None)
                else:
                    complete = False
            return complete


__all__ = ["MCPHosts", "MCPSessionPort"]
