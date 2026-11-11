"""Reviewed same-schema service exchanges and ordered witnesses; no platform, process or storage IO."""

from __future__ import annotations

import ntpath
import posixpath
from collections.abc import Mapping, Sequence
from typing import Any, TypedDict, cast

from .contracts import BusinessError
from .releases import ReleasePlan, digest, validate_release
from .service_activation import activation_binding, reservation
from .service_control import ControlPlan, validate_control
from .service_deployment import (
    deployment_created,
    deployment_fields,
    deployment_path,
    deployment_sha,
    deployment_uuid,
)
from .service_operations import validate_operation

FORMAT = "omni-service-exchange-4"
PHASE_FORMAT = "omni-service-admin-phase-1"
ADMIN_PROTOCOL = "omni-service-admin-4"
STAGES = ("remove_source", "select_target", "install_target", "start_target")


class ExchangePlan(TypedDict):
    format: str
    id: str
    created_at: str
    release: ReleasePlan
    remove_source: dict[str, Any]
    install_target: dict[str, Any]
    start_target: ControlPlan
    journal: str
    starts_service: bool
    stops_service: bool
    releases_activation_on_abandon: bool
    automatic_rollback: bool
    administrators: dict[str, Any] | None
    sha256: str


def invalid(code: str = "service_exchange_invalid") -> BusinessError:
    return BusinessError(code, "Review the exact prepared release, ordered operations and reserved target boot")


def make_exchange(
    release: object,
    removal: object,
    installation: object,
    start: object,
    journal: object,
    identity: object,
    at: object,
    administrators: object = None,
) -> ExchangePlan:
    plan = validate_release(release)
    system = plan["source"]["request"]["target"] in {"linux-system", "macos-system"}
    if (
        system
        and administrators is None
        and plan["source"]["runtime"]["schema_head"] != plan["target"]["runtime"]["schema_head"]
    ):
        raise invalid("service_exchange_requires_schema_maintenance")
    remove, install, control = validate_operation(removal), validate_operation(installation), validate_control(start)
    if plan["source"]["request"]["target"] not in {
        "windows-user",
        "linux-user",
        "macos-user",
        "linux-system",
        "macos-system",
    }:
        raise invalid("service_exchange_target_unimplemented")
    if (
        remove["action"] != "uninstall"
        or remove["deployment"] != plan["source"]
        or install["action"] != "install"
        or install["deployment"] != plan["target"]
        or control["action"] != "start"
        or control["deployment"] != plan["target"]
    ):
        raise invalid("service_exchange_operation_changed")
    ids = [
        deployment_uuid(identity),
        plan["id"],
        remove["id"],
        install["id"],
        control["id"],
        control["boot_id"],
    ]
    participants: dict[str, dict[str, str]] | None = None
    if administrators is not None:
        if not system:
            raise invalid("service_exchange_administrators_invalid")
        supplied = deployment_fields(administrators, {"source", "target"})
        participants = {}
        for role in ("source", "target"):
            item = deployment_fields(supplied[role], {"id", "protocol", "bundle_sha256"})
            if item["protocol"] != ADMIN_PROTOCOL:
                raise invalid("service_exchange_admin_protocol_unsupported")
            participants[role] = {
                "id": deployment_uuid(item["id"]),
                "protocol": ADMIN_PROTOCOL,
                "bundle_sha256": deployment_sha(item["bundle_sha256"]),
            }
            ids.append(participants[role]["id"])
    if len(set(ids)) != len(ids):
        raise invalid("service_exchange_identity_reused")
    windows = plan["target"]["runtime"]["platform"] == "win32"
    location = deployment_path(journal, windows)
    pathmod = ntpath if windows else posixpath
    protected = [*plan["paths"].values()]
    if plan["recovery"] is not None:
        protected.append(plan["recovery"]["snapshot"])
    for side in ("source", "target"):
        protected += [plan[side]["request"][key] for key in ("home", "bundle_dir", "working_directory")]
        protected += [plan[side]["runtime"][key] for key in ("python_prefix", "package_root")]
    for other in protected:
        a, b = pathmod.normcase(location), pathmod.normcase(other)
        try:
            common = pathmod.commonpath([a, b])
        except ValueError:
            continue
        if common in {a, b}:
            raise invalid("service_exchange_journal_unsafe")
    value = {
        "format": FORMAT,
        "id": ids[0],
        "created_at": deployment_created(at),
        "release": plan,
        "remove_source": remove,
        "install_target": install,
        "start_target": control,
        "journal": location,
        "starts_service": True,
        "stops_service": remove["stops_service"],
        "releases_activation_on_abandon": plan["target"]["request"]["target"]
        in {"macos-user", "linux-system", "macos-system"},
        "automatic_rollback": False,
        "administrators": participants,
    }
    return cast(ExchangePlan, {**value, "sha256": digest(value)})


def validate_exchange(value: object) -> ExchangePlan:
    record = deployment_fields(
        value,
        {
            "format",
            "id",
            "created_at",
            "release",
            "remove_source",
            "install_target",
            "start_target",
            "journal",
            "starts_service",
            "stops_service",
            "releases_activation_on_abandon",
            "automatic_rollback",
            "administrators",
            "sha256",
        },
    )
    expected = make_exchange(
        record["release"],
        record["remove_source"],
        record["install_target"],
        record["start_target"],
        record["journal"],
        record["id"],
        record["created_at"],
        record["administrators"],
    )
    if record != expected or any(
        type(record[key]) is not bool
        for key in ("starts_service", "stops_service", "releases_activation_on_abandon", "automatic_rollback")
    ):
        raise invalid("service_exchange_changed")
    return expected


def marker(plan: ExchangePlan) -> dict[str, object]:
    binding = activation_binding(plan["start_target"]) if plan["releases_activation_on_abandon"] else None
    return reservation(plan["id"], plan["sha256"], binding)


def stages(plan: ExchangePlan) -> tuple[str, ...]:
    return (
        ("remove_source", "select_target", "close_source", "install_target", "start_target", "close_target")
        if plan["administrators"] is not None
        else STAGES
    )


def administrator_approval(plan: object, role: object) -> dict[str, object]:
    checked = validate_exchange(plan)
    if checked["administrators"] is None or not isinstance(role, str) or role not in {"source", "target"}:
        raise invalid("service_exchange_administrators_invalid")
    participant = checked["administrators"][role]
    content = {
        "format": PHASE_FORMAT,
        "id": participant["id"],
        "role": role,
        "protocol": participant["protocol"],
        "broker_sha256": participant["bundle_sha256"],
        "exchange": checked,
    }
    return {**content, "sha256": digest(content)}


def validate_administrator_approval(value: object) -> dict[str, object]:
    record = deployment_fields(value, {"format", "id", "role", "protocol", "broker_sha256", "exchange", "sha256"})
    if record != administrator_approval(record["exchange"], record["role"]):
        raise invalid("service_exchange_administrator_changed")
    return record


def stage_identity(plan: ExchangePlan, stage: str) -> tuple[str, str]:
    if stage in {"close_source", "close_target"}:
        item = administrator_approval(plan, "source" if stage == "close_source" else "target")
        return cast(str, item["id"]), cast(str, item["sha256"])
    item = {
        "select_target": plan["release"],
        "remove_source": plan["remove_source"],
        "install_target": plan["install_target"],
        "start_target": plan["start_target"],
    }.get(stage)
    if not isinstance(item, dict):
        raise invalid()
    return str(item["id"]), str(item["sha256"])


def checked_proof(plan: ExchangePlan, stage: str, value: object) -> dict[str, object]:
    proof = deployment_fields(
        value,
        {
            "stage",
            "operation_id",
            "operation_sha256",
            "succeeded",
            "cleanup_complete",
            "error_code",
            "verification",
        },
    )
    identity, sha = stage_identity(plan, stage)
    if (
        proof["stage"] != stage
        or proof["operation_id"] != identity
        or proof["operation_sha256"] != sha
        or type(proof["succeeded"]) is not bool
        or type(proof["cleanup_complete"]) is not bool
        or any(
            not isinstance(proof[key], str) or len(cast(str, proof[key])) > 128
            for key in ("error_code", "verification")
        )
        or (proof["succeeded"] and (proof["error_code"] or not proof["verification"]))
    ):
        raise invalid("service_exchange_proof_changed")
    return proof


def progress(plan: ExchangePlan, history: Sequence[Mapping[str, object]]) -> tuple[int, str, str]:
    """Validate every transition before deriving completed count and the in-flight stage."""
    completed, active, last = 0, "", ""
    ordered = stages(plan)
    for index, row in enumerate(history):
        if set(row) != {"state", "stage", "recorded_at", "proof"}:
            raise invalid("service_exchange_journal_invalid")
        deployment_created(row["recorded_at"])
        state = cast(str, row["state"])
        stage = cast(str, row["stage"])
        proof = row["proof"]
        if last in {"finished", "abandoned", "isolated"}:
            raise invalid("service_exchange_journal_invalid")
        if last == "isolating" and state != "isolated":
            raise invalid("service_exchange_journal_invalid")
        if index == 0:
            valid = state == "intent" and stage == "" and proof == {}
        elif state == "dispatch":
            valid = completed < len(ordered) and not active and stage == ordered[completed] and proof == {}
            if valid:
                active = stage
        elif state == "awaiting_administrator":
            valid = (
                plan["administrators"] is not None
                and completed < len(ordered)
                and not active
                and stage == ordered[completed]
                and proof == {}
            )
        elif state in {"complete", "blocked"}:
            valid = bool(active) and stage == active
            if valid:
                checked = checked_proof(plan, stage, proof)
                success = bool(checked["succeeded"]) and bool(checked["cleanup_complete"])
                valid = success if state == "complete" else not success
                if valid and state == "complete":
                    completed, active = completed + 1, ""
        elif state == "finished":
            valid = completed == len(ordered) and not active and stage == "" and proof == {}
        elif state == "abandoned":
            valid = stage == "" and proof == {}
        elif state in {"isolating", "isolated"}:
            from .service_isolation import isolation_reference

            checked = isolation_reference(proof)
            valid = (
                stage == "" and checked["exchange_id"] == plan["id"] and checked["exchange_sha256"] == plan["sha256"]
            )
            if state == "isolating":
                valid = (
                    valid
                    and last != "isolating"
                    and checked["event_count"] == index
                    and checked["history_sha256"] == digest(history[:index])
                )
            else:
                valid = valid and last == "isolating" and proof == history[index - 1]["proof"]
        else:
            valid = False
        if not valid:
            raise invalid("service_exchange_journal_invalid")
        last = state
    return completed, active, last
