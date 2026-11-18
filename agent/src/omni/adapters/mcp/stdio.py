"""Bounded JSON-RPC over one owned stdio process per MCP server generation."""

from __future__ import annotations

import asyncio
import contextvars
import json
import logging
import os
from collections.abc import Awaitable, Callable
from typing import Any, cast

from omni.adapters.platform.runtime.processes import OwnedProcess

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

ENV_ALLOW = (
    "PATH",
    "SystemRoot",
    "WINDIR",
    "HOME",
    "USERPROFILE",
    "TMP",
    "TEMP",
    "LANG",
    "APPDATA",
    "LOCALAPPDATA",
)


def bounded_payload(value: object, *, maximum: int = MAX_FRAME_BYTES) -> Packet:
    if not isinstance(value, dict):
        raise MCPUncertain("mcp_protocol_error", "MCP response was not a JSON object")
    try:
        encoded = json.dumps(value, ensure_ascii=False, allow_nan=False).encode("utf-8")
    except (TypeError, ValueError, UnicodeError, RecursionError) as exc:
        raise MCPUncertain("mcp_protocol_error", "MCP response was not bounded JSON") from exc
    if len(encoded) > maximum:
        raise MCPUncertain("mcp_response_too_large", "MCP response exceeded the transport bound")
    return cast(Packet, value)


class StdioSession:
    """One MCP server process; requests are serial and a lost response is always uncertain."""

    kind = "stdio"

    def __init__(self, target: ServerTarget, *, cwd: str) -> None:
        self.target, self._cwd = target, cwd
        self._owned: OwnedProcess | None = None
        self._stderr: asyncio.Task[None] | None = None
        self._lock = asyncio.Lock()
        self._next_id = 1
        self._closed = False
        self._cleanup_pending = False
        self._stderr_bytes = 0
        self._stderr_reports = 0
        self._handshake: Packet = {}
        self._poisoned = False

    # ---------- lifecycle ----------

    def _env(self) -> dict[str, str]:
        env = {name: os.environ[name] for name in ENV_ALLOW if name in os.environ}
        # Offline guards must reach the child; model/service credentials never do.
        for name in ("OMNI_TEST_OFFLINE", "OMNI_TEST_VIOLATIONS", "LITELLM_LOCAL_MODEL_COST_MAP"):
            if name in os.environ:
                env[name] = os.environ[name]
        if os.environ.get("OMNI_TEST_OFFLINE") == "1":
            env["PYTHONPATH"] = os.pathsep.join(
                path for path in os.environ.get("PYTHONPATH", "").split(os.pathsep) if path and os.path.isabs(path)
            )
        env["PYTHONUTF8"] = "1"
        env["PYTHONDONTWRITEBYTECODE"] = "1"
        return env

    async def _drain(self, stream: asyncio.StreamReader) -> None:
        last = -1.0
        loop = asyncio.get_running_loop()
        while chunk := await stream.read(4096):
            self._stderr_bytes += len(chunk)
            if loop.time() - last >= 1:
                last = loop.time()
                self._stderr_reports += 1
                # Never store external text; metadata only.
                log.warning(
                    "MCP server stderr bytes observed: %d",
                    self._stderr_bytes,
                    extra={"event": "mcp.stderr", "server": self.target.server_id},
                )

    async def _spawn(self) -> OwnedProcess:
        argv = [self.target.command, *self.target.arguments]
        owned = await OwnedProcess.spawn(
            argv,
            stdin=asyncio.subprocess.PIPE,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
            limit=MAX_FRAME_BYTES + 1,
            env=self._env(),
            cwd=self._cwd,
        )
        assert owned.process.stderr is not None
        self._owned = owned
        self._stderr = asyncio.create_task(
            self._drain(owned.process.stderr), name="mcp-stderr", context=contextvars.Context()
        )
        return owned

    async def start(self) -> Packet:
        if self._closed:
            raise MCPDispatchError("mcp_not_connected", "MCP session is closed")
        if self._poisoned and not await self._reclaim(5):
            raise MCPDispatchError("mcp_cleanup_pending", "Previous MCP process cleanup is pending")
        if self._owned is None or self._owned.process.returncode is not None or self._owned.reclaimed:
            await self._spawn()
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
        self._handshake = {
            "protocol_version": result.get("protocolVersion") or PROTOCOL_VERSION,
            "server_name": cast(Packet, result.get("serverInfo") or {}).get("name", "")
            if isinstance(result.get("serverInfo"), dict)
            else "",
            "server_version": cast(Packet, result.get("serverInfo") or {}).get("version", "")
            if isinstance(result.get("serverInfo"), dict)
            else "",
        }
        await self._notify("notifications/initialized", {})
        return self._handshake

    async def _reclaim(self, timeout_s: float) -> bool:
        deadline = asyncio.get_running_loop().time() + max(0, timeout_s)
        owned = self._owned
        if owned is not None:
            if owned.process.stdin is not None:
                owned.process.stdin.close()
            if not await owned.terminate(max(0, deadline - asyncio.get_running_loop().time())):
                self._poisoned = True
                self._cleanup_pending = True
                return False
        if self._stderr is not None:
            self._stderr.cancel()
            await asyncio.gather(self._stderr, return_exceptions=True)
        self._owned = self._stderr = None
        self._poisoned = self._cleanup_pending = False
        return True

    async def close(self, timeout_s: float = 5) -> bool:
        self._closed = True
        return await self._reclaim(timeout_s)

    def health(self) -> Packet:
        process = self._owned.process if self._owned is not None else None
        return {
            "transport": self.kind,
            "running": process is not None and process.returncode is None,
            "pid": process.pid if process is not None else None,
            "cleanup_pending": self._cleanup_pending,
            "stderr_bytes": self._stderr_bytes,
            "stderr_reports": self._stderr_reports,
            "closed": self._closed,
            "handshake": self._handshake,
        }

    # ---------- JSON-RPC ----------

    async def _notify(self, method: str, params: Packet) -> None:
        frame = json.dumps({"jsonrpc": "2.0", "method": method, "params": params}, ensure_ascii=False).encode() + b"\n"
        if len(frame) > MAX_FRAME_BYTES:
            raise MCPDispatchError("mcp_request_too_large", "MCP notification exceeded the frame bound")
        owned = self._owned
        if owned is None or owned.process.stdin is None:
            raise MCPDispatchError("mcp_not_connected", "MCP session is not running")
        owned.process.stdin.write(frame)
        await owned.process.stdin.drain()

    async def _request(self, method: str, params: Packet, timeout_s: float, *, uncertain_on_loss: bool) -> Packet:
        async with self._lock:
            owned = self._owned
            if owned is None or owned.reclaimed or owned.process.returncode is not None:
                raise MCPDispatchError("mcp_not_connected", "MCP server is not running")
            process = owned.process
            assert process.stdin is not None and process.stdout is not None
            identity = self._next_id
            self._next_id += 1
            frame = (
                json.dumps(
                    {"jsonrpc": "2.0", "id": identity, "method": method, "params": params}, ensure_ascii=False
                ).encode()
                + b"\n"
            )
            if len(frame) > MAX_FRAME_BYTES:
                raise MCPDispatchError("mcp_request_too_large", "MCP request exceeded the frame bound")
            sent = False
            try:
                process.stdin.write(frame)
                # write() may already hand bytes to the pipe. drain() only
                # waits for flow control and cannot prove no external delivery.
                sent = True
                await process.stdin.drain()
                line = await asyncio.wait_for(process.stdout.readline(), timeout=timeout_s)
            except (BrokenPipeError, ConnectionResetError) as exc:
                await self._reclaim(5)
                if sent and uncertain_on_loss:
                    raise MCPUncertain(
                        "mcp_connection_lost", "MCP process disconnected after the request was queued"
                    ) from exc
                raise MCPDispatchError("mcp_connection_lost", "MCP process disconnected") from exc
            except TimeoutError as exc:
                await self._reclaim(5)
                if sent and uncertain_on_loss:
                    raise MCPUncertain(
                        "mcp_call_timeout", "The MCP request exceeded its time budget; the external effect is unknown"
                    ) from exc
                raise MCPDispatchError(
                    "mcp_call_timeout" if method == "tools/call" else "mcp_handshake_timeout",
                    "The MCP request exceeded its time budget",
                    sent=False,
                ) from exc
            except asyncio.CancelledError:
                await self._reclaim(5)
                if sent and uncertain_on_loss:
                    raise MCPUncertain("mcp_cancelled", "The MCP request was cancelled after it was sent") from None
                raise
            except (ValueError, asyncio.LimitOverrunError) as exc:
                await self._reclaim(5)
                if sent and uncertain_on_loss:
                    raise MCPUncertain("mcp_protocol_error", "MCP response exceeded the protocol bound") from exc
                raise MCPDispatchError("mcp_protocol_error", "MCP response exceeded the protocol bound") from exc
            if not line:
                await self._reclaim(5)
                if sent and uncertain_on_loss:
                    raise MCPUncertain("mcp_connection_lost", "MCP process exited after the request was sent")
                raise MCPDispatchError("mcp_connection_lost", "MCP process exited")
            try:
                response: Packet = json.loads(line)
            except (ValueError, UnicodeError, RecursionError) as exc:
                await self._reclaim(5)
                if sent and uncertain_on_loss:
                    raise MCPUncertain("mcp_protocol_error", "MCP response was not valid JSON") from exc
                raise MCPDispatchError("mcp_protocol_error", "MCP response was not valid JSON") from exc
            if (
                not isinstance(cast(object, response), dict)
                or response.get("id") != identity
                or response.get("jsonrpc") != "2.0"
            ):
                await self._reclaim(5)
                if sent and uncertain_on_loss:
                    raise MCPUncertain("mcp_protocol_error", "MCP response identity did not match its request")
                raise MCPDispatchError("mcp_protocol_error", "MCP response identity did not match its request")
            if "error" in response:
                error = response["error"]
                code = str(cast(Packet, error).get("code", ""))[:40] if isinstance(error, dict) else ""
                message = str(cast(Packet, error).get("message", ""))[:200] if isinstance(error, dict) else ""
                raise MCPDispatchError(
                    "mcp_server_error", f"MCP server rejected the request ({code}) {message}".strip()
                )
            result = response.get("result")
            return bounded_payload(cast(Packet, result) if isinstance(result, dict) else {"value": result})

    # ---------- MCP operations ----------

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


__all__ = ["StdioSession"]
