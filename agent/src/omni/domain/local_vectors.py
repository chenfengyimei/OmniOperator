"""Local embedding bundle and bounded transport contracts; no files, model imports or I/O."""

from __future__ import annotations

import hashlib
import json
import re
import uuid
from collections.abc import Sequence
from typing import cast

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


def manifest(value: object) -> dict[str, object]:
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
    if not isinstance(value, dict):
        raise invalid()
    record = cast(dict[str, object], value)
    if set(record) != keys or type(record["schema"]) is not int or record["schema"] != 1:
        raise invalid()
    for field, low, high in (("dimension", 1, 512), ("max_tokens", 8, 512), ("pad_id", 0, 2147483647)):
        number = cast(int, record[field])
        if type(record[field]) is not int or not low <= number <= high:
            raise invalid()
    if not isinstance(record["pooling"], str) or record["pooling"] not in {"mean_masked", "cls", "pooled"}:
        raise invalid()
    for field, maximum in (
        ("model_id", 100),
        ("output", 100),
        ("pad_token", 100),
        ("query_prefix", 200),
        ("document_prefix", 200),
    ):
        text = record[field]
        if not isinstance(text, str) or len(text) > maximum or any(ord(c) < 32 for c in text):
            raise invalid()
    if not record["model_id"] or not record["output"] or not record["pad_token"]:
        raise invalid()
    raw_files = record["files"]
    if not isinstance(raw_files, dict):
        raise invalid()
    files = cast(dict[str, object], raw_files)
    if set(files) != {"model.onnx", "tokenizer.json"}:
        raise invalid()
    for name, maximum in (("model.onnx", MAX_MODEL), ("tokenizer.json", MAX_TOKENIZER)):
        item = files[name]
        if not isinstance(item, dict):
            raise invalid()
        row = cast(dict[str, object], item)
        size = cast(int, row.get("bytes"))
        if (
            set(row) != {"sha256", "bytes"}
            or type(row["bytes"]) is not int
            or not 1 <= size <= maximum
            or not isinstance(row["sha256"], str)
            or not re.fullmatch(r"[0-9a-f]{64}", row["sha256"])
        ):
            raise invalid()
    return cast(dict[str, object], json.loads(canonical(record)))


def version(value: dict[str, object], runtime: dict[str, object] | None = None) -> str:
    return "onnx-v1:" + fingerprint(
        {
            "manifest": manifest(value),
            "runtime": RUNTIME if runtime is None else runtime,
            "windows": "overflow-token-weighted-v1",
            "provider": "cpu",
        }
    )


def texts_request(texts: Sequence[str], purpose: str) -> list[str]:
    if purpose not in {"document", "query"} or not isinstance(texts, (list, tuple)):
        raise invalid("vector_input_invalid")
    items = cast(list[object] | tuple[object, ...], texts)
    if not 1 <= len(items) <= 8:
        raise invalid("vector_input_invalid")
    if any(not isinstance(t, str) or not t.strip() or len(t) > 1200 or "\0" in t for t in items):
        raise invalid("vector_input_invalid")
    try:
        for text in items:
            cast(str, text).encode("utf-8")
    except UnicodeError:
        raise invalid("vector_input_invalid") from None
    return list(cast(list[str] | tuple[str, ...], items))


def _pairs(entries: list[tuple[str, object]]) -> dict[str, object]:
    result: dict[str, object] = {}
    for key, value in entries:
        if key in result:
            raise invalid("vector_protocol_invalid")
        result[key] = value
    return result


def decode(frame: bytes) -> dict[str, object]:
    if not frame.endswith(b"\n") or len(frame) > MAX_FRAME:
        raise invalid("vector_protocol_invalid")
    try:
        row = json.loads(frame, object_pairs_hook=_pairs, parse_constant=lambda _: (_ for _ in ()).throw(ValueError()))
        if not isinstance(row, dict):
            raise ValueError()
        record = cast(dict[str, object], row)
        identifier = record.get("id")
        if (
            record.get("protocol") != PROTOCOL
            or not isinstance(identifier, str)
            or str(uuid.UUID(identifier)) != identifier
        ):
            raise ValueError()
        return record
    except (ValueError, UnicodeError, RecursionError):
        raise invalid("vector_protocol_invalid") from None


def vectors(value: object, count: int, dimension: int) -> list[list[float]]:
    if not isinstance(value, list):
        raise invalid("vector_protocol_invalid")
    items = cast(list[object], value)
    if len(items) != count:
        raise invalid("vector_protocol_invalid")
    return [normalized_vector(row, dimension) for row in items]
