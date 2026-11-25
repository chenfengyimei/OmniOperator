"""Wheel lifecycle and read-only installation preflight."""

from __future__ import annotations

import argparse
import json
from pathlib import Path
from typing import cast

from ...adapters.persistence.maintenance import durable_json, read_schema
from ...adapters.platform.runtime.paths import InstancePaths
from ...adapters.platform.runtime.permissions import private_path
from ...adapters.primitives import SystemClock
from ...application.ports import Record
from ...domain.contracts import BusinessError
from ...domain.installation import make_installation, validate_installation
from . import CommandPlugin


def _database_schema(database: Path) -> str:
    return read_schema(database)


def installation_report(paths: InstancePaths) -> dict[str, object]:
    from ...adapters.persistence.migration_config import head as migration_head

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
    from ...adapters.persistence.migration_config import head as migration_head

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


def installation_arguments(installation: argparse.ArgumentParser) -> None:
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
        "--dependency-bundle", type=Path, help="Local pinned dependency wheels for an isolated environment"
    )
    installation.add_argument("--dependency-sha256", default="", help="SHA256 of the dependency manifest.json")
    installation.add_argument(
        "--purge-data", action="store_true", help="Explicitly remove local data/objects/receipts during uninstall"
    )


def run_installation(args: argparse.Namespace, paths: InstancePaths) -> int:
    if args.operation != "environment" and (args.dependency_bundle is not None or args.dependency_sha256):
        raise BusinessError("environment_invalid", "Dependency options apply only to installation environment")
    if args.operation == "seal":
        print(json.dumps(seal_installation(paths)))
    elif args.operation == "artifact":
        if args.artifact is None or not args.sha256:
            raise BusinessError("artifact_missing", "Provide the wheel path and its recorded SHA256")
        from omni.adapters.platform.releases.wheel_artifact import inspect_wheel

        print(json.dumps(inspect_wheel(args.artifact, args.sha256)))
    elif args.operation == "stage":
        if args.artifact is None or not args.sha256:
            raise BusinessError("artifact_missing", "Provide the wheel path and its recorded SHA256")
        from omni.adapters.platform.releases.wheel_stage import stage_wheel

        print(json.dumps(stage_wheel(paths.home, args.artifact, args.sha256)))
    elif args.operation == "prepare":
        if not args.sha256:
            raise BusinessError("artifact_missing", "Provide the staged wheel SHA256")
        from omni.adapters.platform.releases.wheel_prepare import prepare_wheel

        print(json.dumps(prepare_wheel(paths.home, args.sha256)))
    elif args.operation == "switch-plan":
        if not args.sha256:
            raise BusinessError("artifact_missing", "Provide the prepared wheel SHA256")
        from omni.adapters.platform.releases.wheel_switch import switch_plan

        print(json.dumps(switch_plan(paths, args.sha256)))
    elif args.operation == "switch":
        if not args.sha256:
            raise BusinessError("artifact_missing", "Provide the prepared wheel SHA256")
        from omni.adapters.platform.releases.wheel_install import switch_candidate

        print(json.dumps(switch_candidate(paths, args.sha256)))
    elif args.operation == "install":
        if args.artifact is None or not args.sha256:
            raise BusinessError("artifact_missing", "Provide the wheel path and its recorded SHA256")
        from omni.adapters.platform.releases.wheel_install import install_candidate

        print(json.dumps(install_candidate(paths, args.artifact, args.sha256)))
    elif args.operation == "migrate":
        if not args.sha256:
            raise BusinessError("artifact_missing", "Provide the prepared wheel SHA256")
        from omni.adapters.platform.releases.wheel_install import migrate_candidate

        print(json.dumps(migrate_candidate(paths, args.sha256)))
    elif args.operation == "current":
        from omni.adapters.platform.releases.wheel_install import runtime_current

        print(json.dumps(runtime_current(paths)))
    elif args.operation == "uninstall":
        from omni.adapters.platform.releases.wheel_uninstall import uninstall_candidate

        print(json.dumps(uninstall_candidate(paths, args.sha256, purge=args.purge_data)))
    elif args.operation == "environment":
        if not args.sha256:
            raise BusinessError("artifact_missing", "Provide the staged wheel SHA256")
        from omni.adapters.platform.releases.wheel_environment import create_environment

        print(
            json.dumps(
                create_environment(
                    paths,
                    args.sha256,
                    dependency_bundle=args.dependency_bundle,
                    dependency_sha256=args.dependency_sha256,
                )
            )
        )
    elif args.operation == "rollback":
        from omni.adapters.platform.releases.wheel_install import rollback_candidate

        print(json.dumps(rollback_candidate(paths)))
    elif args.operation == "backup":
        if not args.sha256:
            raise BusinessError("artifact_missing", "Provide the prepared wheel SHA256")
        from omni.adapters.platform.releases.wheel_backup import backup_candidate

        print(json.dumps(backup_candidate(paths, args.sha256)))
    elif args.operation == "rollback-check":
        if not args.sha256:
            raise BusinessError("artifact_missing", "Provide the prepared wheel SHA256")
        from omni.adapters.platform.releases.wheel_rollback import rollback_check

        print(json.dumps(rollback_check(paths, args.sha256)))
    elif args.operation == "status":
        from omni.adapters.platform.releases.wheel_status import installation_status

        print(json.dumps(installation_status(paths)))
    elif args.operation == "smoke":
        if not args.sha256:
            raise BusinessError("artifact_missing", "Provide the prepared wheel SHA256")
        from omni.adapters.platform.releases.wheel_smoke import smoke_candidate

        print(json.dumps(smoke_candidate(paths, args.sha256)))
    else:
        print(json.dumps(installation_report(paths)))
    return 0


INSTALLATION = CommandPlugin(
    "installation",
    run_installation,
    help="V2 installation receipt and read-only upgrade preflight",
    configure=installation_arguments,
)
