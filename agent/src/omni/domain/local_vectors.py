"""Local embedding bundle and bounded transport contracts; no files, model imports or I/O."""

from __future__ import annotations

import hashlib
import json
import re
import uuid
from collections.abc import Sequence

from .contracts import BusinessError
from .documents import normalized_vector

PROTOCOL = "omni-local-vector-1"
MAX_FRAME = 131072
MAX_MODEL = 512 * 1024 * 1024
MAX_TOKENIZER = 16 * 1024 * 1024
RUNTIME = {"onnx": "1.23.0", "onnxruntime": "1.31.1", "tokenizers": "0.23.3", "numpy": "2.5.4"}


def invalid(code: str = "vector_bundle_invalid") -> BusinessError:
    return BusinessError(code, "Local vector bundle or protocol cannot be verified", status=422)


def canonical(value: object) -> bytes:
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"), allow_nan=False).encode()


def fingerprint(value: object) -> str:
    return hashlib.sha256(canonical(value)).hexdigest()


def manifest(value: object) -> dict:
    keys = {
        "schema",
        "model_id",
        "dimension",
        "max_tokens",
        "pooling",
        "output",
        "pad_id",
        "pad_token",
        "query_prefix",
        "document_prefix",
        "files",
    }
    if not isinstance(value, dict) or set(value) != keys or type(value["schema"]) is not int or value["schema"] != 1:
        raise invalid()
    for field, low, high in (("dimension", 1, 512), ("max_tokens", 8, 512), ("pad_id", 0, 2147483647)):
        if type(value[field]) is not int or not low <= value[field] <= high:
            raise invalid()
    if not isinstance(value["pooling"], str) or value["pooling"] not in {"mean_masked", "cls", "pooled"}:
        raise invalid()
    for field, maximum in (
        ("model_id", 100),
        ("output", 100),
        ("pad_token", 100),
        ("query_prefix", 200),
        ("document_prefix", 200),
    ):
        if not isinstance(value[field], str) or len(value[field]) > maximum or any(ord(c) < 32 for c in value[field]):
            raise invalid()
    if not value["model_id"] or not value["output"] or not value["pad_token"]:
        raise invalid()
    files = value["files"]
    if not isinstance(files, dict) or set(files) != {"model.onnx", "tokenizer.json"}:
        raise invalid()
    for name, maximum in (("model.onnx", MAX_MODEL), ("tokenizer.json", MAX_TOKENIZER)):
        row = files[name]
        if (
            not isinstance(row, dict)
            or set(row) != {"sha256", "bytes"}
            or type(row["bytes"]) is not int
            or not 1 <= row["bytes"] <= maximum
            or not isinstance(row["sha256"], str)
            or not re.fullmatch(r"[0-9a-f]{64}", row["sha256"])
        ):
            raise invalid()
    return json.loads(canonical(value))


def version(value: dict, runtime: dict | None = None) -> str:
    return "onnx-v1:" + fingerprint(
        {
            "manifest": manifest(value),
            "runtime": RUNTIME if runtime is None else runtime,
            "windows": "overflow-token-weighted-v1",
            "provider": "cpu",
        }
    )


def texts_request(texts: Sequence[str], purpose: str) -> list[str]:
    if (
        not isinstance(purpose, str)
        or purpose not in {"document", "query"}
        or not isinstance(texts, (list, tuple))
        or not 1 <= len(texts) <= 8
    ):
        raise invalid("vector_input_invalid")
    if any(not isinstance(t, str) or not t.strip() or len(t) > 1200 or "\0" in t for t in texts):
        raise invalid("vector_input_invalid")
    try:
        for text in texts:
            text.encode("utf-8")
    except UnicodeError:
        raise invalid("vector_input_invalid") from None
    return list(texts)


def _pairs(entries: list[tuple[str, object]]) -> dict:
    result = {}
    for key, value in entries:
        if key in result:
            raise invalid("vector_protocol_invalid")
        result[key] = value
    return result


def decode(frame: bytes) -> dict:
    if not frame.endswith(b"\n") or len(frame) > MAX_FRAME:
        raise invalid("vector_protocol_invalid")
    try:
        row = json.loads(frame, object_pairs_hook=_pairs, parse_constant=lambda _: (_ for _ in ()).throw(ValueError()))
        if (
            not isinstance(row, dict)
            or row.get("protocol") != PROTOCOL
            or not isinstance(row.get("id"), str)
            or str(uuid.UUID(row["id"])) != row["id"]
        ):
            raise ValueError()
        return row
    except (ValueError, UnicodeError, RecursionError):
        raise invalid("vector_protocol_invalid") from None


def vectors(value: object, count: int, dimension: int) -> list[list[float]]:
    if not isinstance(value, list) or len(value) != count:
        raise invalid("vector_protocol_invalid")
    return [normalized_vector(row, dimension) for row in value]
