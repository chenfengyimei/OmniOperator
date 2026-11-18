"""Assemble reviewed lifecycle commands, with no operation during import or plan inspection."""

from __future__ import annotations

from pathlib import Path
from typing import cast

from omni.adapters.platform.services.deployment_files import CurrentDeploymentFiles
from omni.adapters.platform.services.service_control_files import ServiceControlFiles
from omni.adapters.platform.services.service_readiness import ServiceReadiness
from omni.adapters.platform.services.service_registration import ServiceRegistration

from ..adapters.primitives import SystemClock, UUIDs
from ..application.ports import Record, ServiceStarterPort
from ..application.service_control import ServiceControl
from ..domain.contracts import BusinessError
from ..domain.service_admin import admin_operation
from .lifecycle import close_until_quiescent


async def run_control(
    command: str,
    path: Path,
    *,
    action: str = "",
    output: Path | None = None,
    approval: str = "",
    admin_socket: Path | None = None,
    admin_bundle_sha256: str | None = None,
) -> Record:
    deployments, files = CurrentDeploymentFiles(), ServiceControlFiles()
    native: ServiceStarterPort
    if (admin_socket is None) != (admin_bundle_sha256 is None):
        raise BusinessError(
            "service_admin_configuration_invalid", "Supply the reviewed root socket and bundle SHA together"
        )
    if admin_socket is not None and admin_bundle_sha256 is not None:
        if command not in {"apply-control", "reconcile-control"}:
            raise BusinessError(
                "service_admin_configuration_invalid",
                "Root routing applies only to reviewed start execution or observation",
            )
        operation = admin_operation(deployments.read(path.absolute()))
        if operation["action"] != "start":
            raise BusinessError("service_admin_action_unsupported", "Only a reviewed system start may use this route")
        from omni.adapters.platform.services.service_admin_client import PrivilegedServiceClient

        native = PrivilegedServiceClient(cast(Record, operation), str(admin_socket.absolute()), admin_bundle_sha256)
    else:
        native = ServiceRegistration()
    service = ServiceControl(deployments, files, native, ServiceReadiness(), SystemClock(), UUIDs())
    try:
        if command == "control-plan":
            if output is None:
                raise BusinessError("service_control_output_required", "Choose a new private review plan")
            return await service.plan(str(path.absolute()), action, str(output.absolute()))
        operation = deployments.read(path.absolute())
        if command == "inspect-control":
            return {"receipt": files.latest(operation), "executed": False}
        if command in {"apply-control", "reconcile-control", "cancel-control"}:
            return await service.apply(
                operation, approval, reconcile=command == "reconcile-control", cancel=command == "cancel-control"
            )
        raise BusinessError("service_control_command_invalid", "Choose an explicit service lifecycle command")
    finally:

        async def close(budget: float) -> Record:
            return {"complete": await service.close(budget), "residual": ["service_control"]}

        await close_until_quiescent(close, 8)
