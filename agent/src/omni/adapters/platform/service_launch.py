"""Once-only reviewed boot reservations under the instance lock; never starts a process or reads credentials."""

from __future__ import annotations

import uuid
from pathlib import Path

from ...domain.contracts import BusinessError
from ...domain.service_activation import activation_binding, validate_reservation
from ...domain.service_control import ControlPlan, validate_control
from .deployment_files import CurrentDeploymentFiles, _decode, _json, _no_links, _read
from .permissions import assert_private, private_path


def _activation(control: Path) -> dict | None:
    path = control / "service-exchange.json"
    _no_links(path)
    if not path.exists():
        return None
    assert_private(path)
    marker = validate_reservation(_decode(_read(path, 8192, single=True)))
    binding = marker["activation"]
    if binding is not None and Path(binding["home"]) != control.parent:
        raise BusinessError("service_activation_source_blocked", "The exchange excludes unattended source startup")
    return binding


def _require_activation(control: Path, operation: ControlPlan) -> None:
    binding = _activation(control)
    if binding is not None and activation_binding(operation) != binding:
        raise BusinessError("service_activation_mismatch", "Only the reserved exchange boot may start this home")


def consume_launch(control: Path, deployment: dict | None) -> str:
    binding = _activation(control)
    path = control / "launch-ticket.json"
    _no_links(path)
    if not path.exists():
        if binding is not None:
            raise BusinessError("service_activation_pending", "Exchange startup awaits its reviewed launch reservation")
        return str(uuid.uuid4())
    assert_private(path)
    files = CurrentDeploymentFiles()
    operation = validate_control(files.read(path))
    _require_activation(control, operation)
    if (
        deployment is None
        or operation["action"] != "start"
        or operation["deployment"]["sha256"] != deployment["service_plan_sha256"]
        or operation["deployment"]["request"]["instance_id"] != deployment["instance_id"]
    ):
        raise BusinessError("service_launch_mismatch", "The startup reservation belongs to another reviewed deployment")
    folder = control / "service-launches"
    _no_links(folder)
    if not folder.exists():
        folder.mkdir(mode=0o700)
        private_path(folder, directory=True)
    assert_private(folder)
    receipt = folder / (operation["id"] + ".json")
    if receipt.exists():
        raise BusinessError(
            "service_launch_consumed", "This startup was already claimed; reconcile instead of replaying"
        )
    files._publish_json(
        receipt,
        _json(
            {
                "operation_id": operation["id"],
                "operation_sha256": operation["sha256"],
                "boot_id": operation["boot_id"],
                "service_plan_sha256": operation["deployment"]["sha256"],
            }
        ),
    )
    return operation["boot_id"]


def reserve_launch(control: Path, operation: ControlPlan) -> None:
    operation = validate_control(operation)
    if operation["action"] != "start":
        raise BusinessError("service_launch_mismatch", "Only a reviewed start may reserve a boot")
    _require_activation(control, operation)
    CurrentDeploymentFiles()._publish_json(control / "launch-ticket.json", _json(dict(operation)))
