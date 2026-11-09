"""Immutable object identities and metadata constraints, independent of storage."""

from __future__ import annotations

import re
import uuid
from dataclasses import dataclass

from .contracts import BusinessError

MAX_OBJECT_BYTES = 16 * 1024 * 1024


def agent_identity(value: str) -> str:
    try:
        if str(uuid.UUID(value)) != value:
            raise ValueError
    except (ValueError, AttributeError) as exc:
        raise BusinessError("invalid_agent_id", "A canonical Agent UUID is required", status=422) from exc
    return value


def media_type(value: str) -> str:
    if not re.fullmatch(r"[a-z0-9][a-z0-9!#$&^_.+-]{0,63}/[a-z0-9][a-z0-9!#$&^_.+-]{0,63}", value):
        raise BusinessError("invalid_media_type", "A bounded MIME type without parameters is required", status=422)
    return value


@dataclass(frozen=True)
class Blob:
    agent_id: str
    sha256: str
    size: int

    def __post_init__(self) -> None:
        agent_identity(self.agent_id)
        if not re.fullmatch(r"[0-9a-f]{64}", self.sha256):
            raise BusinessError("invalid_object_digest", "A full SHA256 digest is required", status=422)
        if type(self.size) is not int or not 0 <= self.size <= MAX_OBJECT_BYTES:
            raise BusinessError("object_too_large", "Object exceeds the 16 MiB limit", status=413)

    @property
    def key(self) -> str:
        return f"{self.agent_id}/{self.sha256[:2]}/{self.sha256}.blob"
