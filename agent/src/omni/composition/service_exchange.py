"""Assemble reviewed exchanges and their explicitly pinned root session; never elevates or switches default entrypoints."""

from __future__ import annotations

from pathlib import Path
from typing import cast

from omni.adapters.platform.releases.release_files import ReleaseFiles
from omni.adapters.platform.services.deployment_files import CurrentDeploymentFiles
from omni.adapters.platform.services.exchange_deployments import ExchangeSourceDeployment
from omni.adapters.platform.services.exchange_files import ExchangeFiles
from omni.adapters.platform.services.service_control_files import ServiceControlFiles
from omni.adapters.platform.services.service_operation_files import ServiceOperationFiles
from omni.adapters.platform.services.service_readiness import ServiceReadiness
from omni.adapters.platform.services.service_registration import ServiceRegistration

from ..adapters.persistence.releases import ReleaseData
from ..adapters.primitives import SystemClock, UUIDs
from ..application.exchange_recovery import ExchangeRecovery
from ..application.exchange_recovery_steps import ExchangeRecoverySteps
from ..application.exchange_steps import ExchangeSteps
from ..application.ports import OwnedClosePort, Record, ServiceRegistrationPort, ServiceStarterPort
from ..application.releases import Releases
from ..application.service_control import ServiceControl
from ..application.service_exchange import ServiceExchange
from ..application.service_operations import ServiceOperations
from ..domain.contracts import BusinessError
from ..domain.releases import validate_release
from ..domain.service_exchange import marker, progress, validate_exchange
from ..domain.service_isolation import validate_isolation
from .lifecycle import close_until_quiescent


async def run_exchange(
    command: str,
    path: Path,
    *,
    journal: Path | None = None,
    output: Path | None = None,
    approval: str = "",
    reason: str = "",
    request: Path | None = None,
    admin_socket: Path | None = None,
    admin_bundle_sha256: str | None = None,
    administrators: Path | None = None,
    role: str = "",
) -> Record:
    deployments, clock, ids = CurrentDeploymentFiles(), SystemClock(), UUIDs()
    files = ExchangeFiles(deployments)
    value = deployments.read(path.absolute())
    isolation = validate_isolation(value) if command == "apply-isolation" else None
    if isolation is not None:
        value = cast(Record, dict(isolation["exchange"]))
    plan = None if command == "plan" else validate_exchange(value)
    release = validate_release(value) if plan is None else plan["release"]
    if (admin_socket is None) != (admin_bundle_sha256 is None):
        raise BusinessError(
            "service_exchange_admin_configuration_invalid",
            "Supply the reviewed exchange root socket and bundle SHA together",
        )
    if administrators is not None and command != "plan":
        raise BusinessError(
            "service_exchange_admin_configuration_invalid", "Administrator participants belong to the reviewed plan"
        )
    administrator: OwnedClosePort | None = None
    handover = None
    single_administrator = None
    source_native: ServiceRegistrationPort
    target_native: ServiceRegistrationPort
    selection_native: ServiceRegistrationPort
    control_native: ServiceStarterPort
    if plan is not None and plan["administrators"] is not None and command in {"apply", "resume"}:
        if admin_socket is not None or admin_bundle_sha256 is not None:
            raise BusinessError(
                "service_exchange_admin_configuration_invalid",
                "Staged execution uses only its reviewed participant endpoints and bundle hashes",
            )
        from omni.adapters.platform.services.service_handover import AdministratorHandover

        handover = AdministratorHandover(cast(Record, dict(plan)))
        administrator = handover
        completed, _, _ = progress(plan, files.history(plan))
        handover.prepare_resume(completed)
        source_native = handover.parents["source"].lease("remove_source")
        selection_native = handover.parents["source"].lease("remove_source", read_only=True)
        target_native = handover.parents["target"].lease("install_target")
        control_native = handover.parents["target"].lease("start_target")
    elif admin_socket is not None and admin_bundle_sha256 is not None:
        if (
            plan is None
            or command not in {"apply", "resume"}
            or release["target"]["request"]["target"] not in {"linux-system", "macos-system"}
        ):
            raise BusinessError(
                "service_exchange_admin_configuration_invalid",
                "Root routing applies only to execution of the exact system exchange",
            )
        from omni.adapters.platform.services.service_admin_client import ExchangeAdministrator

        single_administrator = ExchangeAdministrator(
            cast(Record, dict(plan)), str(admin_socket.absolute()), admin_bundle_sha256
        )
        administrator = single_administrator
        source_native = single_administrator.lease("remove_source")
        selection_native = single_administrator.lease("remove_source", read_only=True)
        target_native = single_administrator.lease("install_target")
        control_native = single_administrator.lease("start_target")
    else:
        if command in {"apply", "resume"} and release["target"]["request"]["target"] in {
            "linux-system",
            "macos-system",
        }:
            raise BusinessError(
                "service_exchange_admin_required",
                "Explicit root approval is required before any system exchange intent",
            )
        source_native, target_native = ServiceRegistration(), ServiceRegistration()
        selection_native, control_native = ServiceRegistration(), ServiceRegistration()
    reservation = None if plan is None else cast(Record, marker(plan))
    release_files = ReleaseFiles(deployments, exchange=reservation)
    selection = Releases(deployments, release_files, ReleaseData(release_files), selection_native, clock, ids)
    source = ServiceOperations(
        ExchangeSourceDeployment(release, release_files),
        ServiceOperationFiles(exchange=reservation),
        source_native,
        clock,
        ids,
    )
    target = ServiceOperations(deployments, ServiceOperationFiles(exchange=reservation), target_native, clock, ids)
    control = ServiceControl(
        deployments,
        ServiceControlFiles(exchange=reservation),
        control_native,
        ServiceReadiness(),
        clock,
        ids,
    )
    service = ServiceExchange(
        files, ExchangeSteps(source, target, selection, control, owner=administrator, handover=handover), clock, ids
    )
    recovery = ExchangeRecovery(files, ExchangeRecoverySteps(files, selection), clock, ids)
    try:
        preflight = single_administrator is not None and not await single_administrator.probe()
        if handover is not None and plan is not None and not files.history(plan):
            preflight = not await handover.admit("remove_source")
        if preflight:
            raise BusinessError(
                "service_exchange_admin_unavailable",
                "The approved root session must be observable before exchange dispatch",
            )
        if command == "plan":
            if journal is None or output is None:
                raise BusinessError("service_exchange_output_required", "Choose new private journal and plan paths")
            return service.plan(
                release,
                str(journal.absolute()),
                str(output.absolute()),
                None if administrators is None else deployments.read(administrators.absolute()),
            )
        assert plan is not None
        if command == "admin-phase":
            if output is None:
                raise BusinessError("service_exchange_output_required", "Choose a new private phase approval file")
            return files.publish_administrator(plan, role, str(output.absolute()))
        if command == "isolation-plan":
            if output is None:
                raise BusinessError("service_isolation_output_required", "Choose a new private isolation plan path")
            return recovery.plan(plan, reason, str(output.absolute()))
        if command == "apply-isolation":
            return recovery.apply(isolation, approval)
        if command == "rescue-plan":
            if request is None or output is None:
                raise BusinessError(
                    "service_rescue_request_required", "Supply new target, backup, journal and output paths"
                )
            return recovery.rescue_plan(plan, deployments.read(request.absolute()), str(output.absolute()))
        if command == "inspect":
            return service.inspect(plan)
        if command in {"apply", "resume", "abandon"}:
            return await service.apply(plan, approval, resume=command == "resume", abandon=command == "abandon")
        raise BusinessError("service_exchange_command_invalid", "Choose an explicit exchange command")
    finally:

        async def close(budget: float) -> Record:
            return {"complete": await service.close(budget), "residual": ["service_exchange"]}

        await close_until_quiescent(close, 8)
