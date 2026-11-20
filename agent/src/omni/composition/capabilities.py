"""Explicit builtin capability registration; capabilities never construct the kernel."""

from __future__ import annotations

from typing import Any

from ..adapters.workers.client import WorkerClient
from ..application.documents import DocumentService
from ..application.engine import Capability
from ..application.memory_semantics import MemorySemanticService
from ..application.memory_tools import MemoryCapabilities
from ..application.ports import DatabasePort
from ..application.session_search import SessionSearchCapabilities
from ..domain.contracts import ToolSpec


def parameters(properties: dict[str, Any], required: list[str] | None = None) -> dict[str, Any]:
    return {"type": "object", "properties": properties, "required": required or [], "additionalProperties": False}


def builtin_capabilities(
    database: DatabasePort, documents: DocumentService, semantics: MemorySemanticService | None = None
) -> tuple[Capability, ...]:
    worker = "builtin"
    browser = "browser"
    memory = MemoryCapabilities(database, semantics)
    sessions = SessionSearchCapabilities(database)
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
            "files_search",
            "Search the workspace: target=content (default) does bounded case-insensitive substring search, or use_regex=true treats query as a case-sensitive regular expression (conservative safety gate rejects nested quantifiers, backreferences and other super-linear constructs); output_mode=content (default) returns relative paths/line numbers/snippets, files_with_matches returns distinct matching paths, count returns per-file matching-line totals; context=0-3 attaches bounded before/after neighbor lines to content matches; offset=0-1000 skips the first N matches for pagination (50-result page); order=discovery (default) keeps bounded traversal order, order=modified sorts newest-first by file mtime across the bounded scan (stable ties keep discovery order); file_glob restricts content search to files whose workspace-relative path matches a case-sensitive glob (e.g. '*.py', 'src/*.py'), ignored for target=files; file_glob_exclude drops content-search files whose workspace-relative path matches any of up to 8 case-sensitive globs (a string or list of strings, e.g. '*.min.js' or ['node_modules/*', '*.lock']); a leading '!' is stripped, exclusion is applied after file_glob and ignored for target=files; use_regex is ignored for target=files; target=files finds files by case-sensitive basename glob (e.g. '*.py')",
            {
                "query": {"type": "string", "minLength": 1, "maxLength": 200},
                "path": {"type": "string"},
                "target": {"type": "string", "enum": ["content", "files"]},
                "output_mode": {"type": "string", "enum": ["content", "files_with_matches", "count"]},
                "context": {"type": "integer", "minimum": 0, "maximum": 3},
                "offset": {"type": "integer", "minimum": 0, "maximum": 1000},
                "order": {"type": "string", "enum": ["discovery", "modified"]},
                "file_glob": {"type": "string", "minLength": 1, "maxLength": 200},
                "file_glob_exclude": {
                    "type": ["string", "array"],
                    "items": {"type": "string", "minLength": 1, "maxLength": 200},
                    "maxItems": 8,
                },
                "use_regex": {"type": "boolean"},
            },
            ["query"],
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
            "files_patch",
            "Edit a workspace file by unique fuzzy search-replace (ordered strategies: exact, line-trimmed, whitespace-normalized, escape-normalized, unicode-normalized, then approximate block-anchor/context-aware last resort); verified escape-drift auto-correction (spurious quote escapes / doubled backslashes proven against the file) runs before matching, post-match guards reject replacement-side serialization artifacts, and literal \\t/\\r in new_string convert only when the matched region has the real control char; the result note reports corrections and approximate-boundary warnings; requires its current fingerprint; ambiguous matches rejected unless replace_all (approximate matches never allow replace_all)",
            {
                "path": {"type": "string"},
                "old_string": {"type": "string", "minLength": 1},
                "new_string": {"type": "string"},
                "expected_sha256": {"type": "string"},
                "replace_all": {"type": "boolean"},
            },
            ["path", "old_string", "new_string", "expected_sha256"],
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
            "browser_dialog",
            "Accept or dismiss a native JS dialog (alert/confirm/prompt/beforeunload) held pending on the approved page; browser_read lists pending dialogs; prompt_text answers prompt() only (ignored otherwise); dialog_id is required when multiple dialogs are pending",
            {
                "action": {"type": "string", "enum": ["accept", "dismiss"]},
                "prompt_text": {"type": "string", "maxLength": 2000},
                "dialog_id": {"type": "string"},
                "expected_url": {"type": "string"},
            },
            ["action", "expected_url"],
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
            "Save memory with an exact quote from a message, observed tool action, or observed document revision; "
            "optional importance 1-100 overrides automatic scoring; when omitted, channel-sourced captures are "
            "auto-scored from speaker context and content (otherwise default 50), steering later decay",
            {
                "content": {"type": "string", "minLength": 1, "maxLength": 20000},
                "message_id": {"type": "string"},
                "action_id": {"type": "string"},
                "document_revision_id": {"type": "string"},
                "quote": {"type": "string", "minLength": 1, "maxLength": 20000},
                "importance": {"type": "integer", "minimum": 1, "maximum": 100},
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
        (
            "session_search",
            "Search or read this Agent's past conversations (not memory). query=discover matching sessions; "
            "session_id+around_seq=scroll a window; session_id alone=read head/tail; no args=browse recent. "
            "Returns original message anchors; erased history is excluded.",
            {
                "query": {"type": "string", "maxLength": 500},
                "session_id": {"type": "string", "maxLength": 64},
                "around_seq": {"type": "integer", "minimum": 1},
                "window": {"type": "integer", "minimum": 1, "maximum": 20},
                "limit": {"type": "integer", "minimum": 1, "maximum": 10},
                "head": {"type": "integer", "minimum": 0, "maximum": 50},
                "tail": {"type": "integer", "minimum": 0, "maximum": 50},
                "role_filter": {"type": "string"},
                "exclude_session_ids": {"type": "array", "items": {"type": "string"}, "maxItems": 20},
            },
            [],
            False,
            sessions,
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
