"""Bind unattended exchange activation to one reviewed boot; pure persisted contracts with no platform IO."""

from __future__ import annotations

from typing import cast

from .contracts import BusinessError
from .service_control import digest, validate_control
from .service_deployment import deployment_fields, deployment_path, deployment_sha, deployment_uuid

EXCHANGE_FORMAT = "omni-service-exchange-3"
ACTIVATION_FORMAT = "omni-service-activation-1"


def invalid() -> BusinessError:
    return BusinessError("service_activation_changed", "Preserve the exchange and its exact reserved target boot")


def activation_binding(operation: object) -> dict[str, object]:
    value = validate_control(operation)
    deployment = value["deployment"]
    request = cast(dict[str, object], deployment["request"])
    target = request.get("target")
    if value["action"] != "start" or target not in {
        "macos-user",
        "linux-system",
        "macos-system",
    }:
        raise invalid()
    return {
        "format": ACTIVATION_FORMAT,
        "home": str(request["home"]),
        "instance_id": str(request["instance_id"]),
        "service_plan_sha256": str(deployment["sha256"]),
        "operation_id": value["id"],
        "operation_sha256": value["sha256"],
        "boot_id": value["boot_id"],
    }


def validate_binding(value: object) -> dict[str, object]:
    record = deployment_fields(
        value,
        {
            "format",
            "home",
            "instance_id",
            "service_plan_sha256",
            "operation_id",
            "operation_sha256",
            "boot_id",
        },
    )
    expected: dict[str, object] = {
        "format": ACTIVATION_FORMAT,
        "home": deployment_path(record["home"], False),
        "instance_id": deployment_uuid(record["instance_id"]),
        "service_plan_sha256": deployment_sha(record["service_plan_sha256"]),
        "operation_id": deployment_uuid(record["operation_id"]),
        "operation_sha256": deployment_sha(record["operation_sha256"]),
        "boot_id": deployment_uuid(record["boot_id"]),
    }
    if record != expected:
        raise invalid()
    return expected


def reservation(identity: str, sha256: str, activation: object = None) -> dict[str, object]:
    content = {
        "format": EXCHANGE_FORMAT,
        "exchange_id": deployment_uuid(identity),
        "sha256": deployment_sha(sha256),
        "activation": None if activation is None else validate_binding(activation),
    }
    return {**content, "reservation_sha256": digest(content)}


def validate_reservation(value: object) -> dict[str, object]:
    record = deployment_fields(value, {"format", "exchange_id", "sha256", "activation", "reservation_sha256"})
    expected = reservation(str(record["exchange_id"]), str(record["sha256"]), record["activation"])
    if record != expected:
        raise invalid()
    return expected
