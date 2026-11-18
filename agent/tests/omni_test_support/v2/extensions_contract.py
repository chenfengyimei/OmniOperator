"""Reusable extensions_contract fixtures, extracted without collecting test cases."""

from __future__ import annotations

import hashlib
import json
from copy import deepcopy
from uuid import UUID

import pytest

from omni.domain.contracts import BusinessError, ToolSpec
from omni.domain.extensions import (
    FORMAT,
    MAX_MANIFEST_BYTES,
    MAX_SCHEMA_BYTES,
    PROTOCOL,
    CapturedPackage,
    catalog,
    catalog_fingerprint,
    page,
    parse_authorization,
    parse_install,
    parse_manifest,
    positive_revision,
    request_fingerprint,
    tool_spec,
)
from omni.domain.history import MAX_INTEGER


def identity(number):
    return str(UUID(int=number))


def encoded(value):
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"), allow_nan=False).encode()


def manifest(count=1):
    return {
        "format": FORMAT,
        "id": "calculator",
        "version": "1.0",
        "entry": "plugin.py",
        "tools": [
            {
                "name": "tool" + str(index),
                "description": "Explicitly approved computation",
                "parameters": {"type": "object", "properties": {}, "additionalProperties": False},
                "writes": True,
                "timeout_s": 30,
            }
            for index in range(count)
        ],
    }


def authorization(**changes):
    return {
        "revision": 1,
        "package_sha256": "a" * 64,
        "catalog_sha256": "b" * 64,
        "history_epoch": identity(1),
        "tools": ["ext__calculator__tool0"],
        "trust": True,
        **changes,
    }


def invalid(call):
    with pytest.raises(BusinessError) as failure:
        call()
    assert (failure.value.code, failure.value.status, failure.value.retryable) == ("extension_invalid", 422, False)
    assert "private-canary" not in str(failure.value)


def chinese_schema(size=MAX_SCHEMA_BYTES):
    value = {"type": "object", "properties": {"label": {"type": "string", "enum": [""]}}, "additionalProperties": False}
    available = size - len(encoded(value))
    value["properties"]["label"]["enum"][0] = "记" * (available // 3) + "x" * (available % 3)
    assert len(encoded(value)) == size
    return value


def chinese_manifest():
    value = manifest(5)
    for tool in value["tools"]:
        tool["parameters"] = {
            "type": "object",
            "properties": {"label": {"type": "string", "enum": [""]}},
            "additionalProperties": False,
        }
    for tool in value["tools"]:
        available = min(MAX_SCHEMA_BYTES - len(encoded(tool["parameters"])), MAX_MANIFEST_BYTES - len(encoded(value)))
        tool["parameters"]["properties"]["label"]["enum"][0] = "记" * (available // 3) + "x" * (available % 3)
    assert len(encoded(value)) == MAX_MANIFEST_BYTES
    assert all(len(encoded(tool["parameters"])) <= MAX_SCHEMA_BYTES for tool in value["tools"])
    return value


__all__ = [
    "BusinessError",
    "CapturedPackage",
    "FORMAT",
    "MAX_INTEGER",
    "MAX_MANIFEST_BYTES",
    "MAX_SCHEMA_BYTES",
    "PROTOCOL",
    "ToolSpec",
    "UUID",
    "authorization",
    "catalog",
    "catalog_fingerprint",
    "chinese_manifest",
    "chinese_schema",
    "deepcopy",
    "encoded",
    "hashlib",
    "identity",
    "invalid",
    "json",
    "manifest",
    "page",
    "parse_authorization",
    "parse_install",
    "parse_manifest",
    "positive_revision",
    "pytest",
    "request_fingerprint",
    "tool_spec",
]
