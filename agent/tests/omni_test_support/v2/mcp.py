"""Reusable mcp fixtures, extracted without collecting test cases."""

import asyncio
import hashlib
import json
import sys
import uuid

import pytest

from omni.adapters.mcp.hosts import MCPHosts
from omni.adapters.mcp.http import HttpSession
from omni.adapters.mcp.stdio import StdioSession
from omni.domain.mcp import (
    MAX_TEXT_CHARS,
    MCPDispatchError,
    MCPUncertain,
    ServerTarget,
    catalog_fingerprint,
    catalog_rows,
    exposed_name,
    result_text,
    tool_fingerprint,
)
from omni_test_support.v2.v2_mcp_helpers import TOOLS, FixtureHTTP, stdio_server

SCHEMA = {"type": "object", "properties": {"q": {"type": "string"}}, "additionalProperties": False}


def target(**overrides):
    values = {
        "agent_id": str(uuid.uuid4()),
        "server_id": str(uuid.uuid4()),
        "name": "notes",
        "transport": "stdio",
        "command": sys.executable,
        "arguments": (),
        "endpoint": "",
        "auth_mode": "none",
        "credential_ref": "",
        "authority_epoch": 1,
        "config_revision_id": str(uuid.uuid4()),
    }
    values.update(overrides)
    return ServerTarget(**values)


__all__ = [
    "FixtureHTTP",
    "HttpSession",
    "MAX_TEXT_CHARS",
    "MCPDispatchError",
    "MCPHosts",
    "MCPUncertain",
    "SCHEMA",
    "ServerTarget",
    "StdioSession",
    "TOOLS",
    "asyncio",
    "catalog_fingerprint",
    "catalog_rows",
    "exposed_name",
    "hashlib",
    "json",
    "pytest",
    "result_text",
    "stdio_server",
    "sys",
    "target",
    "tool_fingerprint",
    "uuid",
]
