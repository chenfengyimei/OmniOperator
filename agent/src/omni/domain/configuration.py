"""Typed configuration payloads and fingerprints; contains no I/O or credential values."""

from __future__ import annotations

import hashlib
import json
import re
import unicodedata
from typing import Literal, cast
from urllib.parse import urlsplit

from pydantic import BaseModel, ConfigDict, Field, ValidationError, model_validator

from .contracts import BusinessError

KINDS = frozenset({"model", "channel", "mcp", "preset"})


def reference(value: str) -> str:
    if value and not re.fullmatch(r"[A-Za-z0-9_.:/-]{1,200}", value):
        raise ValueError("Invalid credential reference")
    return value


def endpoint(value: str, *, websocket: bool = False) -> str:
    if not value:
        return value
    url = urlsplit(value)
    schemes = {"http", "https", "ws", "wss"} if websocket else {"http", "https"}
    if (
        any(c.isspace() or ord(c) < 32 for c in value)
        or "\\" in value
        or (url.port is not None and not 1 <= url.port <= 65535)
        or url.scheme not in schemes
        or not url.hostname
        or url.username
        or url.password
        or url.query
        or url.fragment
        or (url.scheme in {"http", "ws"} and url.hostname not in {"127.0.0.1", "localhost", "::1"})
    ):
        raise ValueError("Endpoint requires TLS except on loopback and cannot contain credentials")
    return value


class Payload(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)


class ModelOptions(Payload):
    model: str = Field(default="", max_length=200)
    base_url: str = Field(default="", max_length=2000)
    credential_ref: str = Field(default="", max_length=200)
    context_tokens: int = Field(default=32768, ge=1024, le=2_000_000)
    output_tokens: int = Field(default=2048, ge=1, le=100_000)
    summary_tokens: int = Field(default=1024, ge=1, le=16000)
    margin_tokens: int = Field(default=1024, ge=128, le=16000)
    counter_mode: Literal["utf8_upper_bound", "required"] = "utf8_upper_bound"
    counter_ref: str = Field(default="utf8-envelope-v2", min_length=1, max_length=100)
    extra_headers: dict[str, str] = Field(default_factory=dict)

    @model_validator(mode="after")
    def valid(self):
        endpoint(self.base_url)
        reference(self.credential_ref)
        if max(self.output_tokens, self.summary_tokens) + self.margin_tokens >= self.context_tokens:
            raise ValueError("Input capacity must remain after output and margin reservations")
        if self.counter_mode == "utf8_upper_bound" and self.counter_ref not in {"utf8-envelope-v1", "utf8-envelope-v2"}:
            raise ValueError("The conservative counter has a fixed versioned identity")
        if len(self.extra_headers) > 20:
            raise ValueError("At most 20 custom headers are allowed")
        for name, value in self.extra_headers.items():
            if not re.fullmatch(r"[A-Za-z0-9-]{1,100}", name):
                raise ValueError("Custom header names must be token characters")
            if name.lower() in {"authorization", "x-api-key", "cookie", "host"}:
                raise ValueError("Custom headers cannot override authentication or routing headers")
            if len(value) > 2000 or any(ord(c) < 32 or ord(c) == 127 for c in value):
                raise ValueError("Custom header values must be printable and bounded")
        return self


class ChannelOptions(Payload):
    driver: Literal["qq", "bilibili", "android"] = "qq"
    endpoint: str = Field(default="", max_length=2000)
    account: str = Field(default="", max_length=200)
    credential_ref: str = Field(default="", max_length=200)

    @model_validator(mode="after")
    def valid(self):
        endpoint(self.endpoint, websocket=True)
        reference(self.credential_ref)
        return self


class MCPOptions(Payload):
    transport: Literal["stdio", "streamable_http"] = "stdio"
    command: str = Field(default="", max_length=4000)
    arguments: list[str] = Field(default_factory=list, max_length=32)
    endpoint: str = Field(default="", max_length=2000)
    auth_mode: Literal["none", "bearer", "oauth"] = "none"
    credential_ref: str = Field(default="", max_length=200)

    @model_validator(mode="after")
    def valid(self):
        reference(self.credential_ref)
        endpoint(self.endpoint)
        if self.transport == "stdio" and (self.endpoint or self.auth_mode != "none"):
            raise ValueError("stdio has no remote endpoint or remote authentication")
        if self.transport == "streamable_http" and (self.command or self.arguments or not self.endpoint):
            raise ValueError("Remote MCP requires an endpoint and cannot launch a process")
        if self.auth_mode == "bearer" and not self.credential_ref:
            raise ValueError("Bearer authentication requires a credential reference")
        for arg in self.arguments:
            if len(arg) > 1000 or re.search(
                r"^--?(?:api[-_]?key|token|access[-_]?token|refresh[-_]?token|password|cookie|authorization)(?:=|$)",
                arg,
                re.I,
            ):
                raise ValueError("Arguments must be bounded public values, not inline credentials")
        return self


class PresetOptions(Payload):
    content: str = Field(default="", max_length=50000)


PAYLOADS = {"model": ModelOptions, "channel": ChannelOptions, "mcp": MCPOptions, "preset": PresetOptions}


def config_value(kind: str, value: object) -> dict[str, object]:
    if kind not in PAYLOADS or not isinstance(value, dict):
        raise BusinessError("invalid_configuration", "A known configuration kind and object are required", status=422)
    try:
        result = PAYLOADS[kind].model_validate(value).model_dump()

        def has_nul(node: object) -> bool:
            if isinstance(node, str):
                return "\0" in node
            if isinstance(node, list):
                return any(has_nul(item) for item in cast(list[object], node))
            return isinstance(node, dict) and any(has_nul(item) for item in cast(dict[str, object], node).values())

        if has_nul(result):
            raise ValueError("NUL is not allowed")
        return result
    except (ValueError, ValidationError) as exc:
        raise BusinessError(
            "invalid_configuration",
            "Configuration fields or capacity bounds are invalid; credentials require references",
            status=422,
        ) from exc


def config_name(name: str) -> tuple[str, str]:
    cleaned = name.strip()
    if not cleaned or len(cleaned) > 100 or any(ord(c) < 32 for c in cleaned):
        raise BusinessError("invalid_configuration_name", "Name must contain 1 to 100 printable characters", status=422)
    return cleaned, unicodedata.normalize("NFKC", cleaned).casefold()


def fingerprint(kind: str, name: str, enabled: bool, value: dict[str, object]) -> str:
    return hashlib.sha256(
        json.dumps([kind, name, enabled, value], sort_keys=True, ensure_ascii=False, separators=(",", ":")).encode()
    ).hexdigest()
