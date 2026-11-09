"""Exact quarantined-registration cleanup plans; no filesystem, native calls or reinterpretation of old effects."""

from __future__ import annotations

import ntpath
import posixpath
from typing import Any, TypedDict, cast

from .contracts import BusinessError
from .releases import digest
from .service_deployment import _created, _integer, _object, _path, _sha, _uuid
from .service_exchange import ExchangePlan, validate_exchange
from .service_isolation import isolation_reference
from .service_operations import make_operation, validate_operation

FORMAT = "omni-service-cleanup-1"


class CleanupPlan(TypedDict):
    format: str
    id: str
    created_at: str
    exchange: ExchangePlan
    event_count: int
    history_sha256: str
    isolation: dict[str, Any]
    side: str
    operation: dict[str, Any]
    journal: str
    starts_service: bool
    stops_service: bool
    deletes_instance_data: bool
    preserves_original_records: bool
    late_effects_possible: bool
    sha256: str


def invalid(code: str = "service_cleanup_invalid") -> BusinessError:
    return BusinessError(code, "Review the exact isolated exchange, selected definition and separate cleanup receipt")


def registry(exchange: ExchangePlan) -> str:
    pathmod = ntpath if exchange["release"]["target"]["runtime"]["platform"] == "win32" else posixpath
    root = exchange["journal"]
    return pathmod.join(pathmod.dirname(root), "." + pathmod.basename(root) + ".cleanup")


def registration_key(operation: dict) -> str:
    manager = operation["manager"]
    return digest({key: manager[key] for key in ("manager", "domain", "name", "scope")})


def protected_paths(exchange: ExchangePlan) -> list[str]:
    release = exchange["release"]
    paths = [exchange["journal"], registry(exchange), *release["paths"].values()]
    if release["recovery"] is not None:
        paths.append(release["recovery"]["snapshot"])
        authority = release["recovery"]["isolation"]
        if authority is not None:
            paths.append(authority["exchange_journal"])
    for side in ("source", "target"):
        plan = release[side]
        paths += [plan["request"][key] for key in ("home", "working_directory", "bundle_dir")]
        paths += [plan["runtime"][key] for key in ("package_root", "python_prefix")]
    return paths


def overlaps(left: str, right: str, windows: bool) -> bool:
    pathmod = ntpath if windows else posixpath
    left, right = pathmod.normcase(left), pathmod.normcase(right)
    try:
        return pathmod.commonpath([left, right]) in {left, right}
    except ValueError:
        return False


def make_cleanup(
    exchange: object,
    count: int,
    history_sha: str,
    isolation: object,
    side: str,
    identity: str,
    operation_id: str,
    at: str,
) -> CleanupPlan:
    original, ref = validate_exchange(exchange), isolation_reference(isolation)
    if not isinstance(side, str) or side not in {"source", "target"}:
        raise invalid()
    count = _integer(count, 3, 64)
    if (
        ref["exchange_id"] != original["id"]
        or ref["exchange_sha256"] != original["sha256"]
        or count != ref["event_count"] + 2
    ):
        raise invalid("service_cleanup_isolation_changed")
    identity, operation_id = _uuid(identity), _uuid(operation_id)
    existing = {original["id"], original["release"]["id"], ref["isolation_id"]}
    existing.update(original[key]["id"] for key in ("remove_source", "install_target", "start_target"))
    existing.add(original["start_target"]["boot_id"])
    if identity == operation_id or {identity, operation_id} & existing:
        raise invalid("service_cleanup_identity_reused")
    operation = make_operation(original["release"][side], "uninstall", operation_id, at)
    windows = original["release"]["target"]["runtime"]["platform"] == "win32"
    pathmod = ntpath if windows else posixpath
    root = registry(original)
    if any(overlaps(root, path, windows) for index, path in enumerate(protected_paths(original)) if index != 1):
        raise invalid("service_cleanup_registry_unsafe")
    value = {
        "format": FORMAT,
        "id": identity,
        "created_at": _created(at),
        "exchange": original,
        "event_count": count,
        "history_sha256": _sha(history_sha),
        "isolation": ref,
        "side": side,
        "operation": operation,
        "journal": _path(pathmod.join(root, registration_key(operation), identity), windows),
        "starts_service": False,
        "stops_service": operation["stops_service"],
        "deletes_instance_data": False,
        "preserves_original_records": True,
        "late_effects_possible": True,
    }
    return cast(CleanupPlan, {**value, "sha256": digest(value)})


def validate_cleanup(value: object) -> CleanupPlan:
    record = _object(
        value,
        {
            "format",
            "id",
            "created_at",
            "exchange",
            "event_count",
            "history_sha256",
            "isolation",
            "side",
            "operation",
            "journal",
            "starts_service",
            "stops_service",
            "deletes_instance_data",
            "preserves_original_records",
            "late_effects_possible",
            "sha256",
        },
    )
    operation = validate_operation(record["operation"])
    expected = make_cleanup(
        record["exchange"],
        record["event_count"],
        record["history_sha256"],
        record["isolation"],
        record["side"],
        record["id"],
        operation["id"],
        record["created_at"],
    )
    if record != expected or any(
        type(record[key]) is not bool
        for key in (
            "starts_service",
            "stops_service",
            "deletes_instance_data",
            "preserves_original_records",
            "late_effects_possible",
        )
    ):
        raise invalid("service_cleanup_changed")
    return expected
