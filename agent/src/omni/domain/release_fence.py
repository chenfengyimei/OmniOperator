"""Persisted startup exclusion contract shared by immutable installations; no IO or mutable state."""

from __future__ import annotations

from .contracts import BusinessError
from .service_deployment import _object, _sha, _uuid

FORMAT = "omni-release-fence-1"


def fence(identity: str, sha256: str, role: str) -> dict:
    if not isinstance(role, str) or role not in {"source", "target"}:
        raise BusinessError("release_fence_invalid", "Use the exact reviewed release startup fence")
    return {"format": FORMAT, "release_id": _uuid(identity), "plan_sha256": _sha(sha256), "role": role}


def validate_fence(value: object) -> dict:
    record = _object(value, {"format", "release_id", "plan_sha256", "role"})
    expected = fence(record["release_id"], record["plan_sha256"], record["role"])
    if record != expected:
        raise BusinessError("release_fence_invalid", "The startup exclusion identity changed")
    return expected
