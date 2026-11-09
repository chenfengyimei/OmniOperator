"""Reviewed immutable release/recovery plans; same-schema first delivery, with no platform or storage IO."""

from __future__ import annotations

import hashlib
import json
import ntpath
import posixpath
from typing import Any, TypedDict, cast

from .contracts import BusinessError
from .recovery_authority import RecoverySpec, validate_authority, validate_witness
from .schema_upgrade import schema_upgrade
from .service_control import ControlPlan, validate_control
from .service_deployment import _created, _object, _path, _sha, _uuid, manager_identity, validate_plan

FORMAT = "omni-release-plan-3"
FENCE_FILES = (
    "domain/release_fence.py",
    "adapters/platform/paths.py",
    "domain/service_activation.py",
    "adapters/platform/service_launch.py",
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


def make_release(
    source: object,
    target: object,
    paths: object,
    identity: str,
    at: str,
    recovery: object = None,
    source_stop: object = None,
    migration: object = None,
) -> ReleasePlan:
    old, new = validate_plan(source), validate_plan(target)
    a, b = old["request"], new["request"]
    x, y = old["runtime"], new["runtime"]
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
        snapshot_recovery = (
            isinstance(recovery, dict)
            and recovery.get("restore_policy") == "original_snapshot"
            and recovery.get("isolation") is None
            and migration is None
            and x["schema_head"] > y["schema_head"]
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
        rows = [{row["path"]: row for row in runtime["files"]} for runtime in (x, y)]
        if name not in rows[0] or rows[0].get(name) != rows[1].get(name):
            raise invalid("release_fence_contract_missing")
    supplied = _object(paths, {"backup", "journal"})
    windows = x["platform"] == "win32"
    locations = {key: _path(value, windows) for key, value in supplied.items()}
    pathmod = ntpath if windows else posixpath

    def overlaps(left: str, right: str) -> bool:
        left, right = pathmod.normcase(left), pathmod.normcase(right)
        try:
            return pathmod.commonpath([left, right]) in {left, right}
        except ValueError:
            return False

    mutable = [a["home"], b["home"], *locations.values()]
    for index, left in enumerate(mutable):
        if any(overlaps(left, right) for right in mutable[index + 1 :]):
            raise invalid("release_paths_overlap")
    immutable = [
        a["bundle_dir"],
        b["bundle_dir"],
        x["python_prefix"],
        y["python_prefix"],
        x["package_root"],
        y["package_root"],
    ]
    if any(overlaps(left, right) for left in mutable for right in immutable):
        raise invalid("release_paths_overlap")
    if (
        overlaps(a["bundle_dir"], b["bundle_dir"])
        or overlaps(x["python_prefix"], y["python_prefix"])
        or overlaps(x["package_root"], y["package_root"])
    ):
        raise invalid("release_installations_not_separate")
    if any(overlaps(bundle, code) for bundle in (a["bundle_dir"], b["bundle_dir"]) for code in immutable[2:]):
        raise invalid("release_paths_overlap")
    restore = None
    stop = None if source_stop is None else validate_control(source_stop)
    if stop is not None and (stop["deployment"] != old or stop["action"] != "stop"):
        raise invalid("release_source_stop_changed")
    if recovery is not None:
        record = _object(
            recovery,
            {"release_sha256", "snapshot", "manifest_sha256", "isolation", "restore_policy", "expected_source_witness"},
        )
        if record["restore_policy"] not in ("original_snapshot", "latest_failed"):
            raise invalid("release_restore_policy_invalid")
        authority = None if record["isolation"] is None else validate_authority(record["isolation"], windows)
        restore = cast(
            RecoverySpec,
            {
                "release_sha256": _sha(record["release_sha256"]),
                "snapshot": _path(record["snapshot"], windows),
                "manifest_sha256": _sha(record["manifest_sha256"]),
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
    value = {
        "format": FORMAT,
        "id": _uuid(identity),
        "created_at": _created(at),
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
    record = _object(
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
