"""Assemble quarantined registration cleanup, with an explicit root route for system definitions."""

from __future__ import annotations

from pathlib import Path

from omni.adapters.platform.services.deployment_files import CurrentDeploymentFiles
from omni.adapters.platform.services.exchange_files import ExchangeFiles
from omni.adapters.platform.services.service_cleanup_files import CleanupDeployment, CleanupFiles, CleanupPlanFiles
from omni.adapters.platform.services.service_registration import ServiceRegistration
from ..adapters.primitives import SystemClock, UUIDs
from ..application.ports import Record, ServiceRegistrationPort
from ..application.service_cleanup import CleanupReview, ServiceCleanup
from ..application.service_operations import ServiceOperations
from ..domain.contracts import BusinessError
from ..domain.service_cleanup import validate_cleanup
from .lifecycle import close_until_quiescent


async def run_cleanup(
    command: str,
    path: Path,
    *,
    side: str = "",
    output: Path | None = None,
    approval: str = "",
    admin_socket: Path | None = None,
    admin_bundle_sha256: str | None = None,
) -> Record:
    deployments, clock, ids = CurrentDeploymentFiles(), SystemClock(), UUIDs()
    value = deployments.read(path.absolute())
    if (admin_socket is None) != (admin_bundle_sha256 is None) or (
        admin_socket is not None and command not in {"apply-cleanup", "reconcile-cleanup"}
    ):
        raise BusinessError(
            "service_cleanup_admin_configuration_invalid",
            "Supply the explicit root endpoint and SHA only for cleanup execution or observation",
        )
    if command == "cleanup-plan":
        if output is None:
            raise BusinessError("service_cleanup_output_required", "Choose a new private cleanup review file")
        review = CleanupReview(ExchangeFiles(deployments), CleanupPlanFiles(deployments), clock, ids)
        return review.plan(value, side, str(output.absolute()))
    plan = validate_cleanup(value)
    target = plan["operation"]["deployment"]["request"]["target"]
    files = CleanupFiles(plan, deployments)
    native: ServiceRegistrationPort
    if admin_socket is not None and admin_bundle_sha256 is not None:
        if target not in {"linux-system", "macos-system"}:
            raise BusinessError(
                "service_cleanup_admin_configuration_invalid", "Root cleanup binds only a reviewed system definition"
            )
        from omni.adapters.platform.services.service_admin_client import PrivilegedServiceClient

        native = PrivilegedServiceClient(plan["operation"], str(admin_socket.absolute()), admin_bundle_sha256)
    else:
        if target in {"linux-system", "macos-system"} and command in {"apply-cleanup", "reconcile-cleanup"}:
            raise BusinessError(
                "service_cleanup_admin_required", "Approve the nested system uninstall separately at the root broker"
            )
        native = ServiceRegistration()
    operations = ServiceOperations(CleanupDeployment(files), files, native, clock, ids)
    service = ServiceCleanup(plan, files, operations)
    try:
        if command == "inspect-cleanup":
            return service.inspect()
        if command == "apply-cleanup":
            return await service.apply(approval)
        if command == "reconcile-cleanup":
            return await service.reconcile()
        if command == "cancel-cleanup":
            return await service.cancel()
        raise BusinessError("service_cleanup_command_invalid", "Choose an explicit cleanup or read-only observation")
    finally:

        async def close(budget: float) -> Record:
            return {"complete": await service.close(budget), "residual": ["service_cleanup"]}

        await close_until_quiescent(close, 8)
