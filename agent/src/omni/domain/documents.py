"""Bounded source text, deterministic chunks and local vector math; contains no I/O."""

from __future__ import annotations

import hashlib
import math
import re
from dataclasses import dataclass
from typing import cast

from .contracts import BusinessError

MAX_DOCUMENT_BYTES = 262144
MAX_DOCUMENT_CHARS = 100000
CHUNK_CHARS = 1000
TEXT_INDEX_VERSION = "utf8-chunks-v1"
VECTOR_CANDIDATES = 512


def document_title(value: str) -> str:
    if not value.strip() or len(value) > 200 or any(ord(c) < 32 for c in value):
        raise BusinessError("invalid_document", "A printable title of 1 to 200 characters is required", status=422)
    return value.strip()


@dataclass(frozen=True)
class DocumentText:
    content: str
    sha256: str
    size: int

    @classmethod
    def parse(cls, payload: bytes) -> DocumentText:
        try:
            content = payload.decode("utf-8")
        except UnicodeError as exc:
            raise BusinessError("invalid_document", "Source documents require UTF-8 text", status=422) from exc
        if (
            not content.strip()
            or "\0" in content
            or len(content) > MAX_DOCUMENT_CHARS
            or len(payload) > MAX_DOCUMENT_BYTES
        ):
            raise BusinessError("invalid_document", "Document exceeds text limits or contains invalid text", status=422)
        return cls(content, hashlib.sha256(payload).hexdigest(), len(payload))

    @classmethod
    def from_text(cls, content: str) -> DocumentText:
        try:
            return cls.parse(content.encode("utf-8"))
        except UnicodeError as exc:
            raise BusinessError("invalid_document", "Source text must be valid UTF-8", status=422) from exc

    def chunks(self) -> list[dict[str, object]]:
        return [
            {
                "position": i // CHUNK_CHARS,
                "start": i,
                "end": min(i + CHUNK_CHARS, len(self.content)),
                "content": self.content[i : i + CHUNK_CHARS],
            }
            for i in range(0, len(self.content), CHUNK_CHARS)
        ]


def normalized_vector(value: object, dimension: int) -> list[float]:
    if type(dimension) is not int or not 1 <= dimension <= 512 or not isinstance(value, (list, tuple)):
        raise BusinessError(
            "invalid_vector", "A finite vector with the declared bounded dimension is required", status=422
        )
    items = cast(list[object] | tuple[object, ...], value)
    if len(items) != dimension or not all(isinstance(n, (int, float)) and math.isfinite(cast(float, n)) for n in items):
        raise BusinessError(
            "invalid_vector", "A finite vector with the declared bounded dimension is required", status=422
        )
    numbers = cast(list[float] | tuple[float, ...], items)
    norm = math.hypot(*numbers)
    if not norm or not math.isfinite(norm):
        raise BusinessError("invalid_vector", "A finite nonzero vector is required", status=422)
    return [float(n) / norm for n in numbers]


def vector_identity(version: str, dimension: int) -> None:
    if not re.fullmatch(r"[A-Za-z0-9_.:/-]{1,100}", version) or type(dimension) is not int or not 1 <= dimension <= 512:
        raise BusinessError(
            "vector_unavailable", "A versioned local vectorizer with dimension 1 to 512 is required", status=503
        )


def merge_results(
    text_rows: list[dict[str, object]], vector_rows: list[dict[str, object]], limit: int
) -> list[dict[str, object]]:
    scores: dict[str, float] = {}
    rows: dict[str, dict[str, object]] = {}
    for source in (text_rows, vector_rows):
        for rank, row in enumerate(source, 1):
            key = cast(str, row["chunk_id"])
            rows[key] = row
            scores[key] = scores.get(key, 0.0) + 1 / (60 + rank)
    return [{**rows[key], "score": scores[key]} for key in sorted(scores, key=lambda key: (-scores[key], key))[:limit]]
