"""Versioned authority for keeping an isolated predecessor fenced; no IO or claims about external effects."""

from __future__ import annotations

from typing import TypedDict, cast

from .contracts import BusinessError
from .service_deployment import (
    deployment_fields,
    deployment_integer,
    deployment_path,
    deployment_sha,
    deployment_uuid,
)


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
    expected_source_witness: list[dict[str, object]]


def validate_witness(value: object) -> list[dict[str, object]]:
    if not isinstance(value, list):
        raise BusinessError("release_source_witness_invalid", "Bind the exact source database/WAL/journal fingerprints")
    items = cast(list[object], value)
    if len(items) != 3:
        raise BusinessError("release_source_witness_invalid", "Bind the exact source database/WAL/journal fingerprints")
    rows: list[dict[str, object]] = []
    for item, name in zip(items, ("omni.db", "omni.db-wal", "omni.db-journal"), strict=True):
        row = deployment_fields(item, {"path", "sha256", "bytes"})
        if row["path"] != name or (row["sha256"] is None and (row["bytes"] is not None or name == "omni.db")):
            raise BusinessError("release_source_witness_invalid", "Source fingerprint identity changed")
        rows.append(
            {
                "path": name,
                "sha256": None if row["sha256"] is None else deployment_sha(row["sha256"]),
                "bytes": None if row["sha256"] is None else deployment_integer(row["bytes"], 0, 1 << 50),
            }
        )
    return rows


def validate_authority(value: object, windows: bool) -> RecoveryAuthority:
    record = deployment_fields(
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
            "exchange_id": deployment_uuid(record["exchange_id"]),
            "exchange_sha256": deployment_sha(record["exchange_sha256"]),
            "exchange_journal": deployment_path(record["exchange_journal"], windows),
            "isolation_id": deployment_uuid(record["isolation_id"]),
            "isolation_sha256": deployment_sha(record["isolation_sha256"]),
            "release_id": deployment_uuid(record["release_id"]),
            "release_sha256": deployment_sha(record["release_sha256"]),
            "source_deployment_sha256": deployment_sha(record["source_deployment_sha256"]),
        },
    )
