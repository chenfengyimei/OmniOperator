"""Explicit builtin capability registration; capabilities never construct the kernel."""

from __future__ import annotations

from typing import Any

from ..adapters.workers.client import WorkerClient
from ..application.documents import DocumentService
from ..application.engine import Capability
from ..application.memory_semantics import MemorySemanticService
from ..application.memory_tools import MemoryCapabilities
from ..application.ports import DatabasePort
from ..domain.contracts import ToolSpec


def parameters(properties: dict[str, Any], required: list[str] | None = None) -> dict[str, Any]:
    return {"type": "object", "properties": properties, "required": required or [], "additionalProperties": False}


def builtin_capabilities(
    database: DatabasePort, documents: DocumentService, semantics: MemorySemanticService | None = None
) -> tuple[Capability, ...]:
    worker = "builtin"
    browser = "browser"
    memory = MemoryCapabilities(database, semantics)
    specs: list[tuple[str, str, dict[str, Any], list[str] | None, bool, Any, str]] = [
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
            "document_search",
            "Search source documents; returned exact revision and character ranges are registered before use",
            {"query": {"type": "string", "minLength": 1, "maxLength": 500}},
            ["query"],
            False,
            documents,
            "documents",
        ),
        (
            "document_read",
            "Read a bounded exact source revision; offsets count Unicode characters",
            {
                "document_id": {"type": "string"},
                "revision_id": {"type": "string"},
                "start": {"type": "integer", "minimum": 0},
                "length": {"type": "integer", "minimum": 1, "maximum": 4000},
            },
            ["document_id"],
            False,
            documents,
            "documents",
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
            "Save memory with an exact quote from a message, observed tool action, or observed document revision",
            {
                "content": {"type": "string", "minLength": 1, "maxLength": 20000},
                "message_id": {"type": "string"},
                "action_id": {"type": "string"},
                "document_revision_id": {"type": "string"},
                "quote": {"type": "string", "minLength": 1, "maxLength": 20000},
            },
            ["content", "quote"],
            True,
            memory,
            "",
        ),
        (
            "memory_forget",
            "Forget one memory entry by its exact identifier",
            {"memory_id": {"type": "string"}, "revision": {"type": "integer", "minimum": 1}},
            ["memory_id", "revision"],
            True,
            memory,
            "",
        ),
    ]
    registered = [
        (ToolSpec(name, description, parameters(props, required), writes, resource=resource), adapter)
        for name, description, props, required, writes, adapter, resource in specs
    ]
    workers = {
        kind: WorkerClient(
            kind, tool_writes={spec.name: spec.writes for spec, adapter in registered if adapter == kind}
        )
        for kind in (worker, browser)
    }
    return tuple(
        Capability(
            spec,
            workers[adapter] if isinstance(adapter, str) else adapter,
            workers[adapter].review_quiescent if isinstance(adapter, str) else lambda: True,
        )
        for spec, adapter in registered
    )
