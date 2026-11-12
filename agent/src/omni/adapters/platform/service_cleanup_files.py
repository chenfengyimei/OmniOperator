"""Separate cleanup journals and two-home reservations; preserves old unknown records, fences and business data."""

from __future__ import annotations

import itertools
from collections.abc import Generator
from contextlib import ExitStack, contextmanager
from pathlib import Path
from typing import Any, cast

from ...application.ports import Record
from ...domain.contracts import BusinessError
from ...domain.release_fence import validate_fence
from ...domain.releases import digest
from ...domain.service_cleanup import (
    CleanupPlan,
    overlaps,
    protected_paths,
    registration_key,
    registry,
    validate_cleanup,
)
from ...domain.service_exchange import marker, progress
from ..object_store import sync_directory
from .deployment_files import CurrentDeploymentFiles, json_bytes, no_links, path_of, publish_json
from .exchange_files import ExchangeFiles
from .paths import InstanceLock, InstancePaths, PrivateFileLock
from .permissions import assert_private, private_path
from .release_files import ReleaseFiles
from .service_operation_files import ServiceOperationFiles


def invalid(code: str = "service_cleanup_scope_changed") -> BusinessError:
    return BusinessError(code, "Preserve the isolated predecessor and reconcile the exact cleanup operation")


class CleanupPlanFiles:
    def __init__(self, deployments: CurrentDeploymentFiles) -> None:
        self.deployments = deployments

    def publish(self, plan: CleanupPlan, output: str) -> Record:
        checked, target = validate_cleanup(plan), path_of(output)
        no_links(target)
        windows = checked["operation"]["deployment"]["runtime"]["platform"] == "win32"
        if any(overlaps(str(target), name, windows) for name in protected_paths(checked["exchange"])):
            raise invalid("service_cleanup_output_unsafe")
        publish_json(self.deployments, target, json_bytes(dict(checked)))
        return {
            "path": str(target),
            "cleanup_id": checked["id"],
            "sha256": checked["sha256"],
            "side": checked["side"],
            "journal": checked["journal"],
            "executed": False,
            "starts_service": False,
            "stops_service": checked["stops_service"],
            "deletes_instance_data": False,
            "preserves_original_records": True,
            "late_effects_possible": True,
        }


class CleanupFiles(ServiceOperationFiles):
    def __init__(self, plan: CleanupPlan, deployments: CurrentDeploymentFiles) -> None:
        self.plan = validate_cleanup(plan)
        self.deployments = deployments
        self.exchanges, self.releases = ExchangeFiles(deployments), ReleaseFiles(deployments)
        self._owned = False
        super().__init__(validate=self._operation)

    def _operation(self, value: object) -> Record:
        if value != self.plan["operation"]:
            raise invalid()
        return self.plan["operation"]

    def _paths(self, operation: Record) -> tuple[InstancePaths, Path, Path]:
        self._operation(operation)
        operation_plan = self.plan["operation"]
        deployment = operation_plan["deployment"]
        paths = InstancePaths(path_of(cast(str, cast(dict[str, Any], deployment["request"])["home"])))
        journal = path_of(self.plan["journal"])
        root, folder = journal / self.namespace, journal / self.namespace / cast(str, operation_plan["id"])
        for path in (path_of(registry(self.plan["exchange"])), journal.parent, journal, root, folder):
            no_links(path)
            if path.exists():
                assert_private(path)
        if journal.exists():
            names = list(itertools.islice(journal.iterdir(), 3))
            if any(path.name not in {"review.json", self.namespace} for path in names) or len(names) > 2:
                raise invalid("service_cleanup_journal_invalid")
            review = journal / "review.json"
            if review.exists() and self._private_json(review) != self.plan:
                raise invalid("service_cleanup_review_changed")
            if not review.exists() and names:
                raise invalid("service_cleanup_review_missing")
        return paths, root, folder

    def homes(self) -> list[InstancePaths]:
        return sorted(
            self.releases.homes(self.plan["exchange"]["release"]), key=lambda value: str(value.home).casefold()
        )

    def reservation_path(self, home: InstancePaths) -> Path:
        return home.control / "service-cleanups" / (registration_key(self.plan["operation"]) + ".json")

    def reservation(self) -> Record:
        return {
            "format": "omni-service-cleanup-reservation-1",
            "cleanup_id": self.plan["id"],
            "sha256": self.plan["sha256"],
            "journal": self.plan["journal"],
        }

    def _reservations(self) -> bool:
        present = False
        for home in self.homes():
            path = self.reservation_path(home)
            no_links(path)
            if path.parent.exists():
                assert_private(path.parent)
                if not path.parent.is_dir():
                    raise invalid("service_cleanup_reservation_invalid")
            if path.exists():
                if self._private_json(path, 8192) != self.reservation():
                    raise invalid("service_cleanup_unresolved")
                present = True
        return present

    def scope(self) -> None:
        exchange = self.plan["exchange"]
        history = self.exchanges.history(exchange)
        _, _, state = progress(exchange, history)
        if (
            state != "isolated"
            or len(history) != self.plan["event_count"]
            or digest(history) != self.plan["history_sha256"]
            or history[-1]["proof"] != self.plan["isolation"]
        ):
            raise invalid("service_cleanup_isolation_changed")
        self.releases.isolation_authority(exchange["release"])
        for side in ("source", "target"):
            self.releases.require_fence(exchange["release"], side)
        for home in self.exchanges.homes(exchange):
            if self._private_json(home.control / "service-exchange.json", 8192) != marker(exchange):
                raise invalid("service_cleanup_exchange_changed")

    @contextmanager
    def exclusive(self, operation: Record) -> Generator[None, None, None]:
        self._operation(operation)
        if self._owned:
            raise invalid("service_cleanup_busy")
        with self.exchanges.exclusive(self.plan["exchange"]), ExitStack() as stack:
            for home in self.homes():
                no_links(home.home)
                assert_private(home.home)
                no_links(home.control)
                assert_private(home.control)
                mutex = PrivateFileLock(home.control / "service-maintenance.lock")
                mutex.acquire()
                stack.callback(mutex.close)
            self.scope()
            for home in self.homes():
                fence = validate_fence(self._private_json(home.control / "release-fence.json", 4096))
                lock = InstanceLock(home)
                lock.acquire(release=(fence["release_id"], fence["plan_sha256"]))
                stack.callback(lock.close)
            self.scope()
            self._reservations()
            self._paths(operation)
            self._owned = True
            try:
                yield
            finally:
                self._owned = False

    def _require_owned(self) -> None:
        if not self._owned:
            raise invalid("service_cleanup_not_owned")

    @staticmethod
    def _directory(path: Path) -> None:
        no_links(path)
        if not path.exists():
            path.mkdir(mode=0o700)
            private_path(path, directory=True)
            sync_directory(path.parent)
        assert_private(path)
        if not path.is_dir():
            raise invalid("service_cleanup_journal_invalid")

    def _prepare(self) -> None:
        self._require_owned()
        root = path_of(registry(self.plan["exchange"]))
        journal = path_of(self.plan["journal"])
        for directory in (root, journal.parent, journal):
            self._directory(directory)
        review = journal / "review.json"
        if not review.exists():
            publish_json(self._publisher, review, json_bytes(dict(self.plan)))
        self._paths(self.plan["operation"])

    def latest(self, operation: Record) -> Record | None:
        result = super().latest(operation)
        if result is None and self._reservations():
            return {
                "operation_id": operation["id"],
                "operation_sha256": operation["sha256"],
                "state": "intent",
                "effect": "unknown",
                "terminal": False,
            }
        return result

    def activate(self, operation: Record) -> None:
        self._prepare()
        self._reservations()
        for home in self.homes():
            path = self.reservation_path(home)
            self._directory(path.parent)
            if not path.exists():
                publish_json(self._publisher, path, json_bytes(self.reservation()))
        super().activate(operation)

    def append(self, operation: Record, result: Record) -> None:
        self._prepare()
        _, root, _ = self._paths(operation)
        self._directory(root)
        super().append(operation, result)

    def settled(self, operation: Record) -> None:
        self._require_owned()
        result = self.latest(operation)
        if result is None or result.get("cleanup_complete") is not True:
            raise invalid("service_cleanup_native_pending")
        self._reservations()
        super().settled(operation)
        for home in self.homes():
            path = self.reservation_path(home)
            if path.exists():
                path.unlink()
                sync_directory(path.parent)


class CleanupDeployment(CurrentDeploymentFiles):
    def __init__(self, files: CleanupFiles) -> None:
        self.files = files

    def verify(self, bundle: str | Path) -> Record:
        deployment = self.files.plan["operation"]["deployment"]
        if str(bundle) != deployment["request"]["bundle_dir"]:
            raise invalid()
        self.files.scope()
        self.files.releases.reviewed_installation(deployment)
        return {
            "valid": True,
            "eligible": True,
            "plan": deployment,
            "eligibility": "reviewed_quarantined_registration_removal_only",
        }
