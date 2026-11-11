"""Fingerprint offline service changes; registration never implies a healthy running service."""

from __future__ import annotations

import hashlib
import json
from collections.abc import Mapping

from .contracts import BusinessError
from .service_deployment import _created, _object, _sha, _uuid, manager_identity, validate_plan

FORMAT = "omni-service-operation-2"
ACTIONS = frozenset({"install", "uninstall"})


def operation_steps(plan: dict, action: str) -> list[str]:
    target = plan["request"]["target"]
    steps = ["verify_immutable_deployment", "lock_initialized_instance", "persist_intent", "inspect_registration"]
    if target == "windows-user":
        steps += ["create_task_only" if action == "install" else "delete_matching_idle_task"]
    elif target in {"linux-user", "linux-system"}:
        if target == "linux-system":
            steps += ["verify_explicit_root_broker", "persist_root_dispatch_audit"]
        steps += (
            ["publish_unit_without_overwrite", "reload_manager", "enable_without_start"]
            if action == "install"
            else ["disable_without_stop", "remove_matching_unit", "reload_manager"]
        )
    elif target in {"macos-user", "macos-system"}:
        if target == "macos-system":
            steps += ["verify_explicit_root_broker", "persist_root_dispatch_audit"]
        steps += (
            [
                "verify_system_domain" if target == "macos-system" else "verify_graphical_user_domain",
                "publish_plist_without_overwrite",
                "enable_future_activation",
                "do_not_bootstrap_under_instance_lock",
            ]
            if action == "install"
            else [
                "verify_loaded_definition",
                "disable_future_activation",
                "unload_matching_idle_job",
                "remove_matching_plist",
            ]
        )
    else:
        steps += ["native_registration_not_implemented"]
    return [*steps, "inspect_postcondition", "persist_receipt"]


def make_operation(deployment: object, action: str, id: str, created_at: str) -> dict:
    if action not in ACTIONS:
        raise BusinessError("invalid_service_operation", "Choose install or uninstall", status=422)
    plan = validate_plan(deployment)
    value = {
        "format": FORMAT,
        "id": _uuid(id),
        "created_at": _created(created_at),
        "action": action,
        "deployment": plan,
        "manager": manager_identity(plan),
        "steps": operation_steps(plan, action),
        # macOS may react to a new login item independently of this process;
        # bootout may terminate an idle job that starts between the checks.
        "starts_service": plan["request"]["target"].startswith("macos-") and action == "install",
        "stops_service": plan["request"]["target"].startswith("macos-") and action == "uninstall",
        "deletes_instance_data": False,
    }
    return {**value, "sha256": _fingerprint(value)}


def _fingerprint(value: dict) -> str:
    return hashlib.sha256(
        json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=False, allow_nan=False).encode("utf-8")
    ).hexdigest()


def validate_operation(data: object) -> dict:
    value = _object(
        data,
        {
            "format",
            "id",
            "created_at",
            "action",
            "deployment",
            "manager",
            "steps",
            "starts_service",
            "stops_service",
            "deletes_instance_data",
            "sha256",
        },
    )
    if (
        value["format"] != FORMAT
        or not isinstance(value["action"], str)
        or any(type(value[key]) is not bool for key in ("starts_service", "stops_service", "deletes_instance_data"))
    ):
        raise BusinessError("invalid_service_operation", "Service operation format is invalid", status=422)
    expected = make_operation(value["deployment"], value["action"], value["id"], value["created_at"])
    if _sha(value["sha256"]) != expected["sha256"] or value != expected:
        raise BusinessError("service_operation_changed", "Review the exact service operation fingerprint")
    return expected


def registration_goal(action: str, report: Mapping[str, object]) -> bool:
    if report.get("available") is not True:
        return False
    if action == "install":
        return (
            report.get("installed") is True
            and report.get("definition_matches") is True
            and report.get("enabled") is True
        )
    return (
        report.get("installed") is False
        and report.get("definition_present") is False
        and report.get("state") == "stopped"
    )


def registration_precondition(action: str, report: dict) -> bool:
    # Unknown manager output is neither absence nor a safe removal target.
    if report.get("available") is not True or report.get("state") != "stopped":
        return False
    if action == "install":
        return report.get("installed") is False and report.get("definition_present") is False
    return report.get("installed") is True and report.get("definition_matches") is True
