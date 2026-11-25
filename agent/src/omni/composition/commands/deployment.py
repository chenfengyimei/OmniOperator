"""Reviewed service, release and exchange commands."""

from __future__ import annotations

import argparse
import asyncio
import json
from pathlib import Path

from ...adapters.platform.runtime.paths import InstancePaths
from ...adapters.primitives import SystemClock, UUIDs
from ...domain.contracts import BusinessError
from . import CommandPlugin


def exchange_arguments(exchanges: argparse.ArgumentParser) -> None:
    exchange_commands = exchanges.add_subparsers(dest="exchange_command", required=True)
    exchange_plan = exchange_commands.add_parser("plan")
    exchange_plan.add_argument("release", type=Path)
    exchange_plan.add_argument("journal", type=Path)
    exchange_plan.add_argument("output", type=Path)
    exchange_plan.add_argument(
        "--administrators", type=Path, help="Reviewed source/target protocol and immutable administrator bundle hashes"
    )
    admin_phase = exchange_commands.add_parser(
        "admin-phase", help="Publish one exact root phase approval without executing it"
    )
    admin_phase.add_argument("plan", type=Path)
    admin_phase.add_argument("role", choices=("source", "target"))
    admin_phase.add_argument("output", type=Path)
    isolation_plan = exchange_commands.add_parser(
        "isolation-plan", help="Review offline startup quarantine, retaining unknown effects"
    )
    isolation_plan.add_argument("plan", type=Path)
    isolation_plan.add_argument("output", type=Path)
    isolation_plan.add_argument("--reason", required=True)
    isolate = exchange_commands.add_parser("apply-isolation")
    isolate.add_argument("plan", type=Path)
    isolate.add_argument("--approve", required=True)
    rescue = exchange_commands.add_parser(
        "rescue-plan", help="Review isolated new-home recovery with a separate native registration key"
    )
    rescue.add_argument("plan", type=Path)
    rescue.add_argument("request", type=Path)
    rescue.add_argument("output", type=Path)
    cleanup_plan = exchange_commands.add_parser("cleanup-plan", help="Review one exact isolated predecessor definition")
    cleanup_plan.add_argument("plan", type=Path)
    cleanup_plan.add_argument("side", choices=("source", "target"))
    cleanup_plan.add_argument("output", type=Path)
    for operation in ("apply-cleanup", "inspect-cleanup", "reconcile-cleanup", "cancel-cleanup"):
        cleanup = exchange_commands.add_parser(operation)
        cleanup.add_argument("plan", type=Path)
        if operation in {"apply-cleanup", "reconcile-cleanup"}:
            cleanup.add_argument("--admin-socket", type=Path)
            cleanup.add_argument("--admin-bundle-sha256")
        if operation == "apply-cleanup":
            cleanup.add_argument(
                "--approve", required=True, help="Complete cleanup SHA; may remove a real native definition"
            )
    for operation in ("apply", "resume", "inspect", "abandon"):
        exchange = exchange_commands.add_parser(operation)
        exchange.add_argument("plan", type=Path)
        if operation in {"apply", "resume"}:
            exchange.add_argument("--admin-socket", type=Path)
            exchange.add_argument("--admin-bundle-sha256")
        if operation != "inspect":
            exchange.add_argument(
                "--approve", required=True, help="Exact exchange SHA; may alter and start a real service"
            )


def release_arguments(releases: argparse.ArgumentParser) -> None:
    release_commands = releases.add_subparsers(dest="release_command", required=True)
    for operation in ("plan", "online-plan", "recovery-plan"):
        release_plan = release_commands.add_parser(operation)
        release_plan.add_argument("source", type=Path)
        release_plan.add_argument("request", type=Path)
        release_plan.add_argument("output", type=Path)
    for operation in ("prepare", "prepare-online", "select", "inspect", "abandon"):
        release_operation = release_commands.add_parser(operation)
        release_operation.add_argument("plan", type=Path)
        if operation != "inspect":
            release_operation.add_argument("--approve", required=True)
        if operation == "select":
            release_operation.add_argument("--source-operation", type=Path)
            release_operation.add_argument("--admin-socket", type=Path)
            release_operation.add_argument("--admin-bundle-sha256")


def service_arguments(services: argparse.ArgumentParser) -> None:
    service_commands = services.add_subparsers(dest="service_operation", required=True)
    control_plan = service_commands.add_parser("control-plan", help="Review one service start or graceful stop")
    control_plan.add_argument("bundle", type=Path)
    control_plan.add_argument("action", choices=("start", "stop"))
    control_plan.add_argument("output", type=Path)
    for operation in ("apply-control", "inspect-control", "reconcile-control", "cancel-control"):
        control = service_commands.add_parser(operation)
        control.add_argument("control_plan", type=Path)
        if operation in {"apply-control", "reconcile-control"}:
            control.add_argument("--admin-socket", type=Path)
            control.add_argument("--admin-bundle-sha256")
        if operation == "apply-control":
            control.add_argument("--approve", required=True)
    capture = service_commands.add_parser("capture", help="Capture this interpreter and installed program identity")
    capture.add_argument("output", type=Path)
    admin_bundle = service_commands.add_parser(
        "admin-bundle", help="Build a reviewable stdlib-only root administrator artifact; never installs or elevates"
    )
    admin_bundle.add_argument("destination", type=Path)
    service_plan = service_commands.add_parser("plan", help="Create an immutable reviewable service plan")
    service_plan.add_argument("request", type=Path)
    service_plan.add_argument("runtime", type=Path)
    service_plan.add_argument("output", type=Path)
    materialize = service_commands.add_parser(
        "materialize", help="Publish a new deployment bundle without installing it"
    )
    materialize.add_argument("plan", type=Path)
    materialize.add_argument("destination", type=Path)
    for operation in ("verify", "status"):
        service_query = service_commands.add_parser(operation)
        service_query.add_argument("bundle", type=Path)
    registration_plan = service_commands.add_parser(
        "operation-plan", help="Review installation/removal without changing OS registration"
    )
    registration_plan.add_argument("bundle", type=Path)
    registration_plan.add_argument("action", choices=("install", "uninstall"))
    registration_plan.add_argument("output", type=Path)
    for operation in ("apply-operation", "inspect-operation", "reconcile-operation", "cancel-operation"):
        registration = service_commands.add_parser(operation)
        registration.add_argument("operation_plan", type=Path)
        if operation in {"apply-operation", "reconcile-operation"}:
            registration.add_argument("--admin-socket", type=Path)
            registration.add_argument("--admin-bundle-sha256")
        if operation == "apply-operation":
            registration.add_argument(
                "--approve", required=True, help="Exact reviewed operation SHA256; performs real OS registration"
            )


def run_exchange(args: argparse.Namespace, _paths: InstancePaths) -> int:
    if args.exchange_command in {
        "cleanup-plan",
        "apply-cleanup",
        "inspect-cleanup",
        "reconcile-cleanup",
        "cancel-cleanup",
    }:
        from ..service_cleanup import run_cleanup

        result = asyncio.run(
            run_cleanup(
                args.exchange_command,
                args.plan,
                side=getattr(args, "side", ""),
                output=getattr(args, "output", None),
                approval=getattr(args, "approve", ""),
                admin_socket=getattr(args, "admin_socket", None),
                admin_bundle_sha256=getattr(args, "admin_bundle_sha256", None),
            )
        )
    else:
        from ..service_exchange import run_exchange

        result = asyncio.run(
            run_exchange(
                args.exchange_command,
                args.release if args.exchange_command == "plan" else args.plan,
                journal=getattr(args, "journal", None),
                output=getattr(args, "output", None),
                approval=getattr(args, "approve", ""),
                reason=getattr(args, "reason", ""),
                request=getattr(args, "request", None),
                admin_socket=getattr(args, "admin_socket", None),
                admin_bundle_sha256=getattr(args, "admin_bundle_sha256", None),
                administrators=getattr(args, "administrators", None),
                role=getattr(args, "role", ""),
            )
        )
    print(json.dumps(result, ensure_ascii=False))
    if args.exchange_command in {"apply", "resume"} and result.get("state") != "finished":
        return 1
    cleanup_receipt = result.get("receipt")
    if args.exchange_command in {"apply-cleanup", "reconcile-cleanup"} and (
        result.get("state") != "succeeded"
        or not isinstance(cleanup_receipt, dict)
        or cleanup_receipt.get("cleanup_complete") is not True
    ):
        return 1
    return 0


def run_release(args: argparse.Namespace, _paths: InstancePaths) -> int:
    from ..releases import run_release

    result = asyncio.run(
        run_release(
            args.release_command,
            args.source if args.release_command in {"plan", "online-plan", "recovery-plan"} else args.plan,
            request=getattr(args, "request", None),
            output=getattr(args, "output", None),
            approval=getattr(args, "approve", ""),
            source_operation=getattr(args, "source_operation", None),
            admin_socket=getattr(args, "admin_socket", None),
            admin_bundle_sha256=getattr(args, "admin_bundle_sha256", None),
        )
    )
    print(json.dumps(result, ensure_ascii=False))
    if args.release_command == "prepare-online" and result.get("state") != "prepared":
        return 1
    return 0


def run_service(args: argparse.Namespace, _paths: InstancePaths) -> int:
    from omni.adapters.platform.services.deployment_files import CurrentDeploymentFiles
    from omni.adapters.platform.services.service_query import ServiceQuery
    from omni.adapters.platform.services.service_readiness import ServiceReadiness

    from ...application.service_deployment import ServiceDeployment

    deployment = ServiceDeployment(CurrentDeploymentFiles(), SystemClock(), UUIDs())
    operation = args.service_operation
    if operation in {"control-plan", "apply-control", "inspect-control", "reconcile-control", "cancel-control"}:
        from ..service_control import run_control

        result = asyncio.run(
            run_control(
                operation,
                args.bundle if operation == "control-plan" else args.control_plan,
                action=getattr(args, "action", ""),
                output=getattr(args, "output", None),
                approval=getattr(args, "approve", ""),
                admin_socket=getattr(args, "admin_socket", None),
                admin_bundle_sha256=getattr(args, "admin_bundle_sha256", None),
            )
        )
    elif operation == "admin-bundle":
        from omni.adapters.platform.services.service_admin_bundle import materialize_admin_bundle

        result = materialize_admin_bundle(args.destination.absolute())
    elif operation in {
        "operation-plan",
        "apply-operation",
        "inspect-operation",
        "reconcile-operation",
        "cancel-operation",
    }:
        from ..service_operations import run_operation

        result = asyncio.run(
            run_operation(
                operation,
                args.bundle if operation == "operation-plan" else args.operation_plan,
                action=getattr(args, "action", ""),
                output=getattr(args, "output", None),
                approval=getattr(args, "approve", ""),
                admin_socket=getattr(args, "admin_socket", None),
                admin_bundle_sha256=getattr(args, "admin_bundle_sha256", None),
            )
        )
    elif operation == "capture":
        result = deployment.capture(str(args.output.absolute()))
    elif operation == "plan":
        result = deployment.plan(
            str(args.request.absolute()), str(args.runtime.absolute()), str(args.output.absolute())
        )
    elif operation == "materialize":
        result = deployment.materialize(str(args.plan.absolute()), str(args.destination.absolute()))
    else:
        verification = deployment.verify(str(args.bundle.absolute()))
        result = verification
        if operation == "status":

            async def service_status():
                manager, readiness = ServiceQuery(), ServiceReadiness()
                try:
                    return await deployment.status(verification, manager, readiness)
                finally:
                    manager_closed = await manager.close(6)
                    readiness_closed = await readiness.close(6)
                    if not manager_closed or not readiness_closed:
                        raise BusinessError(
                            "service_query_cleanup_pending", "Read-only service query cleanup is incomplete"
                        )

            result = asyncio.run(service_status())
    print(json.dumps(result, ensure_ascii=False))
    if operation == "verify" and not result["eligible"]:
        return 1
    if operation == "status" and not result["ready"]:
        return 1
    if operation in {"apply-operation", "reconcile-operation"} and result.get("state") != "succeeded":
        return 1
    if operation in {"apply-control", "reconcile-control"} and result.get("state") != "succeeded":
        return 1
    if operation == "cancel-control" and result.get("state") not in {
        "cancelled",
        "succeeded",
        "failed",
        "denied",
    }:
        return 1
    if operation == "cancel-operation" and result.get("state") not in {
        "cancelled",
        "succeeded",
        "denied",
        "failed",
    }:
        return 1
    return 0


EXCHANGE = CommandPlugin(
    "exchange", run_exchange, help="Reviewed prepared release to user-service readiness", configure=exchange_arguments
)
RELEASE = CommandPlugin(
    "release",
    run_release,
    help="Reviewed immutable versions, verified snapshots and new-directory selection",
    configure=release_arguments,
)
SERVICE = CommandPlugin(
    "service",
    run_service,
    help="Review deployments and explicitly apply registration; macOS plans declare activation/unload effects",
    configure=service_arguments,
)
