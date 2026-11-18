"""Private offline maintenance journal; never migrates, replays or removes instance data."""

from __future__ import annotations

import hashlib
import itertools
from collections.abc import Callable, Generator
from contextlib import contextmanager
from pathlib import Path
from typing import cast

from omni.adapters.object_store import sync_directory
from omni.adapters.persistence.deployed_instance import verify_deployed_instance
from omni.adapters.platform.runtime.paths import InstanceLock, InstancePaths, PrivateFileLock
from omni.adapters.platform.runtime.permissions import assert_private, private_path
from omni.adapters.platform.services.deployment_files import (
    CurrentDeploymentFiles,
    decode,
    destination,
    json_bytes,
    no_links,
    read_file,
)
from omni.application.ports import Record
from omni.domain.contracts import BusinessError
from omni.domain.service_operations import validate_operation

MAX_EVENTS = 64


def _digest(value: Record) -> str:
    return hashlib.sha256(json_bytes(value)).hexdigest()


class ServiceOperationFiles:
    def __init__(
        self,
        *,
        validate: Callable[[object], Record] = cast(Callable[[object], Record], validate_operation),
        namespace: str = "service-operations",
        exchange: Record | None = None,
    ) -> None:
        if namespace not in {"service-operations", "service-controls"}:
            raise ValueError("Fixed service journal namespace required")
        self._validate, self.namespace = validate, namespace
        self.exchange = exchange
        self._publisher = CurrentDeploymentFiles()

    def publish(self, operation: Record, output: str) -> Record:
        checked = self._validate(operation)
        target = destination(output, cast(Record, checked["deployment"]))
        bundle = Path(cast(str, cast(Record, cast(Record, checked["deployment"])["request"])["bundle_dir"]))
        if target == bundle or target.is_relative_to(bundle):
            raise BusinessError(
                "service_operation_destination_unsafe", "Keep operation plans outside immutable bundles"
            )
        self._publisher.publish_json(target, json_bytes(checked))
        return {
            "path": str(target),
            "operation_id": checked["id"],
            "sha256": checked["sha256"],
            "executed": False,
            "steps": checked["steps"],
            "starts_service": checked["starts_service"],
            "stops_service": checked["stops_service"],
            "deletes_instance_data": checked["deletes_instance_data"],
        }

    def _paths(self, operation: Record) -> tuple[InstancePaths, Path, Path]:
        checked = self._validate(operation)
        paths = InstancePaths(Path(cast(str, cast(Record, cast(Record, checked["deployment"])["request"])["home"])))
        root = paths.control / self.namespace
        folder = root / cast(str, checked["id"])
        for path in (paths.home, paths.control, root, folder):
            no_links(path)
            if path.exists():
                assert_private(path)
        return paths, root, folder

    @contextmanager
    def exclusive(self, operation: Record) -> Generator[None, None, None]:
        checked = self._validate(operation)
        paths, root, _ = self._paths(checked)
        if not paths.database.is_file() or not paths.control.is_dir():
            raise BusinessError("service_instance_unavailable", "Initialize the independent V2 home first")
        no_links(paths.database)
        if (paths.control / "instance.lock").exists():
            read_lock = paths.control / "instance.lock"
            assert_private(read_lock)
            if read_lock.stat().st_nlink != 1:
                raise BusinessError("unsafe_lock", "Instance lock cannot have multiple links")
        with self.maintenance(operation):
            lock = InstanceLock(paths)
            lock.acquire()
            try:
                verify_deployed_instance(
                    paths.database,
                    cast(str, cast(Record, cast(Record, checked["deployment"])["request"])["instance_id"]),
                    schema_version=cast(
                        str, cast(Record, cast(Record, checked["deployment"])["runtime"])["schema_head"]
                    ),
                )
                if root.exists():
                    active = root / "active.json"
                    if active.exists() or active.is_symlink():
                        value = self._private_json(active)
                        if value != self._marker(checked):
                            raise BusinessError(
                                "service_operation_unresolved",
                                "Reconcile the earlier service operation before another change",
                            )
                yield
            finally:
                lock.close()

    @contextmanager
    def maintenance(self, operation: Record) -> Generator[None, None, None]:
        paths, _, _ = self._paths(operation)
        mutex = PrivateFileLock(paths.control / "service-maintenance.lock")
        mutex.acquire()
        try:
            reservation = paths.control / "service-exchange.json"
            if reservation.exists() or reservation.is_symlink() or reservation.is_junction():
                if self.exchange is None or self._private_json(reservation) != self.exchange:
                    raise BusinessError("service_exchange_reserved", "The reviewed exchange owns service maintenance")
            elif self.exchange is not None:
                raise BusinessError("service_exchange_reservation_missing", "The exchange reservation changed")
            for namespace in ("service-operations", "service-controls"):
                marker = paths.control / namespace / "active.json"
                if (marker.exists() or marker.is_symlink() or marker.is_junction()) and (
                    namespace != self.namespace or self._private_json(marker) != self._marker(operation)
                ):
                    raise BusinessError(
                        "service_operation_unresolved", "Resolve the earlier service maintenance intent first"
                    )
            yield
        finally:
            mutex.close()

    @staticmethod
    def _private_json(path: Path, limit: int = 2 * 1024 * 1024) -> Record:
        assert_private(path)
        return decode(read_file(path, limit, single=True))

    @staticmethod
    def _marker(operation: Record) -> Record:
        return {"format": "omni-service-active-1", "operation_id": operation["id"], "sha256": operation["sha256"]}

    def latest(self, operation: Record) -> Record | None:
        _, _, folder = self._paths(operation)
        if not folder.exists():
            active = folder.parent / "active.json"
            if not active.exists() or self._private_json(active) != self._marker(operation):
                return None
            return {
                "operation_id": operation["id"],
                "operation_sha256": operation["sha256"],
                "state": "intent",
                "effect": "unknown",
                "terminal": False,
            }
        if not (folder / "operation.json").exists():
            active = folder.parent / "active.json"
            if self._private_json(active) != self._marker(operation) or any(folder.iterdir()):
                raise BusinessError(
                    "service_journal_invalid", "Incomplete maintenance journal requires explicit review"
                )
            return {
                "operation_id": operation["id"],
                "operation_sha256": operation["sha256"],
                "state": "intent",
                "effect": "unknown",
                "terminal": False,
            }
        value = self._private_json(folder / "operation.json")
        if value != operation:
            raise BusinessError("service_journal_changed", "Operation identity differs from the maintenance journal")
        names = list(itertools.islice(folder.iterdir(), MAX_EVENTS + 2))
        events = sorted(path for path in names if path.name != "operation.json")
        if len(names) > MAX_EVENTS + 1 or any(path.name != f"{index:04}.json" for index, path in enumerate(events)):
            raise BusinessError("service_journal_invalid", "Maintenance journal is not a bounded contiguous history")
        previous = "0" * 64
        result: Record = {
            "operation_id": operation["id"],
            "operation_sha256": operation["sha256"],
            "state": "intent",
            "effect": "unknown",
            "terminal": False,
        }
        for index, path in enumerate(events):
            entry = self._private_json(path, 16384)
            if (
                set(entry) != {"sequence", "previous_sha256", "record", "sha256"}
                or entry["sequence"] != index
                or entry["previous_sha256"] != previous
            ):
                raise BusinessError("service_journal_invalid", "Maintenance journal linkage changed")
            content = {key: item for key, item in entry.items() if key != "sha256"}
            if entry["sha256"] != _digest(content) or not isinstance(entry["record"], dict):
                raise BusinessError("service_journal_changed", "Maintenance journal fingerprint changed")
            result = cast(Record, entry["record"])
            if result.get("operation_sha256") != operation["sha256"] or result.get("operation_id") != operation["id"]:
                raise BusinessError("service_journal_changed", "Maintenance receipt belongs to another operation")
            previous = str(entry["sha256"])
        return result

    def activate(self, operation: Record) -> None:
        _, root, folder = self._paths(operation)
        if folder.exists():
            raise BusinessError(
                "service_operation_needs_reconciliation", "Existing maintenance intent cannot be replayed"
            )
        if not root.exists():
            root.mkdir(mode=0o700)
            private_path(root, directory=True)
            sync_directory(root.parent)
        if (root / "active.json").exists():
            raise BusinessError("service_operation_unresolved", "An earlier maintenance intent is unresolved")
        self._publisher.publish_json(root / "active.json", json_bytes(self._marker(operation)))
        folder.mkdir(mode=0o700)
        private_path(folder, directory=True)
        sync_directory(root)
        self._publisher.publish_json(folder / "operation.json", json_bytes(operation))

    def dispatched(self, operation: Record) -> bool:
        self.latest(operation)  # Refuse truncated, changed or noncontiguous histories.
        _, _, folder = self._paths(operation)
        if not folder.exists():
            return False
        for path in folder.iterdir():
            if path.name == "operation.json":
                continue
            record = self._private_json(path, 16384)["record"]
            if not isinstance(record, dict):
                raise BusinessError("service_journal_invalid", "Maintenance event is not a structured record")
            if record.get("state") == "dispatch":
                return True
        return False

    def append(self, operation: Record, result: Record) -> None:
        _, _, folder = self._paths(operation)
        self.latest(operation)
        if not folder.exists():
            folder.mkdir(mode=0o700)
            private_path(folder, directory=True)
            sync_directory(folder.parent)
        if not (folder / "operation.json").exists():
            self._publisher.publish_json(folder / "operation.json", json_bytes(operation))
        events = sorted(path for path in folder.iterdir() if path.name != "operation.json")
        if len(events) >= MAX_EVENTS:
            raise BusinessError("service_journal_full", "Maintenance history requires explicit administrator review")
        # Reserve room for a final receipt before any external mutation.
        if result.get("state") == "dispatch" and len(events) >= MAX_EVENTS - 2:
            raise BusinessError("service_journal_full", "No capacity remains for a durable completion receipt")
        previous = self._private_json(events[-1])["sha256"] if events else "0" * 64
        entry: Record = {"sequence": len(events), "previous_sha256": previous, "record": result}
        data = json_bytes({**entry, "sha256": _digest(entry)})
        if len(data) > 16384:
            raise BusinessError("service_journal_record_limit", "Maintenance receipts contain bounded metadata only")
        self._publisher.publish_json(folder / f"{len(events):04}.json", data)

    def settled(self, operation: Record) -> None:
        _, root, _ = self._paths(operation)
        latest = self.latest(operation)
        if latest is None or latest.get("terminal") is not True:
            raise BusinessError(
                "service_operation_unresolved", "Only a durable terminal receipt can release maintenance intent"
            )
        active = root / "active.json"
        if active.exists():
            if self._private_json(active) != self._marker(operation):
                raise BusinessError("service_journal_changed", "Active maintenance identity changed")
            active.unlink()
            sync_directory(root)
