"""Static trusted-plugin metadata, immutable capture records and owner authorizations; never loads code or files."""

from __future__ import annotations

import hashlib
import json
import math
import re
import unicodedata
import uuid
from dataclasses import dataclass

from .contracts import BusinessError, ToolSpec
from .history import MAX_INTEGER

FORMAT = "omni-python-extension-1"
PROTOCOL = "omni-python-extension-ipc-1"
MAX_TOOLS = 16
MAX_ARTIFACTS = 4
MAX_ARTIFACT_BYTES = 8_388_608
MAX_ARTIFACT_TOTAL_BYTES = 16_777_216
MAX_ARTIFACT_NAME = 120
MAX_MANIFEST_BYTES = 65_536
MAX_SCHEMA_BYTES = 16_384
MAX_SCHEMA_DEPTH = 12
MAX_SCHEMA_NODES = 1_024
MAX_FILES = 64
MAX_FILE_BYTES = 1_048_576
MAX_PACKAGE_BYTES = 8_388_608
NAME = re.compile(r"[a-z][a-z0-9_]{0,31}")
SCHEMA_KEYS = frozenset(
    {
        "type",
        "properties",
        "required",
        "additionalProperties",
        "items",
        "enum",
        "minimum",
        "maximum",
        "minLength",
        "maxLength",
        "minItems",
        "maxItems",
        "description",
    }
)
TYPES = frozenset({"object", "array", "string", "number", "integer", "boolean", "null"})


def _invalid() -> BusinessError:
    return BusinessError(
        "extension_invalid", "Use a bounded static extension manifest and explicit owner authorization", status=422
    )


def _text(value: object, maximum: int, *, empty: bool = False) -> str:
    if (
        not isinstance(value, str)
        or len(value) > maximum
        or (not empty and not value.strip())
        or any(unicodedata.category(char) == "Cc" for char in value)
    ):
        raise ValueError
    value.encode("utf-8")
    return value


def _uuid(value: object) -> str:
    if not isinstance(value, str) or len(value) != 36 or str(uuid.UUID(value)) != value:
        raise ValueError
    return value


def _integer(value: object, minimum: int = 1, maximum: int = MAX_INTEGER) -> int:
    if type(value) is not int or not minimum <= value <= maximum:
        raise ValueError
    return value


def _sha(value: object) -> str:
    if not isinstance(value, str) or re.fullmatch(r"[0-9a-f]{64}", value) is None:
        raise ValueError
    return value


def _encoded(value: object) -> bytes:
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"), allow_nan=False).encode("utf-8")


def _fingerprint(value: object) -> str:
    return hashlib.sha256(_encoded(value)).hexdigest()


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
        value.encode("utf-8")
    elif isinstance(value, float):
        if not math.isfinite(value):
            raise ValueError
    elif value is not None and type(value) not in {bool, int}:
        raise ValueError


def _matches(value: object, kind: str) -> bool:
    return {
        "object": isinstance(value, dict),
        "array": isinstance(value, list),
        "string": isinstance(value, str),
        "number": type(value) in {int, float},
        "integer": type(value) is int,
        "boolean": type(value) is bool,
        "null": value is None,
    }[kind]


def _schema(value: object) -> dict:
    if not isinstance(value, dict) or not set(value) <= SCHEMA_KEYS:
        raise ValueError
    kind = value.get("type")
    if not isinstance(kind, str) or kind not in TYPES:
        raise ValueError
    allowed = {"type", "description", "enum"}
    if kind == "object":
        allowed |= {"properties", "required", "additionalProperties"}
        if value.get("additionalProperties") is not False or not isinstance(value.get("properties"), dict):
            raise ValueError
        properties = value["properties"]
        for name, schema in properties.items():
            _text(name, 100)
            _schema(schema)
        required = value.get("required", [])
        if (
            not isinstance(required, list)
            or not all(isinstance(name, str) for name in required)
            or len(set(required)) != len(required)
            or not set(required) <= set(properties)
        ):
            raise ValueError
    elif kind == "array":
        allowed |= {"items", "minItems", "maxItems"}
        _schema(value.get("items"))
    elif kind == "string":
        allowed |= {"minLength", "maxLength"}
    elif kind in {"number", "integer"}:
        allowed |= {"minimum", "maximum"}
    if not set(value) <= allowed:
        raise ValueError
    if "description" in value:
        _text(value["description"], 512, empty=True)
    for lower, upper in (("minLength", "maxLength"), ("minItems", "maxItems")):
        for key in (lower, upper):
            if key in value:
                _integer(value[key], 0)
        if lower in value and upper in value and value[lower] > value[upper]:
            raise ValueError
    for key in ("minimum", "maximum"):
        if key in value and (type(value[key]) not in {int, float} or not math.isfinite(value[key])):
            raise ValueError
    if "minimum" in value and "maximum" in value and value["minimum"] > value["maximum"]:
        raise ValueError
    if "enum" in value:
        choices = value["enum"]
        if not isinstance(choices, list) or not 1 <= len(choices) <= 100:
            raise ValueError
        if not all(_matches(choice, kind) for choice in choices) or len(
            {_encoded(choice) for choice in choices}
        ) != len(choices):
            raise ValueError
    return value


def _parameters(value: object) -> dict:
    _json_nodes(value)
    parameters = _schema(value)
    raw = _encoded(parameters)
    if parameters["type"] != "object" or len(raw) > MAX_SCHEMA_BYTES:
        raise ValueError
    return json.loads(raw)


def parse_manifest(data: object) -> dict:
    try:
        if (
            not isinstance(data, dict)
            or set(data) != {"format", "id", "version", "entry", "tools"}
            or data["format"] != FORMAT
        ):
            raise ValueError
        identity = data["id"]
        if not isinstance(identity, str) or NAME.fullmatch(identity) is None:
            raise ValueError
        _text(data["version"], 64)
        entry = _text(data["entry"], 200)
        parts = entry.split("/")
        if (
            entry.startswith("/")
            or "\\" in entry
            or ":" in entry
            or not entry.endswith(".py")
            or any(part in {"", ".", ".."} for part in parts)
        ):
            raise ValueError
        tools = data["tools"]
        if not isinstance(tools, list) or not 1 <= len(tools) <= MAX_TOOLS:
            raise ValueError
        names = set()
        for tool in tools:
            if not isinstance(tool, dict) or set(tool) not in (
                {"name", "description", "parameters", "writes", "timeout_s"},
                {"name", "description", "parameters", "writes", "timeout_s", "artifacts"},
            ):
                raise ValueError
            name = tool["name"]
            if not isinstance(name, str) or NAME.fullmatch(name) is None or name in names:
                raise ValueError
            if len(f"ext__{identity}__{name}") > 64:
                raise ValueError
            names.add(name)
            _text(tool["description"], 512)
            if tool["writes"] is not True:
                raise ValueError
            _integer(tool["timeout_s"], 1, 300)
            _parameters(tool["parameters"])
            artifact_types(tool.get("artifacts", []))
        raw = _encoded(data)
        if len(raw) > MAX_MANIFEST_BYTES:
            raise ValueError
        return json.loads(raw)
    except (ValueError, TypeError, KeyError, AttributeError, OverflowError, UnicodeError, RecursionError) as exc:
        raise _invalid() from exc


def catalog(manifest: object) -> list[dict]:
    value = parse_manifest(manifest)
    result = []
    for tool in sorted(value["tools"], key=lambda item: item["name"]):
        row = {
            "local_name": tool["name"],
            "name": f"ext__{value['id']}__{tool['name']}",
            "description": tool["description"],
            "parameters": tool["parameters"],
            "writes": True,
            "timeout_s": tool["timeout_s"],
            "artifacts": sorted(tool.get("artifacts", [])),
        }
        result.append({**row, "spec_sha256": _fingerprint(row)})
    return result


def catalog_fingerprint(manifest: object) -> str:
    return _fingerprint(catalog(manifest))


MEDIA_TYPE = re.compile(r"[a-z0-9][a-z0-9.+-]{0,63}/[a-z0-9][a-z0-9.+-]{0,63}")
ARTIFACT_NAME = re.compile(r"[A-Za-z0-9][A-Za-z0-9._-]{0,119}")
ARTIFACT_FIELDS = frozenset({"name", "media_type", "bytes", "sha256"})


def artifact_types(value: object) -> list[str]:
    """Owner-declared media types a tool may publish; never inferred from the child."""
    if not isinstance(value, list) or len(value) > MAX_ARTIFACTS:
        raise ValueError
    if not all(isinstance(item, str) and MEDIA_TYPE.fullmatch(item) for item in value):
        raise ValueError
    if len(set(value)) != len(value):
        raise ValueError
    return list(value)


def tool_artifacts(manifest: object, local_name: str) -> tuple[str, ...]:
    value = parse_manifest(manifest)
    for tool in value["tools"]:
        if tool["name"] == local_name:
            return tuple(artifact_types(tool.get("artifacts", [])))
    raise _invalid()


def artifact_descriptors(value: object, allowed: object) -> tuple[dict, ...]:
    """Validate child-reported artifact metadata; file bytes are checked by the host."""
    try:
        if not isinstance(allowed, (list, tuple)):
            raise ValueError
        permitted = set(artifact_types(list(allowed)))
        if not isinstance(value, list) or len(value) > MAX_ARTIFACTS:
            raise ValueError
        names: set[str] = set()
        total = 0
        result = []
        for item in value:
            if not isinstance(item, dict) or set(item) != ARTIFACT_FIELDS:
                raise ValueError
            name = item["name"]
            if not isinstance(name, str) or ARTIFACT_NAME.fullmatch(name) is None or name in names:
                raise ValueError
            names.add(name)
            if item["media_type"] not in permitted:
                raise ValueError
            size = _integer(item["bytes"], 1, MAX_ARTIFACT_BYTES)
            total += size
            if total > MAX_ARTIFACT_TOTAL_BYTES:
                raise ValueError
            digest = _sha(item["sha256"])
            result.append({"name": name, "media_type": item["media_type"], "bytes": size, "sha256": digest})
        return tuple(result)
    except (ValueError, TypeError, KeyError, AttributeError, OverflowError) as exc:
        raise _invalid() from exc


def artifact_summary(descriptors: object) -> str:
    """A bounded model-visible line: types and sizes only, never names or bytes."""
    if not isinstance(descriptors, (list, tuple)) or not descriptors:
        return ""
    parts = []
    for item in descriptors:
        if isinstance(item, dict):
            parts.append(f"{str(item.get('media_type'))} {int(item.get('bytes', 0))}B")
    return ("artifacts: " + ", ".join(parts))[:512]


def parse_install(data: object) -> dict:
    try:
        if not isinstance(data, dict) or set(data) != {"key", "source", "extension_id", "revision"}:
            raise ValueError
        key, source = _text(data["key"], 200), _text(data["source"], 4_000)
        identity = None if data["extension_id"] is None else _uuid(data["extension_id"])
        revision = _integer(data["revision"], 0)
        if (identity is None) != (revision == 0):
            raise ValueError
        return {"key": key, "source": source, "extension_id": identity, "revision": revision}
    except (ValueError, TypeError, KeyError, AttributeError, OverflowError, UnicodeError) as exc:
        raise _invalid() from exc


def request_fingerprint(data: object) -> str:
    return _fingerprint(parse_install(data))


def parse_authorization(data: object) -> dict:
    try:
        if not isinstance(data, dict) or set(data) != {
            "revision",
            "package_sha256",
            "catalog_sha256",
            "history_epoch",
            "tools",
            "trust",
        }:
            raise ValueError
        tools = data["tools"]
        if data["trust"] is not True or not isinstance(tools, list) or not 1 <= len(tools) <= MAX_TOOLS:
            raise ValueError
        if not all(
            isinstance(name, str) and re.fullmatch(r"ext__[a-z][a-z0-9_]{0,31}__[a-z][a-z0-9_]{0,31}", name)
            for name in tools
        ):
            raise ValueError
        if len(set(tools)) != len(tools):
            raise ValueError
        return {
            "revision": _integer(data["revision"]),
            "package_sha256": _sha(data["package_sha256"]),
            "catalog_sha256": _sha(data["catalog_sha256"]),
            "history_epoch": _uuid(data["history_epoch"]),
            "tools": sorted(tools),
            "trust": True,
        }
    except (ValueError, TypeError, KeyError, AttributeError, OverflowError, UnicodeError) as exc:
        raise _invalid() from exc


def positive_revision(value: object) -> int:
    try:
        return _integer(value)
    except (ValueError, TypeError) as exc:
        raise _invalid() from exc


def page(after: int = 0, limit: int = 20) -> None:
    try:
        _integer(after, 0)
        _integer(limit, 1, 50)
    except (ValueError, TypeError) as exc:
        raise _invalid() from exc


def tool_spec(record: dict) -> ToolSpec:
    try:
        binding = _uuid(record["binding_id"])
        extension_id = _uuid(record["extension_id"])
        if record["writes"] is not True:
            raise ValueError
        if (
            not isinstance(record["name"], str)
            or len(record["name"]) > 64
            or re.fullmatch(r"ext__[a-z][a-z0-9_]{0,31}__[a-z][a-z0-9_]{0,31}", record["name"]) is None
        ):
            raise ValueError
        return ToolSpec(
            name=record["name"],
            description=_text(record["description"], 512),
            parameters=_parameters(record["parameters"]),
            writes=True,
            timeout_s=_integer(record["timeout_s"], 1, 300),
            resource="extension:" + extension_id,
            binding_id=binding,
        )
    except (ValueError, TypeError, KeyError, AttributeError, OverflowError, UnicodeError) as exc:
        raise _invalid() from exc


@dataclass(frozen=True)
class CapturedPackage:
    manifest: dict
    files: tuple[dict, ...]
    package_sha256: str
    catalog_sha256: str
    content: bytes
