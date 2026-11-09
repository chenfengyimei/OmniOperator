"""Assemble one explicit offline service-registration command; production entry remains unchanged."""

from __future__ import annotations

from pathlib import Path

from ..adapters.platform.deployment_files import CurrentDeploymentFiles
from ..adapters.platform.service_operation_files import ServiceOperationFiles
from ..adapters.platform.service_registration import ServiceRegistration
from ..adapters.primitives import SystemClock, UUIDs
from ..application.ports import Record, ServiceRegistrationPort
from ..application.service_operations import ServiceOperations
from ..domain.contracts import BusinessError
from ..domain.service_operations import validate_operation


async def run_operation(
    command: str,
    path: Path,
    *,
    action: str = "",
    output: Path | None = None,
    approval: str = "",
    admin_socket: Path | None = None,
    admin_bundle_sha256: str | None = None,
) -> Record:
    deployments, files = CurrentDeploymentFiles(), ServiceOperationFiles()
    operation = None if command == "operation-plan" else validate_operation(deployments.read(str(path.absolute())))
    native: ServiceRegistrationPort
    if (admin_socket is None) != (admin_bundle_sha256 is None):
        raise BusinessError(
            "service_admin_configuration_invalid",
            "Supply the exact administrator socket and reviewed bundle SHA together",
        )
    if admin_socket is not None and admin_bundle_sha256 is not None:
        from ..adapters.platform.service_admin_client import PrivilegedServiceClient

        if operation is None:
            raise BusinessError(
                "service_admin_configuration_invalid", "Administrator routing applies to an existing operation"
            )
        native = PrivilegedServiceClient(operation, str(admin_socket.absolute()), admin_bundle_sha256)
    else:
        native = ServiceRegistration()
    service = ServiceOperations(deployments, files, native, SystemClock(), UUIDs())
    try:
        if command == "operation-plan":
            if output is None:
                raise BusinessError("service_operation_output_required", "Choose a new private operation plan path")
            return service.plan(str(path.absolute()), action, str(output.absolute()))
        assert operation is not None
        if command == "inspect-operation":
            return {
                "operation_id": operation["id"],
                "sha256": operation["sha256"],
                "receipt": files.latest(operation),
                "executed": False,
            }
        if command == "apply-operation":
            return await service.apply(operation, approval)
        if command == "reconcile-operation":
            return await service.reconcile(operation)
        if command == "cancel-operation":
            return await service.cancel(operation)
        raise BusinessError("invalid_service_operation", "Choose an explicit service operation command", status=422)
    finally:
        if not await service.close(8):
            raise BusinessError(
                "service_operation_cleanup_pending",
                "Native work remains owned; inspect the persisted maintenance journal",
            )
