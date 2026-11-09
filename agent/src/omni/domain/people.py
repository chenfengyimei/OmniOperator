"""Person, account and memory document rules; contains no I/O or ORM types."""

from __future__ import annotations

import hashlib
import re
import unicodedata

from .contracts import BusinessError

DOCUMENT_KINDS = frozenset({"profile", "resident"})


def person_name(value: str) -> str:
    value = value.strip()
    if not value or len(value) > 100 or any(ord(c) < 32 for c in value):
        raise BusinessError("invalid_person_name", "Person name must contain 1 to 100 printable characters", status=422)
    return value


def account_identity(channel: str, external_id: str) -> tuple[str, str]:
    channel, external_id = channel.strip().lower(), external_id.strip()
    if (
        not re.fullmatch(r"[a-z][a-z0-9_-]{0,31}", channel)
        or not external_id
        or len(external_id) > 200
        or any(ord(c) < 32 for c in external_id)
    ):
        raise BusinessError("invalid_account", "A channel and bounded external identity are required", status=422)
    return channel, external_id


def content_fingerprint(value: str) -> str:
    normalized = " ".join(unicodedata.normalize("NFKC", value).split()).casefold()
    return hashlib.sha256(b"content\0" + normalized.encode("utf-8")).hexdigest()


def source_fingerprint(kind: str, source_id: str, quote: str) -> str:
    normalized = " ".join(unicodedata.normalize("NFKC", quote).split()).casefold()
    return hashlib.sha256(b"source\0" + (kind + ":" + source_id + ":" + normalized).encode("utf-8")).hexdigest()


def document_content(kind: str, content: str) -> str:
    if kind not in DOCUMENT_KINDS:
        raise BusinessError("invalid_document_kind", "Only profile and resident documents are supported", status=422)
    if len(content) > 8000:
        raise BusinessError("document_too_large", "A personal document contains at most 8000 characters", status=422)
    return content.strip()
