"""Versioned authority for keeping an isolated predecessor fenced; no IO or claims about external effects."""

from __future__ import annotations

from typing import TypedDict, cast

from .contracts import BusinessError
from .service_deployment import _integer, _object, _path, _sha, _uuid


class RecoveryAuthority(TypedDict):
    exchange_id: str
    exchange_sha256: str
    exchange_journal: str
    isolation_id: str
    isolation_sha256: str
    release_id: str
    release_sha256: str
    source_deployment_sha256: str


class RecoverySpec(TypedDict):
    release_sha256: str
    snapshot: str
    manifest_sha256: str
    isolation: RecoveryAuthority | None
    restore_policy: str
    expected_source_witness: list[dict]


def validate_witness(value: object) -> list[dict]:
    if not isinstance(value, list) or len(value) != 3:
        raise BusinessError("release_source_witness_invalid", "Bind the exact source database/WAL/journal fingerprints")
    rows = []
    for item, name in zip(value, ("omni.db", "omni.db-wal", "omni.db-journal"), strict=True):
        row = _object(item, {"path", "sha256", "bytes"})
        if row["path"] != name or (row["sha256"] is None and (row["bytes"] is not None or name == "omni.db")):
            raise BusinessError("release_source_witness_invalid", "Source fingerprint identity changed")
        rows.append(
            {
                "path": name,
                "sha256": None if row["sha256"] is None else _sha(row["sha256"]),
                "bytes": None if row["sha256"] is None else _integer(row["bytes"], 0, 1 << 50),
            }
        )
    return rows


def validate_authority(value: object, windows: bool) -> RecoveryAuthority:
    record = _object(
        value,
        {
            "exchange_id",
            "exchange_sha256",
            "exchange_journal",
            "isolation_id",
            "isolation_sha256",
            "release_id",
            "release_sha256",
            "source_deployment_sha256",
        },
    )
    return cast(
        RecoveryAuthority,
        {
            "exchange_id": _uuid(record["exchange_id"]),
            "exchange_sha256": _sha(record["exchange_sha256"]),
            "exchange_journal": _path(record["exchange_journal"], windows),
            "isolation_id": _uuid(record["isolation_id"]),
            "isolation_sha256": _sha(record["isolation_sha256"]),
            "release_id": _uuid(record["release_id"]),
            "release_sha256": _sha(record["release_sha256"]),
            "source_deployment_sha256": _sha(record["source_deployment_sha256"]),
        },
    )
