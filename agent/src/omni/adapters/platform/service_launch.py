"""Once-only reviewed boot reservations under the instance lock; never starts a process or reads credentials."""

from __future__ import annotations

import uuid
from pathlib import Path
from typing import cast

from ...application.ports import Record
from ...domain.contracts import BusinessError
from ...domain.service_activation import activation_binding, validate_reservation
from ...domain.service_control import ControlPlan, validate_control
from .deployment_files import CurrentDeploymentFiles, decode, json_bytes, no_links, publish_json, read_file
from .permissions import assert_private, private_path


def _activation(control: Path) -> Record | None:
    path = control / "service-exchange.json"
    no_links(path)
    if not path.exists():
        return None
    assert_private(path)
    marker = validate_reservation(decode(read_file(path, 8192, single=True)))
    binding = marker["activation"]
    if binding is not None and Path(str(cast(Record, binding)["home"])) != control.parent:
        raise BusinessError("service_activation_source_blocked", "The exchange excludes unattended source startup")
    return cast(Record | None, binding)


def _require_activation(control: Path, operation: ControlPlan) -> None:
    binding = _activation(control)
    if binding is not None and activation_binding(operation) != binding:
        raise BusinessError("service_activation_mismatch", "Only the reserved exchange boot may start this home")


def consume_launch(control: Path, deployment: Record | None) -> str:
    binding = _activation(control)
    path = control / "launch-ticket.json"
    no_links(path)
    if not path.exists():
        if binding is not None:
            raise BusinessError("service_activation_pending", "Exchange startup awaits its reviewed launch reservation")
        return str(uuid.uuid4())
    assert_private(path)
    files = CurrentDeploymentFiles()
    operation = validate_control(files.read(path))
    _require_activation(control, operation)
    plan = cast(Record, operation["deployment"])
    request = cast(Record, plan["request"])
    if (
        deployment is None
        or operation["action"] != "start"
        or plan["sha256"] != deployment["service_plan_sha256"]
        or request["instance_id"] != deployment["instance_id"]
    ):
        raise BusinessError("service_launch_mismatch", "The startup reservation belongs to another reviewed deployment")
    folder = control / "service-launches"
    no_links(folder)
    if not folder.exists():
        folder.mkdir(mode=0o700)
        private_path(folder, directory=True)
    assert_private(folder)
    receipt = folder / (operation["id"] + ".json")
    if receipt.exists():
        raise BusinessError(
            "service_launch_consumed", "This startup was already claimed; reconcile instead of replaying"
        )
    publish_json(
        files,
        receipt,
        json_bytes(
            {
                "operation_id": operation["id"],
                "operation_sha256": operation["sha256"],
                "boot_id": operation["boot_id"],
                "service_plan_sha256": plan["sha256"],
            }
        ),
    )
    return operation["boot_id"]


def reserve_launch(control: Path, operation: ControlPlan) -> None:
    operation = validate_control(operation)
    if operation["action"] != "start":
        raise BusinessError("service_launch_mismatch", "Only a reviewed start may reserve a boot")
    _require_activation(control, operation)
    publish_json(CurrentDeploymentFiles(), control / "launch-ticket.json", json_bytes(cast(Record, dict(operation))))
