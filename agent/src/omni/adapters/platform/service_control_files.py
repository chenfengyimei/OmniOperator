"""Service lifecycle journal and once-only startup reservation; reuses registration audit and file-lock ownership."""

from __future__ import annotations

from collections.abc import Generator
from contextlib import contextmanager
from pathlib import Path
from typing import cast

from ...application.ports import Record
from ...domain.contracts import BusinessError
from ...domain.service_control import validate_control
from ..object_store import sync_directory
from ..persistence.deployed_instance import verify_deployed_instance
from .deployment_files import destination, json_bytes, no_links, publish_json
from .paths import InstanceLock
from .runtime_identity import read_private
from .service_launch import reserve_launch
from .service_operation_files import ServiceOperationFiles


class ServiceControlFiles(ServiceOperationFiles):
    def __init__(self, *, exchange: Record | None = None) -> None:
        super().__init__(
            validate=lambda value: cast(Record, validate_control(value)), namespace="service-controls", exchange=exchange
        )

    def publish(self, operation: Record, output: str) -> Record:
        value = validate_control(operation)
        path = destination(output, cast(Record, value["deployment"]))
        request = cast(Record, cast(Record, value["deployment"])["request"])
        bundle = Path(str(request["bundle_dir"]))
        if path == bundle or path.is_relative_to(bundle):
            raise BusinessError("service_control_output_unsafe", "Keep control plans outside immutable bundles")
        publish_json(self._publisher, path, json_bytes(dict(value)))
        return {
            "path": str(path),
            "operation_id": value["id"],
            "sha256": value["sha256"],
            "action": value["action"],
            "boot_id": value["boot_id"],
            "graceful_only": True,
            "executed": False,
        }

    @contextmanager
    def exclusive(self, operation: Record) -> Generator[None, None, None]:
        paths, _, _ = self._paths(operation)
        if not paths.database.is_file() or not paths.control.is_dir():
            raise BusinessError("service_instance_unavailable", "An initialized instance is required")
        with self.maintenance(operation):
            yield

    def reserve(self, operation: Record) -> None:
        value = validate_control(operation)
        paths, _, _ = self._paths(operation)
        lock = InstanceLock(paths)
        lock.acquire()
        try:
            no_links(paths.database)
            if paths.database.stat().st_nlink != 1:
                raise BusinessError(
                    "service_instance_unavailable", "The instance database must be a single regular file"
                )
            request = cast(Record, cast(Record, value["deployment"])["request"])
            verify_deployed_instance(paths.database, str(request["instance_id"]))
            reserve_launch(paths.control, value)
        finally:
            lock.close()

    def dispatched(self, operation: Record) -> bool:
        if super().dispatched(operation):
            return True
        value = validate_control(operation)
        paths, _, _ = self._paths(operation)
        evidence = (
            paths.control / "service-launches" / (value["id"] + ".json"),
            paths.control / "service-drains" / (value["boot_id"] + ".json"),
        )
        return any(path.exists() or path.is_symlink() or path.is_junction() for path in evidence)

    def finish_launch(self, operation: Record) -> None:
        value = validate_control(operation)
        paths, _, _ = self._paths(operation)
        expected = {
            "operation_id": value["id"],
            "operation_sha256": value["sha256"],
            "boot_id": value["boot_id"],
            "service_plan_sha256": value["deployment"]["sha256"],
        }
        if self._private_json(paths.control / "service-launches" / (value["id"] + ".json")) != expected:
            raise BusinessError("service_launch_claim_changed", "The reviewed startup claim is unverified")
        ticket = paths.control / "launch-ticket.json"
        no_links(ticket)
        if ticket.exists():
            if self._private_json(ticket) != value:
                raise BusinessError("service_launch_changed", "Startup reservation changed")
            ticket.unlink()
            sync_directory(ticket.parent)

    def closed(self, operation: Record) -> bool:
        value = validate_control(operation)
        request = cast(Record, cast(Record, value["deployment"])["request"])
        paths, _, _ = self._paths(operation)
        lock = InstanceLock(paths)
        try:
            lock.acquire()
        except BusinessError as exc:
            if exc.code == "instance_busy":
                return False
            raise
        try:
            from .service_readiness import decode

            report = decode(read_private(paths.control / "runtime.json"))
            return (
                report.get("format") == "omni-runtime-identity-1"
                and report.get("state") == "stopped"
                and report.get("boot_id") == value["boot_id"]
                and report.get("instance_id") == request["instance_id"]
                and report.get("service_plan_sha256") == value["deployment"]["sha256"]
            )
        finally:
            lock.close()
