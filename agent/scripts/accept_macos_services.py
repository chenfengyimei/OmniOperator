"""Read-only macOS user/system evidence; never installs, loads, unloads or requests permissions."""

from __future__ import annotations

import argparse
import asyncio
import datetime as dt
import json
import sys
from pathlib import Path
from typing import cast

from omni.adapters.platform.deployment_files import CurrentDeploymentFiles, _destination, _json
from omni.adapters.platform.exchange_files import ExchangeFiles
from omni.adapters.platform.service_query import ServiceQuery
from omni.adapters.platform.service_readiness import ServiceReadiness
from omni.adapters.platform.service_registration import ServiceRegistration
from omni.adapters.primitives import SystemClock, UUIDs
from omni.application.ports import Record
from omni.application.service_deployment import ServiceDeployment
from omni.composition.lifecycle import close_until_quiescent
from omni.domain.contracts import BusinessError
from omni.domain.service_control import running_postcondition, start_postcondition
from omni.domain.service_exchange import ExchangePlan, progress, validate_exchange


def expectation(name: str, registration: Record, status: Record) -> bool:
    manager = status.get("manager")
    if not isinstance(manager, dict):
        return False
    confirmed = (
        registration.get("available") is True
        and type(registration.get("loaded")) is bool
        and type(manager.get("loaded")) is bool
    )
    if not confirmed:
        return False
    if name == "absent":
        return (
            registration.get("installed") is False
            and registration.get("definition_present") is False
            and registration.get("loaded") is False
            and manager.get("installed") is False
        )
    if name == "registered":
        return (
            registration.get("installed") is True
            and registration.get("definition_present") is True
            and registration.get("definition_matches") is True
            and registration.get("enabled") is True
        )
    if name == "loaded":
        return manager.get("loaded") is True and manager.get("definition_matches") is True
    if name == "ready":
        return status.get("ready") is True
    return name == "inspection"


async def inspect(
    files: CurrentDeploymentFiles, verification: Record, expected: str, exchange: ExchangePlan | None = None
) -> Record:
    deployment = cast(Record, verification["plan"])
    exchange_files = None if exchange is None else ExchangeFiles(files)
    if exchange is not None and (
        validate_exchange(exchange)["release"]["target"] != deployment
        or cast(Record, deployment["request"])["target"] not in {"macos-user", "macos-system"}
    ):
        raise BusinessError("service_exchange_target_changed", "Inspect the exact reviewed Mac exchange target")
    before = None if exchange_files is None else exchange_files.history(cast(ExchangePlan, exchange))
    registration, query, readiness = ServiceRegistration(), ServiceQuery(), ServiceReadiness()
    try:
        system = cast(Record, deployment["request"])["target"] == "macos-system"
        native = await query.query(deployment) if system else await registration.inspect(deployment)
        status = await ServiceDeployment(files, SystemClock(), UUIDs()).status(verification, query, readiness)
        matched = expectation(expected, native, status)
        binding: Record | None = None
        if exchange is not None and exchange_files is not None:
            after = exchange_files.history(exchange)
            _, _, state = progress(exchange, after)
            running = cast(Record, status["runtime"])
            current = (
                before == after
                and state == "finished"
                and running.get("boot_id") == exchange["start_target"]["boot_id"]
                and (running_postcondition if system else start_postcondition)(deployment, native, running)
                and status["ready"] is True
            )
            binding = {
                "exchange_id": exchange["id"],
                "exchange_sha256": exchange["sha256"],
                "state": state,
                "target_boot_id": exchange["start_target"]["boot_id"],
                "history_consistent": before == after,
                "current_reserved_boot_ready": current,
            }
            if expected == "ready":
                matched = matched and current
        return {
            "format": "omni-macos-service-acceptance-2",
            "scope": "read_only_inspection",
            "target": cast(Record, deployment["request"])["target"],
            "registration_source": "system_read_only_query" if system else "user_registration_inspection",
            "enabled_override_verified": native.get("enabled") is True,
            "administrator_operation_exercised": False,
            "recorded_at": dt.datetime.now(dt.timezone.utc).isoformat(),
            "plan_sha256": deployment["sha256"],
            "build_id": cast(Record, deployment["runtime"])["build_id"],
            "registration": native,
            "status": status,
            "expected": expected,
            "expectation_passed": matched,
            "exchange": binding,
            "installation_exercised": False,
            "activation_exercised": False,
            "uninstallation_exercised": False,
            "whole_platform_acceptance": False,
        }
    finally:

        async def close(budget: float) -> dict:
            deadline = asyncio.get_running_loop().time() + budget
            results = []
            for port in (registration, query, readiness):
                results.append(await port.close(max(0, deadline - asyncio.get_running_loop().time())))
            return {"complete": all(results), "residual": ["macos_service_inspection"]}

        await close_until_quiescent(close, 8)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--bundle", type=Path, required=True)
    parser.add_argument(
        "--exchange-plan", type=Path, help="Optional exact exchange; ready also requires its reserved boot"
    )
    parser.add_argument("--output", type=Path, required=True, help="New private JSON outside bundle/program/instance")
    parser.add_argument(
        "--expect", choices=("inspection", "absent", "registered", "loaded", "ready"), default="inspection"
    )
    args = parser.parse_args(argv)
    try:
        if sys.platform != "darwin":
            raise BusinessError("macos_required", "Run on the owner Mac; no service operation was performed")
        files = CurrentDeploymentFiles()
        verification = files.verify(str(args.bundle.absolute()))
        if verification.get("eligible") is not True:
            raise BusinessError("deployment_not_eligible", "Use the verified immutable macOS installation")
        plan = cast(Record, verification["plan"])
        if cast(Record, plan["request"])["target"] not in {"macos-user", "macos-system"}:
            raise BusinessError("macos_service_required", "Inspect a reviewed macOS service as its ordinary owner")
        if cast(Record, plan["request"])["target"] == "macos-system" and args.expect == "registered":
            raise BusinessError(
                "macos_system_inspection_only", "Read-only system query cannot attest administrator dispatch receipts"
            )
        output = _destination(args.output.absolute(), plan)
        bundle = args.bundle.absolute()
        if output == bundle or output.is_relative_to(bundle) or output.exists():
            raise BusinessError(
                "destination_exists_or_unsafe", "Choose a new private report outside the deployment bundle"
            )
        exchange = None if args.exchange_plan is None else validate_exchange(files.read(args.exchange_plan.absolute()))
        report = asyncio.run(inspect(files, verification, args.expect, exchange))
        if exchange is None:
            files._publish_json(output, _json(report))
        else:
            ExchangeFiles(files)._publish(exchange, report, str(output))
        print(
            json.dumps(
                {
                    "output": str(output),
                    "expected": args.expect,
                    "passed": report["expectation_passed"],
                    "scope": report["scope"],
                }
            )
        )
        return 0 if report["expectation_passed"] else 1
    except (BusinessError, OSError) as error:
        print(
            json.dumps(
                {"error_code": error.code if isinstance(error, BusinessError) else "service_evidence_filesystem_error"}
            ),
            file=sys.stderr,
        )
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
