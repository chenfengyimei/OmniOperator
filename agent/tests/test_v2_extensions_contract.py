"""Static plugin manifests, owner trust and immutable tool identities remain bounded and independent of execution."""

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


def test_manifest_and_catalog_are_copied_and_have_deterministic_explicit_identity():
    value = manifest(2)
    value["tools"].reverse()
    original = deepcopy(value)
    parsed = parse_manifest(value)
    rows = catalog(value)
    assert parsed == original and parsed is not value and value == original
    assert PROTOCOL == "omni-python-extension-ipc-1"
    assert [row["local_name"] for row in rows] == ["tool0", "tool1"]
    for row in rows:
        assert set(row) == {
            "local_name",
            "name",
            "description",
            "parameters",
            "writes",
            "timeout_s",
            "artifacts",
            "spec_sha256",
        }
        assert row["writes"] is True and row["name"] == "ext__calculator__" + row["local_name"]
        assert row["artifacts"] == []
        assert (
            row["spec_sha256"]
            == hashlib.sha256(encoded({key: item for key, item in row.items() if key != "spec_sha256"})).hexdigest()
        )
    assert catalog_fingerprint(value) == hashlib.sha256(encoded(rows)).hexdigest()
    value["tools"].reverse()
    assert catalog_fingerprint(value) == catalog_fingerprint(original)
    value["tools"][0]["parameters"]["properties"]["changed"] = {"type": "null"}
    assert rows[0]["parameters"]["properties"] == {} and catalog_fingerprint(value) != catalog_fingerprint(original)


def test_public_name_limit_is_64_without_truncation_or_aliases():
    value = manifest()
    value["id"], value["tools"][0]["name"] = "a" * 32, "b" * 25
    assert len(catalog(value)[0]["name"]) == 64
    value["tools"][0]["name"] += "b"
    invalid(lambda: parse_manifest(value))
    value = manifest(16)
    assert len(catalog(value)) == 16
    invalid(lambda: catalog(manifest(17)))
    invalid(lambda: catalog(manifest(0)))
    value["tools"][1]["name"] = value["tools"][0]["name"]
    invalid(lambda: catalog(value))


def test_utf8_canonical_bounds_accept_chinese_and_reject_exactly_one_extra_byte():
    value = manifest()
    value["tools"][0]["parameters"] = chinese_schema()
    assert parse_manifest(value) == value
    value["tools"][0]["parameters"]["properties"]["label"]["enum"][0] += "x"
    invalid(lambda: parse_manifest(value))
    value = chinese_manifest()
    assert parse_manifest(value) == value
    assert len(json.dumps(value).encode()) > MAX_MANIFEST_BYTES
    value["version"] += "x"
    invalid(lambda: parse_manifest(value))


@pytest.mark.parametrize(
    "field,bad",
    [("writes", False), ("writes", 1), ("timeout_s", True), ("timeout_s", 0), ("timeout_s", 301), ("timeout_s", 1.0)],
)
def test_tools_cannot_opt_out_of_human_approval_or_coerce_timeouts(field, bad):
    value = manifest()
    value["tools"][0][field] = bad
    invalid(lambda: parse_manifest(value))


def test_manifest_keys_names_entry_and_text_are_strict():
    for value in (None, [], "private-canary", {**manifest(), "dependencies": []}):
        invalid(lambda: parse_manifest(value))
    for field in manifest():
        value = manifest()
        del value[field]
        invalid(lambda: parse_manifest(value))
    for field, bad in (
        ("format", "1"),
        ("id", "Calculator"),
        ("id", "../private-canary"),
        ("version", ""),
        ("version", "x" * 65),
        ("entry", "../plugin.py"),
        ("entry", "C:/plugin.py"),
        ("entry", "plugin.py\0"),
        ("entry", "plugin.txt"),
    ):
        value = manifest()
        value[field] = bad
        invalid(lambda: parse_manifest(value))
    for field, bad in (
        ("description", ""),
        ("description", "x" * 513),
        ("description", "private-canary\n"),
        ("name", "UPPER"),
    ):
        value = manifest()
        value["tools"][0][field] = bad
        invalid(lambda: parse_manifest(value))
    value = manifest()
    value["tools"][0]["handler"] = "run"
    invalid(lambda: parse_manifest(value))


@pytest.mark.parametrize(
    "keyword,value",
    [
        ("$ref", "https://example.invalid/schema"),
        ("pattern", "a.*"),
        ("format", "uuid"),
        ("oneOf", []),
        ("allOf", []),
        ("min", 1),
        ("$defs", {}),
    ],
)
def test_schema_does_not_allow_remote_references_composition_or_other_dialects(keyword, value):
    request = manifest()
    request["tools"][0]["parameters"][keyword] = value
    invalid(lambda: parse_manifest(request))


def test_schema_validates_types_applicable_bounds_enum_required_and_structural_limits():
    value = manifest()
    parameters = value["tools"][0]["parameters"]
    parameters["properties"] = {
        "count": {"type": "integer", "minimum": 0, "maximum": 10},
        "labels": {
            "type": "array",
            "items": {"type": "string", "minLength": 1, "maxLength": 10},
            "minItems": 0,
            "maxItems": 4,
        },
        "enabled": {"type": "boolean", "enum": [True, False]},
    }
    parameters["required"] = ["count"]
    assert parse_manifest(value) == value
    for bad in (
        {"type": "object", "properties": {}, "additionalProperties": True},
        {"type": "object", "properties": {}, "additionalProperties": False, "required": ["missing"]},
        {"type": "object", "properties": {"x": {"type": "array"}}, "additionalProperties": False},
        {"type": "object", "properties": {"x": {"type": "integer", "enum": [True]}}, "additionalProperties": False},
        {
            "type": "object",
            "properties": {"x": {"type": "number", "maximum": float("inf")}},
            "additionalProperties": False,
        },
        {"type": "object", "properties": {"x": {"type": "string", "minLength": True}}, "additionalProperties": False},
        {"type": "object", "properties": {"x": {"type": "string", "minimum": 1}}, "additionalProperties": False},
        {
            "type": "object",
            "properties": {"x": {"type": "integer", "minimum": 2, "maximum": 1}},
            "additionalProperties": False,
        },
    ):
        request = manifest()
        request["tools"][0]["parameters"] = bad
        invalid(lambda: parse_manifest(request))
    request = manifest()
    request["tools"][0]["parameters"]["properties"] = {f"x{index}": {"type": "null"} for index in range(520)}
    assert len(encoded(request["tools"][0]["parameters"])) < MAX_SCHEMA_BYTES
    invalid(lambda: parse_manifest(request))
    nested = {"type": "string"}
    for _ in range(13):
        nested = {"type": "array", "items": nested}
    request = manifest()
    request["tools"][0]["parameters"]["properties"]["nested"] = nested
    invalid(lambda: parse_manifest(request))


def test_install_identity_and_owner_trust_require_exact_observed_fields():
    request = {"key": "import-1", "source": "/explicit/plugin", "extension_id": None, "revision": 0}
    parsed = parse_install(request)
    assert parsed == request and parsed is not request
    assert request_fingerprint(request) == hashlib.sha256(encoded(request)).hexdigest()
    update = {**request, "extension_id": identity(1), "revision": MAX_INTEGER}
    assert parse_install(update) == update
    for change in (
        {"revision": 1},
        {"extension_id": identity(1)},
        {"extension_id": ""},
        {"revision": False},
        {"revision": MAX_INTEGER + 1},
        {"source": "private-canary\n"},
        {"key": " "},
        {"key": "x" * 201},
        {"command": "run"},
    ):
        invalid(lambda: parse_install({**request, **change}))
    auth = authorization()
    assert parse_authorization(auth) == auth and parse_authorization(auth) is not auth
    for change in (
        {"revision": True},
        {"trust": False},
        {"trust": 1},
        {"package_sha256": "A" * 64},
        {"catalog_sha256": "a" * 63},
        {"history_epoch": "not-a-uuid"},
        {"tools": ["tool0"]},
        {"tools": auth["tools"] * 2},
        {"fingerprint": "a" * 64},
    ):
        invalid(lambda: parse_authorization({**auth, **change}))
    for field in auth:
        invalid(lambda: parse_authorization({key: value for key, value in auth.items() if key != field}))


def test_run_bound_tool_specs_and_page_revision_contracts_are_explicit():
    row = {**catalog(manifest())[0], "extension_id": identity(1), "binding_id": identity(2)}
    spec = tool_spec(row)
    assert spec.name == row["name"] and spec.binding_id == identity(2) and spec.resource == "extension:" + identity(1)
    assert spec.writes is True and spec.parameters == row["parameters"] and spec.parameters is not row["parameters"]
    assert ToolSpec("builtin", "", {}).binding_id == ""
    for bad in ("not-a-uuid", identity(1).replace("-", ""), None, True):
        with pytest.raises((ValueError, TypeError, AttributeError)):
            ToolSpec("bound", "", {}, binding_id=bad)
        invalid(lambda: tool_spec({**row, "binding_id": bad}))
    assert page() is None and page(MAX_INTEGER, 50) is None and positive_revision(MAX_INTEGER) == MAX_INTEGER
    for value in (True, False, 0, -1, MAX_INTEGER + 1, 1.0, "1"):
        invalid(lambda: positive_revision(value))
    for after, limit in ((True, 1), (-1, 1), (MAX_INTEGER + 1, 1), (0, False), (0, 0), (0, 51)):
        invalid(lambda: page(after, limit))
    captured = CapturedPackage(
        manifest(), ({"path": "plugin.py", "bytes": 1, "sha256": "a" * 64},), "b" * 64, "c" * 64, b"x"
    )
    with pytest.raises(AttributeError):
        captured.package_sha256 = "d" * 64
