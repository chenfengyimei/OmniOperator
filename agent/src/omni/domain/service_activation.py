"""Bind unattended exchange activation to one reviewed boot; pure persisted contracts with no platform IO."""

from __future__ import annotations

from .contracts import BusinessError
from .service_control import digest, validate_control
from .service_deployment import _object, _path, _sha, _uuid

EXCHANGE_FORMAT = "omni-service-exchange-3"
ACTIVATION_FORMAT = "omni-service-activation-1"


def invalid() -> BusinessError:
    return BusinessError("service_activation_changed", "Preserve the exchange and its exact reserved target boot")


def activation_binding(operation: object) -> dict:
    value = validate_control(operation)
    deployment = value["deployment"]
    if value["action"] != "start" or deployment["request"]["target"] not in {
        "macos-user",
        "linux-system",
        "macos-system",
    }:
        raise invalid()
    return {
        "format": ACTIVATION_FORMAT,
        "home": deployment["request"]["home"],
        "instance_id": deployment["request"]["instance_id"],
        "service_plan_sha256": deployment["sha256"],
        "operation_id": value["id"],
        "operation_sha256": value["sha256"],
        "boot_id": value["boot_id"],
    }


def validate_binding(value: object) -> dict:
    record = _object(
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
    expected = {
        "format": ACTIVATION_FORMAT,
        "home": _path(record["home"], False),
        "instance_id": _uuid(record["instance_id"]),
        "service_plan_sha256": _sha(record["service_plan_sha256"]),
        "operation_id": _uuid(record["operation_id"]),
        "operation_sha256": _sha(record["operation_sha256"]),
        "boot_id": _uuid(record["boot_id"]),
    }
    if record != expected:
        raise invalid()
    return expected


def reservation(identity: str, sha256: str, activation: object = None) -> dict:
    content = {
        "format": EXCHANGE_FORMAT,
        "exchange_id": _uuid(identity),
        "sha256": _sha(sha256),
        "activation": None if activation is None else validate_binding(activation),
    }
    return {**content, "reservation_sha256": digest(content)}


def validate_reservation(value: object) -> dict:
    record = _object(value, {"format", "exchange_id", "sha256", "activation", "reservation_sha256"})
    expected = reservation(record["exchange_id"], record["sha256"], record["activation"])
    if record != expected:
        raise invalid()
    return expected
