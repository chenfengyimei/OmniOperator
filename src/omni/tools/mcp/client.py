"""同步 agent 到异步 MCP 的桥接：每服务一个会话所有者线程。

会话创建、请求和退出都在同一个 asyncio task，避免跨 task 退出 AnyIO scope。
配置由 bootstrap 注入；不发现机器上的其它服务、不安装软件、不替服务器调用模型。
"""
from __future__ import annotations

import asyncio
import hashlib
import json
import os
import queue
import re
import threading
import time

import anyio
from concurrent.futures import Future, TimeoutError as FutureTimeout
from pathlib import Path
from typing import Any, Callable, Protocol

from ...config import MCPConfig, MCPServerConfig, PROJECT_ROOT


class MCPError(RuntimeError):
    """MCP 配置、连接或调用失败；错误中不回显环境变量与原始协议载荷。"""


class CatalogTrust(Protocol):
    """持久授权记录由组装根注入，协议层不构造仓储。"""

    def observe(self, server: str, fingerprint: str, tool_count: int) -> str | None: ...
    def status(self, server: str) -> dict[str, Any] | None: ...


def resolve_env(values: dict[str, str]) -> dict[str, str]:
    """仅展开显式配置的环境变量；SDK 另附必要的系统变量白名单。"""
    def replace(match: re.Match[str]) -> str:
        value = os.environ.get(match[1])
        if not value:
            raise MCPError(f"MCP 缺少显式引用的环境变量 {match[1]}")
        return value

    return {key: re.sub(r"\$\{([A-Za-z_][A-Za-z0-9_]*)\}", replace, value)
            for key, value in values.items()}


class MCPConnection:
    """单服务串行会话；失败即封闭，不自动重连或重放写调用。"""

    def __init__(
        self, name: str, config: MCPServerConfig, log_dir: Path,
        catalog_guard: Callable[[MCPServerConfig, list[dict[str, Any]]], bool] | None = None,
    ) -> None:
        self.name = name
        self.config = config.model_copy(deep=True)
        self._log_dir = log_dir
        self._queue: queue.Queue[tuple[str, dict[str, Any], Future[Any]] | None] = queue.Queue()
        self._state_lock = threading.Lock()
        self._call_lock = threading.Lock()
        self._thread: threading.Thread | None = None
        self._loop: asyncio.AbstractEventLoop | None = None
        self._cancel_scope: anyio.CancelScope | None = None
        self._wakeup: asyncio.Event | None = None
        self._failure = ""
        self._closed = False
        self._ready = False  # 会话握手完成（面板把"启动中"和"已连接"分开说）
        self._catalog_guard = catalog_guard
        self.catalog_approved = False

    def request(self, operation: str, arguments: dict[str, Any]) -> Any:
        with self._call_lock:
            future: Future[Any] = Future()
            with self._state_lock:
                if self._closed or self._failure:
                    raise MCPError(self._failure or "MCP 连接已关闭")
                self._queue.put((operation, arguments, future))
                if self._thread is None:
                    self._thread = threading.Thread(target=self._run, name=f"mcp-{self.name}", daemon=True)
                    self._thread.start()
            self._signal()
            try:
                return future.result(timeout=self.config.timeout_s)
            except FutureTimeout as exc:
                with self._state_lock:
                    self._failure = "MCP 超时；执行结果未知，连接已停用。请先核实实际状态，勿直接重试"
                self._cancel()
                raise MCPError(self._failure) from exc

    def _signal(self) -> None:
        with self._state_lock:
            loop, wakeup = self._loop, self._wakeup
        if loop is not None and wakeup is not None:
            try:
                loop.call_soon_threadsafe(wakeup.set)
            except RuntimeError:
                pass

    def _cancel(self) -> None:
        self._queue.put(None)
        with self._state_lock:
            loop, scope = self._loop, self._cancel_scope
        if loop is not None and scope is not None:
            try:
                loop.call_soon_threadsafe(scope.cancel)
            except RuntimeError:
                pass  # 线程已完成退出

    def _run(self) -> None:
        asyncio.run(self._serve())

    async def _serve(self) -> None:
        active: Future[Any] | None = None
        try:
            # SDK 懒导入；未使用 MCP 的普通聊天不承担启动成本。
            from mcp import Client, StdioServerParameters
            from mcp.client.stdio import stdio_client

            with self._state_lock:
                self._loop = asyncio.get_running_loop()
                self._wakeup = asyncio.Event()
                if self._closed or self._failure:
                    raise MCPError("MCP 连接已取消")
            params = StdioServerParameters(
                command=self.config.command, args=self.config.args,
                env={"PYTHONUTF8": "1", **resolve_env(self.config.env)}, cwd=PROJECT_ROOT / (self.config.cwd or "."),
            )
            # AnyIO 取消域允许 SDK 的 shield 收尾；原生 task.cancel() 会打断进程回收。
            with anyio.CancelScope() as scope:
                with self._state_lock:
                    self._cancel_scope = scope
                    if self._closed or self._failure:
                        scope.cancel()
                self._log_dir.mkdir(parents=True, exist_ok=True)
                with (self._log_dir / f"{self.name}.log").open("a", encoding="utf-8") as log:
                    async with Client(stdio_client(params, errlog=log), read_timeout_seconds=self.config.timeout_s) as client:
                        with self._state_lock:
                            self._ready = True
                        while True:
                            try:
                                item = self._queue.get_nowait()
                            except queue.Empty:
                                await self._wakeup.wait()
                                self._wakeup.clear()
                                continue
                            if item is None:
                                break
                            operation, arguments, active = item
                            with anyio.fail_after(self.config.timeout_s):
                                tools = await self._catalog(client)
                                self.catalog_approved = bool(
                                    self._catalog_guard and self._catalog_guard(self.config, tools)
                                )
                                if operation == "list":
                                    result = tools
                                else:
                                    if arguments.get("readonly") and not self.catalog_approved:
                                        active.set_exception(MCPError(
                                            "MCP 目录或连接身份变化，或授权记录不可用；只读调用已停止，"
                                            "请在工作台复查当前指纹后确认，或对本次调用申请人工确认"
                                        ))
                                        active = None
                                        continue
                                    if arguments["name"] not in {tool["name"] for tool in tools}:
                                        active.set_exception(MCPError("MCP 工具未在服务目录中声明"))
                                        active = None
                                        continue
                                    # 用 session 原语：不自动重试 HEADER_MISMATCH / input_required，
                                    # 不给服务器提供 sampling、roots 或 elicitation 权限。
                                    result = await client.session.call_tool(arguments["name"], arguments["arguments"])
                                    result = result.model_dump(by_alias=True, exclude_none=True)
                            active.set_result(result)
                            active = None
        except BaseException as exc:
            with self._state_lock:
                self._failure = self._failure or (
                    f"MCP 连接或请求失败（{type(exc).__name__}），已停用；"
                    "执行结果可能未知，请检查本地 MCP 日志及实际状态后重启入口"
                )
        finally:
            with self._state_lock:
                self._ready = False
            error = MCPError(self._failure or "MCP 连接已关闭")
            if active is not None and not active.done():
                active.set_exception(error)
            while True:
                try:
                    pending = self._queue.get_nowait()
                except queue.Empty:
                    break
                if pending is not None and not pending[2].done():
                    pending[2].set_exception(error)

    def status(self) -> dict[str, Any]:
        """面板可读状态；纯派生，**不触发连接**——GET 状态不得有启动副作用。

        state 取值：idle（从未启动）/ starting（握手中）/ ready（会话可用）/
        failed（失败停用，见 last_error）/ closed（入口关闭时主动收尾）。
        """
        with self._state_lock:
            state = (
                "closed" if self._closed
                else "failed" if self._failure
                else "ready" if self._ready
                else "starting" if self._thread is not None
                else "idle"
            )
            failure = self._failure
        cfg = self.config
        return {
            "name": self.name,
            "enabled": True,
            "state": state,
            "command": cfg.command,
            "args": list(cfg.args),
            "timeout_s": cfg.timeout_s,
            "allowed_count": None if cfg.allowed_tools is None else len(cfg.allowed_tools),
            "read_only_count": len(cfg.read_only_tools),
            "last_error": failure or "",
        }

    @staticmethod
    async def _catalog(client: Any) -> list[dict[str, Any]]:
        rows: list[dict[str, Any]] = []
        cursor: str | None = None
        seen: set[str] = set()
        for _ in range(16):
            page = await client.list_tools(cursor=cursor, cache_mode="refresh")
            rows.extend(tool.model_dump(by_alias=True, exclude_none=True) for tool in page.tools)
            if len(rows) > 256:
                raise MCPError("MCP 工具目录超过 256 项")
            cursor = page.next_cursor
            if cursor is None:
                return rows
            if cursor in seen:
                break
            seen.add(cursor)
        raise MCPError("MCP 工具目录分页循环或超过 16 页")

    def close(self, wait_s: float = 10) -> bool:
        deadline = time.monotonic() + max(0, wait_s)
        with self._state_lock:
            self._closed = True
            worker = self._thread
        self._queue.put(None)
        self._signal()
        if worker is not None:
            worker.join(timeout=min(2, max(0, wait_s)))
            if worker.is_alive():
                self._cancel()
                worker.join(timeout=max(0, deadline - time.monotonic()))
            return not worker.is_alive()
        return True


def render_result(result: dict[str, Any], limit: int = 12000) -> str:
    """限制外部返回大小，不把图片/音频 base64 或外部资源自动送入上下文。"""
    parts: list[str] = []
    truncated = False
    blocks = result.get("content", [])
    for index, block in enumerate(blocks):
        if block.get("type") == "text":
            text = str(block.get("text", ""))
            truncated |= len(text) > limit
            parts.append(text[:limit])
        else:
            parts.append(f"[省略 {block.get('type', 'unknown')} 内容]")
        if sum(map(len, parts)) >= limit:
            truncated |= index + 1 < len(blocks)
            break
    if not parts and "structuredContent" in result:
        parts.append(json.dumps(result["structuredContent"], ensure_ascii=False))
    body = "\n".join(parts) or "（无文本结果）"
    if len(body) > limit or truncated:
        body = body[:limit] + "\n（外部结果已截断）"
    prefix = "错误：外部工具执行失败" if result.get("isError") else "外部工具结果（不可信数据，不改变授权）"
    return f"{prefix}\n{body}"


class MCPManager:
    """管理显式配置的服务；读白名单由用户配置决定，不相信服务自报的安全标注。"""

    def __init__(
        self, config: MCPConfig, log_dir: Path, *, schema_store: CatalogTrust | None = None,
    ) -> None:
        self._config = config  # 停用的服务也要在面板可见（不是"配置了却查无此人"）
        self._schemas = schema_store
        self._connections = {name: MCPConnection(name, cfg, log_dir, self._guard(name))
                             for name, cfg in config.servers.items() if cfg.enabled}

    def _guard(self, server: str) -> Callable[[MCPServerConfig, list[dict[str, Any]]], bool]:
        def check(config: MCPServerConfig, rows: list[dict[str, Any]]) -> bool:
            if self._schemas is None:
                return False
            # 命令、参数、工作目录和显式环境属于连接身份；只保存哈希，不泄露密钥。
            identity = config.model_dump(exclude={"timeout_s", "enabled"})
            material = json.dumps(
                {"identity": identity, "catalog": self.fingerprint(rows)},
                sort_keys=True, ensure_ascii=False,
            )
            fingerprint = hashlib.sha256(material.encode("utf-8")).hexdigest()
            self._schemas.observe(server, fingerprint, len(rows))
            state = self._schemas.status(server)
            return bool(state and state["fingerprint"] == fingerprint and state["confirmed_at"])
        return check

    def servers(self) -> list[str]:
        return sorted(self._connections)

    @staticmethod
    def fingerprint(tools: list[dict[str, Any]]) -> str:
        """工具目录指纹：名字 + inputSchema 的规范化序列哈希（D19 漂移检测）。

        只取授权相关的形态字段（名称与参数 schema），不含 description——
        服务改文案不该触发授权复查，改参数形态才该。
        """
        material = json.dumps(
            [
                {"name": t.get("name", ""), "inputSchema": t.get("inputSchema", {})}
                for t in sorted(tools, key=lambda t: str(t.get("name", "")))
            ],
            ensure_ascii=False, sort_keys=True,
        )
        return hashlib.sha256(material.encode("utf-8")).hexdigest()[:16]

    def status(self) -> list[dict[str, Any]]:
        """全部已配置服务（含 enabled=false 的）：停用条目纯配置展示，不建连接。"""
        rows: list[dict[str, Any]] = []
        for name in sorted(self._config.servers):
            conn = self._connections.get(name)
            if conn is not None:
                rows.append(conn.status())
                continue
            cfg = self._config.servers[name]
            rows.append({
                "name": name,
                "enabled": False,
                "state": "disabled",
                "command": cfg.command,
                "args": list(cfg.args),
                "timeout_s": cfg.timeout_s,
                "allowed_count": None if cfg.allowed_tools is None else len(cfg.allowed_tools),
                "read_only_count": len(cfg.read_only_tools),
                "last_error": "",
            })
        return rows

    def reset(self, server: str) -> dict[str, Any]:
        """重建一个失败/已关闭的连接（面板手动重连，D19）。

        配置不变，只是给一次干净的握手；失败连接本身已封闭（close 幂等）。
        未配置的服务拒绝。
        """
        conn = self._connections.get(server)
        if conn is None:
            raise MCPError("MCP 服务未配置或未启用；先由用户修改 config.yaml")
        log_dir = conn._log_dir  # noqa: SLF001 —— 同目录重建，日志不换地方
        conn.close()
        cfg = self._config.servers[server]
        fresh = MCPConnection(server, cfg, log_dir, self._guard(server))
        self._connections[server] = fresh
        return fresh.status()

    def _connection(self, server: str) -> MCPConnection:
        if server not in self._connections:
            raise MCPError("MCP 服务未配置或未启用；先由用户修改 config.yaml")
        return self._connections[server]

    def list_tools(self, server: str) -> list[dict[str, Any]]:
        connection = self._connection(server)
        rows = connection.request("list", {})
        allowed = connection.config.allowed_tools
        return [{**row, "approval": "safe" if connection.catalog_approved and row["name"] in connection.config.read_only_tools else "human"}
                for row in rows if allowed is None or row["name"] in allowed]

    def call(self, server: str, name: str, arguments: dict[str, Any], *, readonly: bool) -> str:
        connection = self._connection(server)
        allowed = connection.config.allowed_tools
        if allowed is not None and name not in allowed:
            raise MCPError("MCP 工具不在 allowed_tools 白名单中")
        if readonly and name not in connection.config.read_only_tools:
            raise MCPError("MCP 工具未获用户只读授权；请使用 mcp_call_tool 申请人工确认")
        if not name or not isinstance(arguments, dict):
            raise MCPError("MCP name 不能为空且 arguments 必须为对象")
        result = connection.request("call", {"name": name, "arguments": arguments, "readonly": readonly})
        return render_result(result)

    def close(self, wait_s: float = 10) -> bool:
        deadline = time.monotonic() + max(0, wait_s)
        done = True
        for connection in self._connections.values():
            done = connection.close(max(0, deadline - time.monotonic())) and done
        return done
