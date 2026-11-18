"""Reviewed immutable release/recovery plans; same-schema first delivery, with no platform or storage IO."""

from __future__ import annotations

import hashlib
import json
import ntpath
import posixpath
from collections.abc import Mapping
from typing import Any, TypedDict, cast

from .contracts import BusinessError
from .recovery_authority import RecoverySpec, validate_authority, validate_witness
from .schema_upgrade import schema_upgrade
from .service_control import ControlPlan, validate_control
from .service_deployment import (
    deployment_created,
    deployment_fields,
    deployment_path,
    deployment_sha,
    deployment_uuid,
    manager_identity,
    validate_plan,
)

FORMAT = "omni-release-plan-3"
FENCE_FILES = (
    "domain/release_fence.py",
    "adapters/platform/runtime/paths.py",
    "domain/service_activation.py",
    "adapters/platform/services/service_launch.py",
)


class ReleasePlan(TypedDict):
    format: str
    id: str
    created_at: str
    source: dict[str, Any]
    target: dict[str, Any]
    paths: dict[str, str]
    recovery: RecoverySpec | None
    source_stop: ControlPlan | None
    schema_upgrade: dict[str, Any] | None
    starts_service: bool
    stops_service: bool
    overwrites_source: bool
    sha256: str


class ReleaseSnapshot(TypedDict):
    backup: str
    backup_manifest_sha256: str
    restore_from: str
    restore_manifest_sha256: str
    objects: int
    source_witness: list[dict[str, Any]]


class ReleasePrepared(ReleaseSnapshot):
    target_witness: list[dict[str, Any]]
    restore_report_sha256: str
    credentials_restored: bool
    execution_quarantined: bool
    schema_upgrade_report_sha256: str | None


class ReleaseEvent(TypedDict):
    release_id: str
    release_sha256: str
    phase: str
    recorded_at: str
    data: dict[str, Any]


def digest(value: object) -> str:
    return hashlib.sha256(
        json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=False, allow_nan=False).encode("utf-8")
    ).hexdigest()


def invalid(code: str = "release_plan_invalid") -> BusinessError:
    return BusinessError(code, "Review an exact immutable release with separate new data, bundle, backup and journal")


def _runtime_files(runtime: Mapping[str, object]) -> dict[str, object]:
    files = runtime["files"]
    if not isinstance(files, list):
        raise invalid("release_fence_contract_missing")
    rows: dict[str, object] = {}
    for row in cast(list[object], files):
        if not isinstance(row, dict):
            raise invalid("release_fence_contract_missing")
        entry = cast(dict[str, object], row)
        path = entry["path"]
        if not isinstance(path, str):
            raise invalid("release_fence_contract_missing")
        rows[path] = entry
    return rows


def make_release(
    source: object,
    target: object,
    paths: object,
    identity: object,
    at: object,
    recovery: object = None,
    source_stop: object = None,
    migration: object = None,
) -> ReleasePlan:
    old, new = validate_plan(source), validate_plan(target)
    a = cast(dict[str, object], old["request"])
    b = cast(dict[str, object], new["request"])
    x = cast(dict[str, object], old["runtime"])
    y = cast(dict[str, object], new["runtime"])
    if (
        a["instance_id"] != b["instance_id"]
        or a["target"] != b["target"]
        or a["principal"] != b["principal"]
        or x["editable"]
        or y["editable"]
        or (old["isolated_registration"] and not new["isolated_registration"])
    ):
        raise invalid("release_installations_incompatible")
    transition = None
    if x["schema_head"] != y["schema_head"]:
        supplied_recovery = cast(dict[str, object], recovery) if isinstance(recovery, dict) else None
        snapshot_recovery = (
            supplied_recovery is not None
            and supplied_recovery.get("restore_policy") == "original_snapshot"
            and supplied_recovery.get("isolation") is None
            and migration is None
            and cast(int, x["schema_head"]) > cast(int, y["schema_head"])
        )
        if not snapshot_recovery:
            if migration is None or recovery is not None:
                raise invalid("release_schema_route_required")
            transition = schema_upgrade(x, y, migration)
    elif migration is not None:
        raise invalid("release_schema_route_unnecessary")
    # Both versions must understand precisely the same persisted startup gate.
    # An earlier executable without this contract cannot safely be retired.
    for name in FENCE_FILES:
        left, right = _runtime_files(x), _runtime_files(y)
        if name not in left or left.get(name) != right.get(name):
            raise invalid("release_fence_contract_missing")
    supplied = deployment_fields(paths, {"backup", "journal"})
    windows = x["platform"] == "win32"
    locations = {key: deployment_path(value, windows) for key, value in supplied.items()}
    pathmod = ntpath if windows else posixpath

    def overlaps(left: str, right: str) -> bool:
        left, right = pathmod.normcase(left), pathmod.normcase(right)
        try:
            return pathmod.commonpath([left, right]) in {left, right}
        except ValueError:
            return False

    mutable: list[str] = [cast(str, a["home"]), cast(str, b["home"]), *locations.values()]
    for index, left in enumerate(mutable):
        if any(overlaps(left, right) for right in mutable[index + 1 :]):
            raise invalid("release_paths_overlap")
    immutable: list[str] = [
        cast(str, a["bundle_dir"]),
        cast(str, b["bundle_dir"]),
        cast(str, x["python_prefix"]),
        cast(str, y["python_prefix"]),
        cast(str, x["package_root"]),
        cast(str, y["package_root"]),
    ]
    if any(overlaps(left, right) for left in mutable for right in immutable):
        raise invalid("release_paths_overlap")
    if (
        overlaps(cast(str, a["bundle_dir"]), cast(str, b["bundle_dir"]))
        or overlaps(cast(str, x["python_prefix"]), cast(str, y["python_prefix"]))
        or overlaps(cast(str, x["package_root"]), cast(str, y["package_root"]))
    ):
        raise invalid("release_installations_not_separate")
    if any(
        overlaps(cast(str, bundle), code) for bundle in (a["bundle_dir"], b["bundle_dir"]) for code in immutable[2:]
    ):
        raise invalid("release_paths_overlap")
    restore = None
    stop = None if source_stop is None else validate_control(source_stop)
    if stop is not None and (stop["deployment"] != old or stop["action"] != "stop"):
        raise invalid("release_source_stop_changed")
    if recovery is not None:
        record = deployment_fields(
            cast(object, recovery),
            {"release_sha256", "snapshot", "manifest_sha256", "isolation", "restore_policy", "expected_source_witness"},
        )
        if record["restore_policy"] not in ("original_snapshot", "latest_failed"):
            raise invalid("release_restore_policy_invalid")
        authority = None if record["isolation"] is None else validate_authority(record["isolation"], windows)
        restore = cast(
            RecoverySpec,
            {
                "release_sha256": deployment_sha(record["release_sha256"]),
                "snapshot": deployment_path(record["snapshot"], windows),
                "manifest_sha256": deployment_sha(record["manifest_sha256"]),
                "isolation": authority,
                "restore_policy": record["restore_policy"],
                "expected_source_witness": validate_witness(record["expected_source_witness"]),
            },
        )
        if authority is not None:
            if restore["restore_policy"] != "latest_failed":
                raise invalid("release_rescue_requires_latest_data")
            if stop is not None:
                raise invalid("release_isolated_source_stop_forbidden")
            if (
                authority["release_sha256"] != restore["release_sha256"]
                or authority["source_deployment_sha256"] != old["sha256"]
                or not new["isolated_registration"]
                or manager_identity(old)["name"] == manager_identity(new)["name"]
            ):
                raise invalid("release_isolation_changed")
            if any(overlaps(authority["exchange_journal"], item) for item in (*mutable, *immutable)):
                raise invalid("release_paths_overlap")
        if any(overlaps(restore["snapshot"], item) for item in (*mutable, *immutable)):
            raise invalid("release_paths_overlap")
    value: dict[str, object] = {
        "format": FORMAT,
        "id": deployment_uuid(identity),
        "created_at": deployment_created(at),
        "source": old,
        "target": new,
        "paths": locations,
        "recovery": restore,
        "source_stop": stop,
        "schema_upgrade": transition,
        "starts_service": False,
        "stops_service": stop is not None,
        "overwrites_source": False,
    }
    return cast(ReleasePlan, {**value, "sha256": digest(value)})


def validate_release(value: object) -> ReleasePlan:
    record = deployment_fields(
        value,
        {
            "format",
            "id",
            "created_at",
            "source",
            "target",
            "paths",
            "recovery",
            "source_stop",
            "schema_upgrade",
            "starts_service",
            "stops_service",
            "overwrites_source",
            "sha256",
        },
    )
    expected = make_release(
        record["source"],
        record["target"],
        record["paths"],
        record["id"],
        record["created_at"],
        record["recovery"],
        record["source_stop"],
        record["schema_upgrade"],
    )
    if record != expected or any(
        type(record[key]) is not bool for key in ("starts_service", "stops_service", "overwrites_source")
    ):
        raise invalid("release_plan_changed")
    return expected
