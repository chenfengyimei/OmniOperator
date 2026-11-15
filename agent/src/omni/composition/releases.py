"""Assemble explicit offline release commands. No service install, stop, model or default-entry switch."""

from __future__ import annotations

from pathlib import Path
from typing import cast

from ..adapters.persistence.releases import ReleaseData
from ..adapters.platform.deployment_files import CurrentDeploymentFiles
from ..adapters.platform.exchange_deployments import ExchangeSourceDeployment
from ..adapters.platform.release_files import ReleaseFiles
from ..adapters.platform.service_control_files import ServiceControlFiles
from ..adapters.platform.service_readiness import ServiceReadiness
from ..adapters.platform.service_registration import ServiceRegistration
from ..adapters.primitives import SystemClock, UUIDs
from ..application.ports import Record, ServiceRegistrationPort
from ..application.release_drain import ReleaseDrain
from ..application.releases import Releases
from ..application.service_control import ServiceControl
from ..domain.contracts import BusinessError
from ..domain.recovery_authority import RecoverySpec
from ..domain.releases import validate_release
from ..domain.service_operations import validate_operation
from .lifecycle import close_until_quiescent


async def run_release(
    command: str,
    path: Path,
    *,
    request: Path | None = None,
    output: Path | None = None,
    approval: str = "",
    source_operation: Path | None = None,
    admin_socket: Path | None = None,
    admin_bundle_sha256: str | None = None,
) -> Record:
    deployments = CurrentDeploymentFiles()
    files = ReleaseFiles(deployments)
    options = (source_operation, admin_socket, admin_bundle_sha256)
    manager: ServiceRegistrationPort
    if any(item is not None for item in options):
        if command != "select" or any(item is None for item in options):
            raise BusinessError(
                "release_admin_configuration_invalid",
                "Supply the source uninstall operation, root socket and bundle SHA together for selection",
            )
        from ..adapters.platform.service_admin_client import PrivilegedServiceClient

        assert source_operation is not None and admin_socket is not None and admin_bundle_sha256 is not None
        release = validate_release(deployments.read(path.absolute()))
        operation = validate_operation(deployments.read(source_operation.absolute()))
        if operation["deployment"] != release["source"] or operation["action"] != "uninstall":
            raise BusinessError(
                "release_admin_source_changed",
                "The read-only root query must bind the exact source uninstall operation",
            )
        manager = PrivilegedServiceClient(cast(Record, operation), str(admin_socket.absolute()), admin_bundle_sha256)
    else:
        manager = ServiceRegistration()
    drain = None
    if command in {"online-plan", "prepare-online"}:
        verifier = (
            deployments
            if command == "online-plan"
            else ExchangeSourceDeployment(validate_release(deployments.read(path.absolute())), files)
        )
        drain = ReleaseDrain(
            ServiceControl(
                verifier, ServiceControlFiles(), ServiceRegistration(), ServiceReadiness(), SystemClock(), UUIDs()
            )
        )
    service = Releases(deployments, files, ReleaseData(files), manager, SystemClock(), UUIDs(), drain=drain)
    try:
        if command in {"plan", "online-plan", "recovery-plan"}:
            if request is None or output is None:
                raise BusinessError("release_request_invalid", "Choose explicit request and output files")
            recovery: RecoverySpec | None = None
            if command in {"plan", "online-plan"}:
                source = deployments.artifact_plan(path.absolute())
            else:
                original = validate_release(deployments.read(path.absolute()))
                history = files.history(original)
                prepared = service.prepared(history)
                if not history or history[-1]["phase"] not in {"selected", "selection_committed"}:
                    raise BusinessError(
                        "release_recovery_not_selected", "Recover a selected release using its verified snapshot"
                    )
                if deployments.capture() != original["source"]["runtime"]:
                    raise BusinessError(
                        "release_recovery_program_changed",
                        "Run recovery planning from the original immutable installation",
                    )
                source = original["target"]
                recovery = {
                    "release_sha256": original["sha256"],
                    "snapshot": prepared["backup"],
                    "manifest_sha256": prepared["backup_manifest_sha256"],
                    "isolation": None,
                    "restore_policy": "original_snapshot",
                    "expected_source_witness": prepared["target_witness"],
                }
            if command == "online-plan":
                return await service.plan_online(source, deployments.read(request.absolute()), str(output.absolute()))
            return service.plan(source, deployments.read(request.absolute()), str(output.absolute()), recovery)
        plan = validate_release(deployments.read(path.absolute()))
        if command == "inspect":
            return service.inspect(plan)
        if command == "prepare":
            return service.prepare(plan, approval)
        if command == "prepare-online":
            return await service.prepare_online(plan, approval)
        if command == "select":
            return await service.select(plan, approval)
        if command == "abandon":
            return service.abandon(plan, approval)
        raise BusinessError("release_command_invalid", "Choose an explicit reviewed release command")
    finally:

        async def close(budget: float) -> Record:
            return {"complete": await service.close(budget), "residual": ["release_maintenance"]}

        await close_until_quiescent(close, 8)
