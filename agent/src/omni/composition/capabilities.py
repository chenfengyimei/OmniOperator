"""Explicit builtin capability registration; capabilities never construct the kernel."""

from __future__ import annotations

from ..adapters.workers.client import WorkerClient
from ..application.engine import Capability
from ..application.memory_tools import MemoryCapabilities
from ..application.ports import DatabasePort
from ..domain.contracts import ToolSpec


def parameters(properties: dict, required: list[str] | None = None) -> dict:
    return {"type": "object", "properties": properties, "required": required or [], "additionalProperties": False}


def builtin_capabilities(database: DatabasePort) -> tuple[Capability, ...]:
    worker = WorkerClient("builtin")
    browser = WorkerClient("browser")
    memory = MemoryCapabilities(database)
    specs = [
        ("system_info", "Read operating system and runtime versions", {}, [], False, worker, ""),
        (
            "files_list",
            "List up to 200 workspace entries",
            {"path": {"type": "string"}},
            [],
            False,
            worker,
            "workspace",
        ),
        (
            "files_read",
            "Read a bounded UTF-8 workspace file and fingerprint",
            {"path": {"type": "string"}},
            ["path"],
            False,
            worker,
            "workspace",
        ),
        (
            "files_write",
            "Save a workspace file; overwrites require its current fingerprint",
            {"path": {"type": "string"}, "content": {"type": "string"}, "expected_sha256": {"type": "string"}},
            ["path", "content"],
            True,
            worker,
            "workspace",
        ),
        (
            "shell_execute",
            "Execute an approved argv in the workspace",
            {"argv": {"type": "array", "items": {"type": "string"}}, "cwd": {"type": "string"}},
            ["argv"],
            True,
            worker,
            "workspace",
        ),
        (
            "browser_navigate",
            "Navigate a dedicated agent browser to an HTTP(S) URL",
            {"url": {"type": "string"}},
            ["url"],
            True,
            browser,
            "browser",
        ),
        ("browser_read", "Read the current browser page", {}, [], False, browser, "browser"),
        (
            "browser_click",
            "Click a unique selector on the approved page",
            {"selector": {"type": "string"}, "expected_url": {"type": "string"}},
            ["selector", "expected_url"],
            True,
            browser,
            "browser",
        ),
        (
            "browser_fill",
            "Fill a unique field on the approved page",
            {"selector": {"type": "string"}, "text": {"type": "string"}, "expected_url": {"type": "string"}},
            ["selector", "text", "expected_url"],
            True,
            browser,
            "browser",
        ),
        (
            "memory_search",
            "Search live memory in this Agent space",
            {"query": {"type": "string"}},
            ["query"],
            False,
            memory,
            "",
        ),
        (
            "memory_remember",
            "Save memory with an exact quote from a source message",
            {"content": {"type": "string"}, "message_id": {"type": "string"}, "quote": {"type": "string"}},
            ["content", "message_id", "quote"],
            True,
            memory,
            "",
        ),
        (
            "memory_forget",
            "Forget one memory entry by its exact identifier",
            {"memory_id": {"type": "string"}},
            ["memory_id"],
            True,
            memory,
            "",
        ),
    ]
    return tuple(
        Capability(ToolSpec(name, description, parameters(props, required), writes, resource=resource), adapter)
        for name, description, props, required, writes, adapter, resource in specs
    )
