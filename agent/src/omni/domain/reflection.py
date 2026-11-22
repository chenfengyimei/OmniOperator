"""Validate a journal draft before its captured document evidence is resolved."""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from typing import cast

from .contracts import BusinessError

JOURNAL_FIELDS = (
    "encounter",
    "facts",
    "author_view",
    "inference",
    "previous_judgment",
    "revision",
    "missing",
    "next_response",
)


def journal_body(fields: Mapping[str, object]) -> dict[str, str]:
    if set(fields) != set(JOURNAL_FIELDS):
        raise BusinessError("invalid_journal", "Expected the eight journal fields", status=422)
    body: dict[str, str] = {}
    for field in JOURNAL_FIELDS:
        value = fields[field]
        if (
            not isinstance(value, str)
            or len(value) > 12000
            or (field in ("encounter", "missing") and not value.strip())
        ):
            raise BusinessError("invalid_journal", "Invalid journal field: " + field, status=422)
        try:
            value.encode("utf-8")
        except UnicodeEncodeError as exc:
            raise BusinessError("invalid_journal", "Journal text must be valid Unicode", status=422) from exc
        body[field] = value
    return body


def document_links(body: Mapping[str, str], links: Sequence[object]) -> list[tuple[str, str]]:
    if not 1 <= len(links) <= 20:
        raise BusinessError("invalid_journal_anchor", "Expected one to twenty captured ranges", status=422)
    unique: set[tuple[str, str]] = set()
    for link in links:
        if not isinstance(link, Mapping):
            raise BusinessError("invalid_journal_anchor", "Expected a document capture and journal field", status=422)
        row = cast(Mapping[str, object], link)
        capture, field = row.get("capture_id"), row.get("field")
        if (
            set(row) != {"capture_id", "field"}
            or not isinstance(capture, str)
            or len(capture) != 36
            or not capture.isascii()
            or not isinstance(field, str)
            or not body.get(field, "").strip()
        ):
            raise BusinessError("invalid_journal_anchor", "Invalid document capture or journal field", status=422)
        unique.add((capture, field))
    return sorted(unique)


def excerpt(body: Mapping[str, object], fields: Mapping[str, object]) -> str:
    field, start, end = fields.get("field"), fields.get("start"), fields.get("end")
    value = body.get(field) if isinstance(field, str) else None
    if (
        field not in ("revision", "next_response")
        or not isinstance(value, str)
        or type(start) is not int
        or type(end) is not int
        or not 0 <= start < end <= len(value)
        or end - start > 240
        or not value[start:end].strip()
    ):
        raise BusinessError(
            "reflection_excerpt_invalid", "Choose 1–240 exact characters from revision or next_response", status=422
        )
    sentence = value[start:end]
    if "sentence" in fields and fields["sentence"] != sentence:
        raise BusinessError("reflection_excerpt_invalid", "Excerpt differs from source text", status=422)
    return sentence
