"""V2 development entrypoint. Online commands use the authenticated HTTP facade."""

from __future__ import annotations

import argparse
import asyncio
import json
import sys
from pathlib import Path
from typing import cast
from urllib.parse import urlsplit

from ..adapters.diagnostic_bundle import DiagnosticFiles, StoredDiagnostics, publish_diagnostic
from ..adapters.persistence.audit_pruning import (
    apply_audit_retention,
    cancel_audit_retention,
    plan_audit_retention,
    read_audit_plan,
)
from ..adapters.persistence.event_pruning import (
    apply_event_retention,
    cancel_event_retention,
    plan_event_retention,
    read_event_plan,
)
from ..adapters.persistence.execution_pruning import apply_pruning, cancel_pruning, plan_pruning, read_pruning_plan
from ..adapters.persistence.maintenance import (
    backup,
    derived_indexes,
    durable_json,
    inspect_database,
    object_report,
    read_schema,
    restore,
)
from ..adapters.persistence.object_gc import apply_collection, plan_collection
from ..adapters.persistence.operations_read import offline_read, offline_status
from ..adapters.persistence.reference_release import (
    apply_reference_release,
    cancel_reference_release,
    plan_reference_release,
    read_reference_plan,
)
from ..adapters.platform.paths import InstanceLock, InstancePaths
from ..adapters.platform.permissions import private_path
from ..adapters.primitives import SystemClock, UUIDs
from ..application.ports import Record
from ..domain.contracts import BusinessError
from ..domain.installation import make_installation, validate_installation
from .instance import open_instance
from .serving import serve_instance


def _database_schema(database: Path) -> str:
    return read_schema(database)


def installation_report(paths: InstancePaths) -> dict[str, object]:
    from ..adapters.persistence.migration_config import head as migration_head

    expected = migration_head()
    manifest_path = paths.home / "installation.json"
    manifest: dict[str, str] | None = None
    problem = ""
    if manifest_path.is_symlink():
        problem = "manifest_link"
    elif manifest_path.is_file():
        try:
            manifest = validate_installation(json.loads(manifest_path.read_text(encoding="utf-8")))
            if manifest["schema_version"] != expected or manifest["code_head"] != expected:
                problem = "manifest_mismatch"
        except (ValueError, BusinessError, UnicodeError):
            problem = "manifest_invalid"
    database = {
        "exists": paths.database.is_file(),
        "wal": (paths.database.parent / "omni.db-wal").is_file(),
        "schema": "",
        "problem": "",
    }
    if database["exists"]:
        try:
            database["schema"] = _database_schema(paths.database)
        except BusinessError:
            database["problem"] = "database_unreadable"
    pending = bool(database["schema"]) and database["schema"] != expected
    return {
        "format": "omni-installation-report-1",
        "home": str(paths.home),
        "exists": paths.home.is_dir(),
        "manifest": manifest,
        "manifest_problem": problem,
        "database": database,
        "expected_schema": expected,
        "upgrade_required": pending,
        "ready": bool(manifest and database["exists"] and database["schema"] and not pending and not problem),
    }


def seal_installation(paths: InstancePaths) -> dict[str, object]:
    from ..adapters.persistence.migration_config import head as migration_head

    if not paths.home.is_dir():
        raise BusinessError("installation_missing", "Initialize the instance before sealing its receipt")
    manifest_path = paths.home / "installation.json"
    if manifest_path.is_symlink():
        raise BusinessError("unsafe_manifest", "Installation receipts cannot be links")
    expected = migration_head()
    schema = _database_schema(paths.database)
    if schema != expected:
        raise BusinessError(
            "installation_pending_upgrade", "Apply pending migrations and verify the instance before sealing"
        )
    receipt = make_installation(SystemClock().now(), schema, expected)
    if manifest_path.is_file():
        existing = validate_installation(json.loads(manifest_path.read_text(encoding="utf-8")))
        if existing["schema_version"] != receipt["schema_version"] or existing["code_head"] != receipt["code_head"]:
            raise BusinessError(
                "installation_changed", "The installation receipt already names another schema or code head"
            )
        return {"written": False, "manifest": existing}
    durable_json(manifest_path, cast(Record, receipt))
    private_path(manifest_path)
    return {"written": True, "manifest": receipt}


def parser() -> argparse.ArgumentParser:
    result = argparse.ArgumentParser(
        prog="python -m omni.composition.cli", description="OmniOperator V2 development runtime"
    )
    result.add_argument("--home", type=Path, help="Fresh, independent V2 instance directory")
    commands = result.add_subparsers(dest="command", required=True)
    commands.add_parser("init", help="Create the empty V2 schema without calling a model")
    commands.add_parser("doctor", help="Read-only, exclusive database diagnostics")
    installation = commands.add_parser("installation", help="V2 installation receipt and read-only upgrade preflight")
    installation.add_argument(
        "operation",
        choices=[
            "verify",
            "seal",
            "artifact",
            "stage",
            "prepare",
            "switch-plan",
            "switch",
            "install",
            "migrate",
            "current",
            "uninstall",
            "environment",
            "rollback",
            "backup",
            "rollback-check",
            "status",
            "smoke",
        ],
        help="Verify/seal/check/stage/prepare, plan, switch, install, migrate, environment, rollback, show current, uninstall, back up, drill, show status, or smoke",
    )
    installation.add_argument("artifact", nargs="?", type=Path, help="Wheel to verify for the artifact operation")
    installation.add_argument("--sha256", default="", help="Expected wheel SHA256 for the artifact operation")
    installation.add_argument(
        "--purge-data", action="store_true", help="Explicitly remove local data/objects/receipts during uninstall"
    )
    vector = commands.add_parser("vectors", help="Select or disable a reviewed local ONNX bundle for the next startup")
    vector_commands = vector.add_subparsers(dest="vector_operation", required=True)
    vector_inspect = vector_commands.add_parser("inspect")
    vector_inspect.add_argument("bundle", type=Path)
    vector_configure = vector_commands.add_parser("configure")
    vector_configure.add_argument("bundle", type=Path)
    vector_configure.add_argument("--sha256", required=True, help="SHA256 of canonical reviewed manifest JSON")
    vector_commands.add_parser("disable")
    exchanges = commands.add_parser("exchange", help="Reviewed prepared release to user-service readiness")
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
    releases = commands.add_parser(
        "release", help="Reviewed immutable versions, verified snapshots and new-directory selection"
    )
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
    services = commands.add_parser(
        "service",
        help="Review deployments and explicitly apply registration; macOS plans declare activation/unload effects",
    )
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
    retention = commands.add_parser(
        "retention-preview", help="Exclusive read-only 30-day execution-body preview; does not delete"
    )
    retention.add_argument("--after", default="")
    retention.add_argument("--limit", type=int, default=20)
    audit_preview = commands.add_parser(
        "retention-audit-preview", help="Exclusive read-only 180-day Agent audit preview; does not delete"
    )
    audit_preview.add_argument("--agent", required=True)
    audit_preview.add_argument("--cursor", default="")
    audit_preview.add_argument("--limit", type=int, default=20)
    audit_preview.add_argument("--as-of", default="")
    audit_plan = commands.add_parser(
        "retention-audit-plan", help="Write a reviewable 180-day Agent audit-pruning plan outside the instance"
    )
    audit_plan.add_argument("output", type=Path)
    audit_plan.add_argument("--agent", required=True)
    audit_plan.add_argument("--cursor", default="")
    audit_plan.add_argument("--limit", type=int, default=20)
    audit_plan.add_argument("--as-of", default="")
    audit_apply = commands.add_parser(
        "retention-audit-apply", help="Apply or resume audit deletion with its verified original snapshot"
    )
    audit_apply.add_argument("plan", type=Path)
    audit_apply.add_argument("--backup", type=Path, required=True)
    audit_cancel = commands.add_parser(
        "retention-audit-cancel", help="Cancel pending audit deletions; committed deletions remain recorded"
    )
    audit_cancel.add_argument("plan", type=Path)
    audit_cancel.add_argument("--reason", required=True)
    events_preview = commands.add_parser(
        "retention-events-preview", help="Exclusive read-only 180-day Agent event preview; does not delete"
    )
    events_preview.add_argument("--agent", required=True)
    events_preview.add_argument("--cursor", default="")
    events_preview.add_argument("--limit", type=int, default=20)
    events_preview.add_argument("--as-of", default="")
    events_plan = commands.add_parser(
        "retention-events-plan", help="Write a reviewable 180-day Agent event-pruning plan outside the instance"
    )
    events_plan.add_argument("output", type=Path)
    events_plan.add_argument("--agent", required=True)
    events_plan.add_argument("--cursor", default="")
    events_plan.add_argument("--limit", type=int, default=20)
    events_plan.add_argument("--as-of", default="")
    events_apply = commands.add_parser(
        "retention-events-apply", help="Apply or resume event deletion with its verified original snapshot"
    )
    events_apply.add_argument("plan", type=Path)
    events_apply.add_argument("--backup", type=Path, required=True)
    events_cancel = commands.add_parser(
        "retention-events-cancel", help="Cancel pending event deletions; committed deletions remain recorded"
    )
    events_cancel.add_argument("plan", type=Path)
    events_cancel.add_argument("--reason", required=True)
    retention_plan = commands.add_parser(
        "retention-plan", help="Write a reviewable 30-day body-pruning plan outside the instance"
    )
    retention_plan.add_argument("output", type=Path)
    retention_plan.add_argument("--after", default="")
    retention_plan.add_argument("--limit", type=int, default=20)
    retention_apply = commands.add_parser(
        "retention-apply", help="Apply or resume a plan with its verified pre-prune snapshot"
    )
    retention_apply.add_argument("plan", type=Path)
    retention_apply.add_argument("--backup", type=Path, required=True)
    retention_cancel = commands.add_parser(
        "retention-cancel", help="Cancel pending prune targets; committed pruning remains recorded"
    )
    retention_cancel.add_argument("plan", type=Path)
    retention_cancel.add_argument("--reason", required=True)
    refs_preview = commands.add_parser(
        "retention-refs-preview", help="Preview retired action-object references; no deletion"
    )
    refs_preview.add_argument("--after", default="")
    refs_preview.add_argument("--limit", type=int, default=20)
    refs_plan = commands.add_parser("retention-refs-plan", help="Review reference release separately from object GC")
    refs_plan.add_argument("output", type=Path)
    refs_plan.add_argument("--after", default="")
    refs_plan.add_argument("--limit", type=int, default=20)
    refs_apply = commands.add_parser(
        "retention-refs-apply", help="Release reviewed references after verified backup; no files deleted"
    )
    refs_apply.add_argument("plan", type=Path)
    refs_apply.add_argument("--backup", type=Path, required=True)
    refs_cancel = commands.add_parser(
        "retention-refs-cancel", help="Cancel pending reference releases without rebuilding committed references"
    )
    refs_cancel.add_argument("plan", type=Path)
    refs_cancel.add_argument("--reason", required=True)
    indexes = commands.add_parser("indexes", help="Exclusive offline full-text index maintenance")
    indexes.add_argument("operation", choices=["check", "rebuild"])
    auth = commands.add_parser("auth", help="Print or rotate the local owner token (keep it private)")
    auth.add_argument("--rotate", action="store_true", help="Rotate via the running instance and print the new token")
    auth.add_argument("--url", default="http://127.0.0.1:8766")
    diagnostics = commands.add_parser(
        "diagnostics", help="Export bounded metadata without chat, credentials or model/device calls"
    )
    diagnostics.add_argument("destination", type=Path)
    diagnostics.add_argument(
        "--offline",
        action="store_true",
        help="Exclusive read-only export without starting the runtime or upgrading schema",
    )
    diagnostics.add_argument("--url", default="http://127.0.0.1:8766")
    serve = commands.add_parser("serve")
    serve.add_argument("--host", default="127.0.0.1")
    serve.add_argument("--port", type=int, default=8766)
    serve.add_argument("--external-url", help="Explicit trusted HTTPS origin when binding remotely")
    serve.add_argument("--master-key-file", type=Path)
    serve.add_argument("--stdout-logs", action="store_true")
    snapshot = commands.add_parser("backup")
    snapshot.add_argument("destination", type=Path)
    recover = commands.add_parser("restore")
    recover.add_argument("source", type=Path)
    recover.add_argument("destination", type=Path)
    query = commands.add_parser("request", help="Authenticated request to an already running local instance")
    query.add_argument("path")
    query.add_argument("--url", default="http://127.0.0.1:8766")
    objects = commands.add_parser("objects", help="Exclusive offline object maintenance")
    operations = objects.add_subparsers(dest="operation", required=True)
    operations.add_parser("check", help="Verify registered objects and pending collection")
    plan = operations.add_parser("gc-plan", help="Write a reviewable collection plan outside the instance")
    plan.add_argument("output", type=Path)
    apply = operations.add_parser("gc-apply", help="Apply or resume the exact reviewed plan")
    apply.add_argument("plan", type=Path)
    return result


def local_origin(value: str) -> str:
    try:
        target = urlsplit(value)
        if (
            target.scheme != "http"
            or target.hostname not in {"127.0.0.1", "localhost", "::1"}
            or target.username is not None
            or target.password is not None
            or target.query
            or target.fragment
            or target.path not in {"", "/"}
        ):
            raise ValueError
        target.port
    except ValueError as exc:
        raise BusinessError("invalid_local_endpoint", "CLI requests require a local API origin") from exc
    return value.rstrip("/")


def diagnostic_download(paths: InstancePaths, url: str) -> bytes:
    import httpx

    origin = local_origin(url)
    token_path = paths.control / "owner.token"
    if token_path.is_symlink() or token_path.is_junction():
        raise BusinessError("unsafe_owner_token", "Owner token cannot be a link")
    token = token_path.read_text(encoding="utf-8").strip()
    try:
        with (
            httpx.Client(
                trust_env=False, follow_redirects=False, timeout=10, headers={"Accept-Encoding": "identity"}
            ) as client,
            client.stream(
                "GET", origin + "/api/v1/system/diagnostics", headers={"Authorization": "Bearer " + token}
            ) as response,
        ):
            response.raise_for_status()
            if response.headers.get("content-encoding", "identity").lower() != "identity":
                raise BusinessError(
                    "invalid_diagnostic_response", "Diagnostic downloads cannot use HTTP content encoding"
                )
            if response.headers.get("content-type") != "application/zip":
                raise BusinessError(
                    "invalid_diagnostic_response", "The local service did not return a diagnostic package"
                )
            chunks: list[bytes] = []
            size = 0
            for chunk in response.iter_raw(chunk_size=65536):
                size += len(chunk)
                if size > 2 * 1024 * 1024:
                    raise BusinessError("diagnostics_too_large", "Diagnostic download exceeds 2 MiB")
                chunks.append(chunk)
            return b"".join(chunks)
    except httpx.HTTPError as exc:
        raise BusinessError(
            "local_api_unavailable",
            "The local diagnostic service is unavailable or did not authorize the request",
            status=503,
        ) from exc


def main() -> int:
    args = parser().parse_args()
    paths = InstancePaths.resolve(args.home)
    try:
        if args.command == "serve":
            serve_instance(
                paths.home,
                host=args.host,
                port=args.port,
                external_url=args.external_url,
                master_key_file=args.master_key_file,
                stdout_logs=args.stdout_logs,
            )
        elif args.command == "vectors":
            from ..adapters.vector_files import configure, identity, inspect_bundle

            paths.initialize()
            lock = InstanceLock(paths)
            lock.acquire()
            try:
                if args.vector_operation == "inspect":
                    result = inspect_bundle(args.bundle)
                elif args.vector_operation == "configure":
                    result = configure(paths.home, args.bundle, args.sha256)
                else:
                    selected = paths.home / "config" / "local-vector.json"
                    if selected.exists():
                        identity(selected)
                        selected.unlink()
                    result = {"disabled": True, "applies": "next_start", "indexes_preserved": True}
                print(json.dumps(result, ensure_ascii=False))
            finally:
                lock.close()
        elif args.command == "exchange":
            if args.exchange_command in {
                "cleanup-plan",
                "apply-cleanup",
                "inspect-cleanup",
                "reconcile-cleanup",
                "cancel-cleanup",
            }:
                from .service_cleanup import run_cleanup

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
                from .service_exchange import run_exchange

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
        elif args.command == "release":
            from .releases import run_release

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
        elif args.command == "service":
            from ..adapters.platform.deployment_files import CurrentDeploymentFiles
            from ..adapters.platform.service_query import ServiceQuery
            from ..adapters.platform.service_readiness import ServiceReadiness
            from ..application.service_deployment import ServiceDeployment

            deployment = ServiceDeployment(CurrentDeploymentFiles(), SystemClock(), UUIDs())
            operation = args.service_operation
            if operation in {"control-plan", "apply-control", "inspect-control", "reconcile-control", "cancel-control"}:
                from .service_control import run_control

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
                from ..adapters.platform.service_admin_bundle import materialize_admin_bundle

                result = materialize_admin_bundle(args.destination.absolute())
            elif operation in {
                "operation-plan",
                "apply-operation",
                "inspect-operation",
                "reconcile-operation",
                "cancel-operation",
            }:
                from .service_operations import run_operation

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
                result = deployment.verify(str(args.bundle.absolute()))
                if operation == "status":

                    async def service_status():
                        manager, readiness = ServiceQuery(), ServiceReadiness()
                        try:
                            return await deployment.status(cast(Record, result), manager, readiness)
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
        elif args.command == "init":

            async def initialize():
                instance = await open_instance(paths.home)
                try:
                    return {"initialized": True, "home": str(paths.home), **instance.identity}
                finally:
                    await instance.close()

            print(json.dumps(asyncio.run(initialize())))
        elif args.command == "installation":
            if args.operation == "seal":
                print(json.dumps(seal_installation(paths)))
            elif args.operation == "artifact":
                if args.artifact is None or not args.sha256:
                    raise BusinessError("artifact_missing", "Provide the wheel path and its recorded SHA256")
                from ..adapters.platform.wheel_artifact import inspect_wheel

                print(json.dumps(inspect_wheel(args.artifact, args.sha256)))
            elif args.operation == "stage":
                if args.artifact is None or not args.sha256:
                    raise BusinessError("artifact_missing", "Provide the wheel path and its recorded SHA256")
                from ..adapters.platform.wheel_stage import stage_wheel

                print(json.dumps(stage_wheel(paths.home, args.artifact, args.sha256)))
            elif args.operation == "prepare":
                if not args.sha256:
                    raise BusinessError("artifact_missing", "Provide the staged wheel SHA256")
                from ..adapters.platform.wheel_prepare import prepare_wheel

                print(json.dumps(prepare_wheel(paths.home, args.sha256)))
            elif args.operation == "switch-plan":
                if not args.sha256:
                    raise BusinessError("artifact_missing", "Provide the prepared wheel SHA256")
                from ..adapters.platform.wheel_switch import switch_plan

                print(json.dumps(switch_plan(paths, args.sha256)))
            elif args.operation == "switch":
                if not args.sha256:
                    raise BusinessError("artifact_missing", "Provide the prepared wheel SHA256")
                from ..adapters.platform.wheel_install import switch_candidate

                print(json.dumps(switch_candidate(paths, args.sha256)))
            elif args.operation == "install":
                if args.artifact is None or not args.sha256:
                    raise BusinessError("artifact_missing", "Provide the wheel path and its recorded SHA256")
                from ..adapters.platform.wheel_install import install_candidate

                print(json.dumps(install_candidate(paths, args.artifact, args.sha256)))
            elif args.operation == "migrate":
                if not args.sha256:
                    raise BusinessError("artifact_missing", "Provide the prepared wheel SHA256")
                from ..adapters.platform.wheel_install import migrate_candidate

                print(json.dumps(migrate_candidate(paths, args.sha256)))
            elif args.operation == "current":
                from ..adapters.platform.wheel_install import runtime_current

                print(json.dumps(runtime_current(paths)))
            elif args.operation == "uninstall":
                from ..adapters.platform.wheel_uninstall import uninstall_candidate

                print(json.dumps(uninstall_candidate(paths, args.sha256, purge=args.purge_data)))
            elif args.operation == "environment":
                if not args.sha256:
                    raise BusinessError("artifact_missing", "Provide the staged wheel SHA256")
                from ..adapters.platform.wheel_environment import create_environment

                print(json.dumps(create_environment(paths, args.sha256)))
            elif args.operation == "rollback":
                from ..adapters.platform.wheel_install import rollback_candidate

                print(json.dumps(rollback_candidate(paths)))
            elif args.operation == "backup":
                if not args.sha256:
                    raise BusinessError("artifact_missing", "Provide the prepared wheel SHA256")
                from ..adapters.platform.wheel_backup import backup_candidate

                print(json.dumps(backup_candidate(paths, args.sha256)))
            elif args.operation == "rollback-check":
                if not args.sha256:
                    raise BusinessError("artifact_missing", "Provide the prepared wheel SHA256")
                from ..adapters.platform.wheel_rollback import rollback_check

                print(json.dumps(rollback_check(paths, args.sha256)))
            elif args.operation == "status":
                from ..adapters.platform.wheel_status import installation_status

                print(json.dumps(installation_status(paths)))
            elif args.operation == "smoke":
                if not args.sha256:
                    raise BusinessError("artifact_missing", "Provide the prepared wheel SHA256")
                from ..adapters.platform.wheel_smoke import smoke_candidate

                print(json.dumps(smoke_candidate(paths, args.sha256)))
            else:
                print(json.dumps(installation_report(paths)))
        elif args.command == "doctor":
            lock = InstanceLock(paths)
            lock.acquire()
            try:
                report = cast(Record, {**inspect_database(paths.database), "objects": object_report(paths)})
                objects = cast(Record, report["objects"])
                print(json.dumps(report))
                if (
                    report["integrity"] != "ok"
                    or report["foreign_key_errors"]
                    or report["document_erasure_attention"]
                    or report["document_index_building"]
                    or report["execution_retention_attention"]
                    or report["reference_release_attention"]
                    or report["audit_retention_attention"]
                    or report["event_retention_attention"]
                    or report["schedule_attention"]
                    or report["unknown_model_calls"]
                    or report["unknown_actions"]
                    or report["delivery_attention"]
                    or report["held_dispatch_resources"]
                    or not objects["ok"]
                    or objects.get("pending_collection")
                    or objects.get("collection_scan_limit_reached")
                ):
                    return 1
            finally:
                lock.close()
        elif args.command in {
            "retention-preview",
            "retention-refs-preview",
            "retention-audit-preview",
            "retention-events-preview",
        }:
            lock = InstanceLock(paths)
            lock.acquire()
            try:
                report = offline_read(
                    paths.database,
                    SystemClock(),
                    UUIDs(),
                    lambda u: (
                        u.event_retention_preview(args.agent, cursor=args.cursor, limit=args.limit, as_of=args.as_of)
                        if args.command == "retention-events-preview"
                        else (
                            u.audit_retention_preview(
                                args.agent, cursor=args.cursor, limit=args.limit, as_of=args.as_of
                            )
                            if args.command == "retention-audit-preview"
                            else (
                                u.reference_preview(args.after, args.limit)
                                if args.command == "retention-refs-preview"
                                else u.retention_preview(args.after, args.limit)
                            )
                        )
                    ),
                )
                print(json.dumps(report))
            finally:
                lock.close()
        elif args.command == "retention-plan":
            print(json.dumps(plan_pruning(paths, args.output, after=args.after, limit=args.limit)))
        elif args.command == "retention-apply":
            print(json.dumps(apply_pruning(paths, read_pruning_plan(args.plan), args.backup)))
        elif args.command == "retention-cancel":
            print(json.dumps(cancel_pruning(paths, read_pruning_plan(args.plan, allow_future=True), args.reason)))
        elif args.command == "retention-audit-plan":
            print(
                json.dumps(
                    plan_audit_retention(
                        paths,
                        args.output,
                        agent_id=args.agent,
                        cursor=args.cursor,
                        limit=args.limit,
                        as_of=args.as_of,
                    )
                )
            )
        elif args.command == "retention-audit-apply":
            print(json.dumps(apply_audit_retention(paths, read_audit_plan(args.plan), args.backup)))
        elif args.command == "retention-audit-cancel":
            print(json.dumps(cancel_audit_retention(paths, read_audit_plan(args.plan, allow_future=True), args.reason)))
        elif args.command == "retention-events-plan":
            print(
                json.dumps(
                    plan_event_retention(
                        paths,
                        args.output,
                        agent_id=args.agent,
                        cursor=args.cursor,
                        limit=args.limit,
                        as_of=args.as_of,
                    )
                )
            )
        elif args.command == "retention-events-apply":
            print(json.dumps(apply_event_retention(paths, read_event_plan(args.plan), args.backup)))
        elif args.command == "retention-events-cancel":
            print(json.dumps(cancel_event_retention(paths, read_event_plan(args.plan, allow_future=True), args.reason)))
        elif args.command == "retention-refs-plan":
            print(json.dumps(plan_reference_release(paths, args.output, after=args.after, limit=args.limit)))
        elif args.command == "retention-refs-apply":
            print(json.dumps(apply_reference_release(paths, read_reference_plan(args.plan), args.backup)))
        elif args.command == "retention-refs-cancel":
            print(
                json.dumps(
                    cancel_reference_release(paths, read_reference_plan(args.plan, allow_future=True), args.reason)
                )
            )
        elif args.command == "indexes":
            print(json.dumps(derived_indexes(paths, rebuild=args.operation == "rebuild")))
        elif args.command == "backup":
            print(json.dumps(backup(paths, args.destination)))
        elif args.command == "restore":
            print(json.dumps(restore(args.source, args.destination)))
        elif args.command == "objects":
            if args.operation == "check":
                lock = InstanceLock(paths)
                lock.acquire()
                try:
                    report = cast(Record, object_report(paths))
                    print(json.dumps(report))
                    return (
                        0
                        if report["ok"]
                        and not report.get("pending_collection")
                        and not report.get("collection_scan_limit_reached")
                        else 1
                    )
                finally:
                    lock.close()
            if args.operation == "gc-plan":
                output = args.output.absolute()
                if output.resolve().is_relative_to(paths.home) or output.exists():
                    raise BusinessError("unsafe_plan_destination", "Use a new plan file outside the instance home")
                plan = cast(Record, plan_collection(paths))
                output.parent.mkdir(parents=True, exist_ok=True)
                durable_json(output, plan)
                print(
                    json.dumps(
                        {
                            "plan": str(output),
                            "candidates": len(cast(list[object], plan["entries"])),
                            "protected": plan["protected"],
                        }
                    )
                )
            else:
                if args.plan.stat().st_size > 128 * 1024 * 1024:
                    raise BusinessError("invalid_gc_plan", "Collection plan exceeds its size limit")
                try:
                    plan = cast(Record, json.loads(args.plan.read_text(encoding="utf-8")))
                except (ValueError, UnicodeError) as exc:
                    raise BusinessError("invalid_gc_plan", "Collection plan must contain valid UTF-8 JSON") from exc
                report = cast(Record, apply_collection(paths, plan))
                print(json.dumps(report))
                return 0 if report["complete"] else 1
        elif args.command == "diagnostics":
            if args.offline:
                lock = InstanceLock(paths)
                lock.acquire()
                try:
                    persisted = offline_status(paths.database, SystemClock(), UUIDs())

                    async def export_offline():
                        files = DiagnosticFiles(paths, StoredDiagnostics(paths))
                        try:
                            information: Record = {
                                "schema": 1,
                                "persisted": persisted,
                                "measurements": {"available": False, "reason": "offline_process"},
                                "host": await files.describe(),
                                "runtime": {"ready": False, "state": "offline"},
                            }
                            return await files.bundle(information)
                        finally:
                            await files.close()

                    content = asyncio.run(export_offline())
                finally:
                    lock.close()
            else:
                content = diagnostic_download(paths, args.url)
            print(json.dumps(publish_diagnostic(paths, args.destination, content)))
        elif args.command == "auth":
            token_path = paths.control / "owner.token"
            if not token_path.is_file():
                # 令牌由运行时首次启动时创建；未启动就取令牌要说清下一步，而不是抛 filesystem_error。
                raise BusinessError(
                    "owner_token_missing",
                    "The owner token is created by the first serve; run `serve` once, then retry `auth`",
                    status=404,
                )
            if not args.rotate:
                print(token_path.read_text(encoding="utf-8"))
            else:
                import httpx

                origin = local_origin(args.url)
                token = token_path.read_text(encoding="utf-8").strip()
                with httpx.Client(trust_env=False, follow_redirects=False, timeout=10) as client:
                    response = client.post(
                        origin + "/api/v1/owner-token/rotate", headers={"Authorization": "Bearer " + token}
                    )
                    response.raise_for_status()
                    print(response.json()["token"])
        elif args.command == "request":
            import httpx

            origin = local_origin(args.url)
            if not args.path.startswith("/api/v1/"):
                raise BusinessError("invalid_local_endpoint", "CLI requests require a local API endpoint")
            token = (paths.control / "owner.token").read_text().strip()
            with httpx.Client(trust_env=False, follow_redirects=False, timeout=10) as client:
                response = client.get(origin + args.path, headers={"Authorization": "Bearer " + token})
                response.raise_for_status()
                print(response.text)
        return 0
    except (BusinessError, OSError) as exc:
        print(
            json.dumps(
                {
                    "error": exc.code if isinstance(exc, BusinessError) else "filesystem_error",
                    "message": exc.message if isinstance(exc, BusinessError) else "Filesystem operation failed",
                }
            ),
            file=sys.stderr,
        )
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
