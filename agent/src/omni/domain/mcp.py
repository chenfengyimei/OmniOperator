"""External MCP transport identity, catalog fingerprints and bounded model text; performs no I/O.

A configured MCP server is an ordinary configuration revision. This module only
decides what a bounded catalog row is, which fingerprint it has, when an owner
authorization belongs to the current catalog, and how an external result may be
shown to the model. Nothing here opens a socket, spawns a process or touches a
credential value.
"""

from __future__ import annotations

import hashlib
import json
import math
import re
import unicodedata
import uuid
from dataclasses import dataclass

from jsonschema import Draft202012Validator
from jsonschema.exceptions import SchemaError

from .configuration import MCPOptions
from .contracts import BusinessError, ToolSpec

TRANSPORTS = frozenset({"stdio", "streamable_http"})
AUTH_MODES = frozenset({"none", "bearer", "oauth"})
SERVER_STATES = frozenset({"idle", "starting", "ready", "failed", "unknown"})
SERVER_TRANSITIONS = {
    "idle": frozenset({"starting"}),
    "starting": frozenset({"ready", "failed", "unknown", "idle"}),
    "ready": frozenset({"idle", "failed", "unknown"}),
    "failed": frozenset({"starting", "idle"}),
    "unknown": frozenset({"starting", "idle"}),
}
CALL_STATES = frozenset({"prepared", "dispatched", "done", "failed", "denied", "unknown", "cancelled"})
CALL_TRANSITIONS = {
    "prepared": frozenset({"dispatched", "failed", "denied", "cancelled"}),
    "dispatched": frozenset({"done", "failed", "unknown", "cancelled"}),
}
TERMINAL_CALL_STATES = frozenset({"done", "failed", "denied", "unknown", "cancelled"})

MAX_TOOLS = 256
MAX_PAGES = 16
MAX_TEXT_CHARS = 12_000
MAX_DESCRIPTION_CHARS = 1024
MAX_SCHEMA_BYTES = 65_536
MAX_SCHEMA_DEPTH = 16
MAX_SCHEMA_NODES = 2_048
MAX_TOOL_NAME_CHARS = 64
MAX_TOOL_PARTS = 64
MAX_ARGUMENT_BYTES = 262_144
MAX_EVIDENCE_CHARS = 200
MAX_FRAME_BYTES = 1_048_576
DEFAULT_CALL_TIMEOUT_S = 60
HANDSHAKE_TIMEOUT_S = 20
CATALOG_BUDGET_S = 30
CALL_TIMEOUT_MIN_S = 1
CALL_TIMEOUT_MAX_S = 600
PROTOCOL_VERSION = "2025-06-18"
CLIENT_NAME = "OmniOperator"
OAUTH_TTL_S = 120
OAUTH_REFERENCE = "mcp.oauth.{server_id}"
TOOL_NAME = re.compile(r"[A-Za-z0-9_-]{1,64}")
SERVER_NAME = re.compile(r"[a-z0-9_-]{1,20}")


def _invalid(code: str, message: str, *, status: int = 422) -> BusinessError:
    return BusinessError(code, message, status=status)


class MCPDispatchError(BusinessError):
    """A bounded MCP failure that records whether the request could have left us."""

    def __init__(self, code: str, message: str, *, sent: bool = False, status: int = 503) -> None:
        super().__init__(code, message, retryable=False, status=status)
        self.sent = sent


class MCPUncertain(MCPDispatchError):
    """The external request was observed leaving; its effect cannot be proven."""

    def __init__(self, code: str = "mcp_outcome_unknown", message: str = "") -> None:
        super().__init__(code, message or "The external MCP call may have executed", sent=True, status=504)


@dataclass(frozen=True)
class ServerTarget:
    agent_id: str
    server_id: str
    name: str
    transport: str
    command: str
    arguments: tuple[str, ...]
    endpoint: str
    auth_mode: str
    credential_ref: str
    authority_epoch: int
    config_revision_id: str


@dataclass(frozen=True)
class CatalogTool:
    name: str
    description: str
    input_schema: dict
    fingerprint: str


def _encoded(value: object) -> bytes:
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"), allow_nan=False).encode("utf-8")


def _text(value: object, maximum: int, *, empty: bool = False) -> str:
    if (
        not isinstance(value, str)
        or len(value) > maximum
        or (not empty and not value.strip())
        or "\0" in value
        or any(unicodedata.category(char) == "Cc" and char not in "\n\t" for char in value)
    ):
        raise ValueError
    value.encode("utf-8")
    return value


def _json_nodes(value: object, depth: int = 0, budget: list[int] | None = None) -> None:
    budget = [MAX_SCHEMA_NODES] if budget is None else budget
    budget[0] -= 1
    if budget[0] < 0 or depth > MAX_SCHEMA_DEPTH:
        raise ValueError
    if isinstance(value, dict):
        for key, item in value.items():
            _text(key, 200, empty=True)
            _json_nodes(item, depth + 1, budget)
    elif isinstance(value, list):
        for item in value:
            _json_nodes(item, depth + 1, budget)
    elif isinstance(value, str):
        _text(value, 4_000, empty=True)
    elif isinstance(value, float):
        if not math.isfinite(value):
            raise ValueError
    elif value is not None and type(value) not in {bool, int}:
        raise ValueError


def server_slug(name: str) -> str:
    normalized = unicodedata.normalize("NFKC", name).strip().casefold()
    slug = re.sub(r"[^a-z0-9]+", "_", normalized).strip("_")[:20]
    return (slug or "server") if SERVER_NAME.fullmatch(slug or "server") else "server"


def exposed_name(server_name: str, tool_name: str) -> str:
    """Stable model-visible name; the service slug prevents cross-server collisions."""
    identity = server_slug(server_name)
    if not TOOL_NAME.fullmatch(tool_name or ""):
        raise _invalid("mcp_tool_name_invalid", "MCP tool names must use 1-64 letters, digits, '_' or '-'")
    result = f"mcp__{identity}__{tool_name}"
    if len(result) > 100:
        raise _invalid("mcp_tool_name_invalid", "The prefixed MCP tool name exceeds the capability name bound")
    return result


def canonical_schema(value: object) -> dict:
    """A bounded, finite JSON Schema object; the exact value later validates arguments."""
    try:
        _json_nodes(value)
        if not isinstance(value, dict):
            raise ValueError
        if "type" in value and value["type"] != "object":
            raise ValueError
        schema = json.loads(_encoded(value))
        if not isinstance(schema, dict):
            raise ValueError
        Draft202012Validator.check_schema(schema)
        if len(_encoded(schema)) > MAX_SCHEMA_BYTES:
            raise ValueError
        return schema
    except (ValueError, TypeError, UnicodeError, RecursionError, OverflowError, SchemaError) as exc:
        raise _invalid("mcp_tool_schema_invalid", "MCP tool inputSchema is not a bounded JSON Schema object") from exc


def tool_fingerprint(name: str, input_schema: dict) -> str:
    """Fingerprint over the tool name and inputSchema only; descriptions never revoke access."""
    return hashlib.sha256(_encoded({"name": name, "inputSchema": input_schema})).hexdigest()


def catalog_fingerprint(tools: list[dict]) -> str:
    pairs = sorted((str(tool["name"]), str(tool["tool_fingerprint"])) for tool in tools)
    return hashlib.sha256(_encoded([[name, digest] for name, digest in pairs])).hexdigest()


def parse_tool(raw: object, server_name: str, position: int) -> CatalogTool:
    if not isinstance(raw, dict) or not isinstance(raw.get("name"), str):
        raise _invalid("mcp_catalog_invalid", "Every MCP catalog entry requires a textual name", status=502)
    name = raw["name"]
    try:
        exposed_name(server_name, name)
        if not 0 <= position < MAX_TOOLS:
            raise ValueError
        schema = canonical_schema(raw.get("inputSchema") or {"type": "object"})
        description = _text(raw.get("description") or "", MAX_DESCRIPTION_CHARS, empty=True)
        encoded = _encoded({"name": name, "description": description, "inputSchema": schema})
        if len(encoded) > MAX_SCHEMA_BYTES + 4_096:
            raise ValueError
    except ValueError as exc:
        raise _invalid(
            "mcp_catalog_invalid", "An MCP catalog entry is not a bounded tool definition", status=502
        ) from exc
    return CatalogTool(
        name=name, description=description, input_schema=schema, fingerprint=tool_fingerprint(name, schema)
    )


def catalog_rows(raw_tools: object, server_name: str) -> list[dict]:
    """Validated, de-duplicated catalog rows for one discovery snapshot."""
    if not isinstance(raw_tools, list):
        raise _invalid("mcp_catalog_invalid", "An MCP catalog must be a list of tools", status=502)
    if len(raw_tools) > MAX_TOOLS:
        raise _invalid("mcp_catalog_too_large", f"An MCP server may expose at most {MAX_TOOLS} tools", status=502)
    seen: set[str] = set()
    rows: list[dict] = []
    for position, raw in enumerate(raw_tools):
        tool = parse_tool(raw, server_name, position)
        if tool.name in seen:
            raise _invalid("mcp_catalog_duplicate", "The MCP server listed the same tool name twice", status=502)
        seen.add(tool.name)
        rows.append(
            {
                "name": tool.name,
                "description": tool.description,
                "input_schema": tool.input_schema,
                "tool_fingerprint": tool.fingerprint,
                "exposed_name": exposed_name(server_name, tool.name),
                "position": position,
            }
        )
    return rows


def server_target(record: dict) -> ServerTarget:
    """Build a transport target from a stored configuration row; values stay bounded."""
    try:
        if record["kind"] != "mcp":
            raise ValueError
        value = record["value"]
        if not isinstance(value, dict):
            raise ValueError
        options = MCPOptions.model_validate(value)
        if not record["enabled"]:
            raise ValueError
        epoch = record.get("authority_epoch", 0)
        if type(epoch) is not int or epoch < 0:
            raise ValueError
        return ServerTarget(
            agent_id=str(record["agent_id"]),
            server_id=str(record["id"]),
            name=_text(record["name"], 100),
            transport=options.transport,
            command=options.command,
            arguments=tuple(options.arguments),
            endpoint=options.endpoint,
            auth_mode=options.auth_mode,
            credential_ref=options.credential_ref,
            authority_epoch=epoch,
            config_revision_id=str(record["revision_id"]),
        )
    except (ValueError, KeyError, TypeError, AttributeError) as exc:
        raise _invalid(
            "mcp_configuration_invalid", "The MCP configuration is disabled or not a bounded transport", status=409
        ) from exc


def call_timeout(value: object = None) -> float:
    if value is None:
        return DEFAULT_CALL_TIMEOUT_S
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        return DEFAULT_CALL_TIMEOUT_S
    if not math.isfinite(value):
        return DEFAULT_CALL_TIMEOUT_S
    return min(CALL_TIMEOUT_MAX_S, max(CALL_TIMEOUT_MIN_S, float(value)))


def positive_revision(value: object) -> int:
    if type(value) is not int or value < 1:
        raise _invalid("mcp_revision_invalid", "A current configuration revision is required")
    return value


def page_bounds(after: str = "", limit: int = 20) -> tuple[str, int]:
    if not isinstance(after, str) or len(after) > 36:
        raise _invalid("mcp_page_invalid", "Invalid MCP page cursor")
    if type(limit) is not int or not 1 <= limit <= 100:
        raise _invalid("mcp_page_invalid", "MCP page limit must be between 1 and 100")
    return after, limit


def parse_tool_authorization(request: object, catalog: dict[str, dict], fingerprint: str) -> dict:
    """Owner-declared per-tool authorization; read-only must be an explicit flag."""
    if not isinstance(request, dict) or set(request) != {"catalog_fingerprint", "tools", "evidence"}:
        raise _invalid("mcp_authorization_invalid", "Authorization requires a fingerprint, tools and evidence")
    if request["catalog_fingerprint"] != fingerprint:
        raise _invalid(
            "mcp_catalog_changed", "The catalog fingerprint changed; inspect the current tools again", status=409
        )
    tools = request["tools"]
    if not isinstance(tools, list) or len(tools) > MAX_TOOLS:
        raise _invalid("mcp_authorization_invalid", "Authorization must list at most the discovered tools")
    try:
        evidence = _text(request["evidence"], MAX_EVIDENCE_CHARS)
    except ValueError as exc:
        raise _invalid("mcp_authorization_invalid", "Authorization needs bounded owner review evidence") from exc
    checked: dict[str, dict] = {}
    for item in tools:
        if not isinstance(item, dict) or set(item) != {"tool_id", "read_only"}:
            raise _invalid("mcp_authorization_invalid", "Each authorized tool needs an id and a read-only flag")
        tool_id = str(item["tool_id"])
        row = catalog.get(tool_id)
        if row is None:
            raise _invalid("mcp_authorization_invalid", "An authorized tool is not in the current catalog")
        if type(item["read_only"]) is not bool:
            raise _invalid("mcp_authorization_invalid", "read_only must be an explicit boolean")
        checked[tool_id] = {"tool_id": tool_id, "read_only": item["read_only"]}
    return {"catalog_fingerprint": fingerprint, "tools": checked, "evidence": evidence}


def result_text(server: str, tool: str, payload: dict) -> tuple[str, dict]:
    """External result text is untrusted data: bounded, attributed and never raw payload."""
    content = payload.get("content")
    is_error = payload.get("isError") is True
    parts: list[str] = []
    if isinstance(content, list):
        for item in content[:MAX_TOOL_PARTS]:
            if isinstance(item, dict) and item.get("type") == "text" and isinstance(item.get("text"), str):
                parts.append(item["text"])
            elif isinstance(item, dict):
                kind = str(item.get("type") or "unknown")[:40]
                parts.append(f"[omitted non-text MCP content: {kind}]")
            else:
                parts.append("[omitted non-text MCP content]")
    body = "\n".join(parts)
    header = f"mcp server={server} tool={tool} is_error={str(is_error).lower()}"
    available = max(0, MAX_TEXT_CHARS - len(header) - 1)
    truncated = len(body) > available
    body = body[:available]
    text = header + ("\n" + body if body else "")
    return text, {
        "server": server,
        "tool": tool,
        "is_error": is_error,
        "chars": len(text),
        "truncated": truncated,
    }


def call_state(payload: dict) -> str:
    return "failed" if payload.get("isError") is True else "done"


def oauth_reference(server_id: str) -> str:
    return OAUTH_REFERENCE.format(server_id=server_id)


def argument_digest(arguments: object) -> str:
    encoded = _encoded(arguments)
    if len(encoded) > MAX_ARGUMENT_BYTES:
        raise _invalid("invalid_tool_arguments", "MCP tool arguments exceed their JSON payload bound")
    return hashlib.sha256(encoded).hexdigest()


def tool_spec(record: dict) -> ToolSpec:
    """A run capture row becomes the model-visible capability, writes derived from the owner flag."""
    try:
        binding = str(record["binding_id"])
        read_only = record["read_only"]
        if type(read_only) is not bool or record["writes"] is not (not read_only):
            raise ValueError
        return ToolSpec(
            name=_text(record["name"], 100),
            description=_text(record["description"], MAX_DESCRIPTION_CHARS, empty=True),
            parameters=canonical_schema(record["input_schema"]),
            writes=not read_only,
            timeout_s=DEFAULT_CALL_TIMEOUT_S,
            resource="mcp:" + str(record["server_id"]),
            binding_id=uuid_of(binding),
        )
    except (ValueError, TypeError, KeyError, AttributeError) as exc:
        raise _invalid("mcp_capture_invalid", "The stored MCP run capture is not a bounded capability") from exc


def uuid_of(value: object) -> str:
    text = str(value)
    if len(text) != 36 or str(uuid.UUID(text)) != text:
        raise ValueError
    return text
