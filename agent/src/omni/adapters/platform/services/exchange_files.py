"""Private bounded exchange witnesses and maintenance reservations; no database, native calls or rollback."""

from __future__ import annotations

import itertools
from collections.abc import Generator, Mapping
from contextlib import ExitStack, contextmanager
from pathlib import Path
from typing import cast

from omni.adapters.object_store import sync_directory
from omni.adapters.platform.runtime.paths import InstancePaths, PrivateFileLock
from omni.adapters.platform.runtime.permissions import assert_private, private_path
from omni.adapters.platform.services.deployment_files import (
    CurrentDeploymentFiles,
    json_bytes,
    no_links,
    path_of,
    publish_json,
)
from omni.application.ports import Record
from omni.domain.contracts import BusinessError
from omni.domain.releases import digest
from omni.domain.service_exchange import ExchangePlan, administrator_approval, marker, progress, validate_exchange
from omni.domain.service_isolation import IsolationPlan, validate_isolation

MAX_EVENTS = 64


def invalid(code: str = "service_exchange_journal_invalid") -> BusinessError:
    return BusinessError(code, "Preserve the exchange witnesses and inspect the exact reserved operations")


class ExchangeFiles:
    def __init__(self, deployments: CurrentDeploymentFiles) -> None:
        self.deployments = deployments
        self._owned: str | None = None

    @staticmethod
    def homes(plan: ExchangePlan) -> list[InstancePaths]:
        # The predecessor stays quarantined and keeps its unknown maintenance records.
        # A rescue owns only its new target's reservation, never the predecessor marker.
        recovery = plan["release"]["recovery"]
        sides = ("target",) if recovery is not None and recovery["isolation"] is not None else ("source", "target")
        return sorted(
            [InstancePaths(path_of(plan["release"][side]["request"]["home"])) for side in sides],
            key=lambda paths: str(paths.home).casefold(),
        )

    def _root(self, plan: ExchangePlan) -> Path:
        root = path_of(plan["journal"])
        no_links(root)
        if root.exists():
            assert_private(root)
        return root

    def _read(self, path: Path) -> Record:
        no_links(path)
        assert_private(path)
        return self.deployments.read(path)

    def read(self, path: Path) -> Record:
        """Public bridge for a verified witness file; keeps the private checks in one place."""
        return self._read(path)

    def _require_owned(self, plan: ExchangePlan) -> None:
        if self._owned != plan["sha256"]:
            raise invalid("service_exchange_not_owned")

    def publish(self, plan: ExchangePlan, output: str) -> Record:
        checked = validate_exchange(plan)
        self._publish(checked, dict(checked), output)
        return {
            "path": str(path_of(output)),
            "exchange_id": checked["id"],
            "sha256": checked["sha256"],
            "executed": False,
            "starts_service": True,
            "stops_service": checked["stops_service"],
            "releases_activation_on_abandon": checked["releases_activation_on_abandon"],
            "automatic_rollback": False,
        }

    def publish_isolation(self, plan: IsolationPlan, output: str) -> Record:
        checked = validate_isolation(plan)
        self._publish(checked["exchange"], dict(checked), output)
        return {
            "path": str(path_of(output)),
            "isolation_id": checked["id"],
            "sha256": checked["sha256"],
            "executed": False,
            "blocks_startup": True,
            "starts_service": False,
            "stops_service": False,
        }

    def publish_administrator(self, plan: ExchangePlan, role: str, output: str) -> Record:
        from omni.domain.service_admin import socket_path

        checked = validate_exchange(plan)
        approval = administrator_approval(checked, role)
        self._publish(checked, approval, output)
        return cast(
            Record,
            {
                "path": str(path_of(output)),
                "approval_id": approval["id"],
                "sha256": approval["sha256"],
                "role": role,
                "protocol": approval["protocol"],
                "bundle_sha256": approval["broker_sha256"],
                "socket": socket_path(approval),
                "executed": False,
            },
        )

    def _publish(self, checked: ExchangePlan, document: Mapping[str, object], output: str) -> None:
        path = path_of(output)
        no_links(path)
        protected = [checked["journal"], *checked["release"]["paths"].values()]
        if checked["release"]["recovery"] is not None:
            protected.append(checked["release"]["recovery"]["snapshot"])
            authority = checked["release"]["recovery"]["isolation"]
            if authority is not None:
                protected.append(authority["exchange_journal"])
        for side in ("source", "target"):
            item = checked["release"][side]
            protected += [item["request"][key] for key in ("home", "bundle_dir", "working_directory")]
            protected += [item["runtime"][key] for key in ("python_prefix", "package_root")]
        if any(
            path == Path(text) or path.is_relative_to(Path(text)) or Path(text).is_relative_to(path)
            for text in protected
        ):
            raise invalid("service_exchange_output_unsafe")
        publish_json(self.deployments, path, json_bytes(document))

    @contextmanager
    def exclusive(self, plan: ExchangePlan) -> Generator[None, None, None]:
        checked = validate_exchange(plan)
        if self._owned is not None:
            raise invalid("service_exchange_busy")
        root = self._root(checked)
        if not root.parent.is_dir():
            raise invalid("service_exchange_parent_missing")
        lock = PrivateFileLock(root.parent / ("." + root.name + ".exchange.lock"))
        lock.acquire()
        self._owned = checked["sha256"]
        try:
            yield
        finally:
            self._owned = None
            lock.close()

    def history(self, plan: ExchangePlan) -> list[Record]:
        validate_exchange(plan)
        root = self._root(plan)
        if not root.exists():
            return []
        if not (root / "plan.json").exists() and not any(root.iterdir()):
            return []  # No child can have been dispatched before the plan is published.
        if self._read(root / "plan.json") != plan:
            raise invalid("service_exchange_plan_changed")
        names = list(itertools.islice(root.iterdir(), MAX_EVENTS + 2))
        events = sorted(path for path in names if path.name != "plan.json")
        if len(names) > MAX_EVENTS + 1 or any(path.name != f"{i:04}.json" for i, path in enumerate(events)):
            raise invalid()
        rows: list[Record] = []
        previous = "0" * 64
        for index, path in enumerate(events):
            value = self._read(path)
            if (
                len(json_bytes(value)) > 8192
                or set(value) != {"sequence", "previous_sha256", "record", "sha256"}
                or type(value["sequence"]) is not int
                or value["sequence"] != index
                or value["previous_sha256"] != previous
                or not isinstance(value["record"], dict)
            ):
                raise invalid()
            content = {key: item for key, item in value.items() if key != "sha256"}
            if value["sha256"] != digest(content):
                raise invalid()
            rows.append(cast(Record, value["record"]))
            previous = str(value["sha256"])
        progress(plan, rows)
        return rows

    @contextmanager
    def maintenance(self, plan: ExchangePlan) -> Generator[list[InstancePaths], None, None]:
        homes = self.homes(plan)
        with ExitStack() as stack:
            for home in homes:
                for path in (home.home, home.control):
                    no_links(path)
                    assert_private(path)
                lock = PrivateFileLock(home.control / "service-maintenance.lock")
                lock.acquire()
                stack.callback(lock.close)
            yield homes

    def claim(self, plan: ExchangePlan) -> None:
        self._require_owned(plan)
        history = self.history(plan)
        with self.maintenance(plan) as homes:
            expected = marker(plan)
            for home in homes:
                keys = (
                    ("remove_source",)
                    if str(home.home) == plan["release"]["source"]["request"]["home"]
                    else ("install_target", "start_target")
                )
                allowed = {(plan[key]["id"], plan[key]["sha256"]) for key in keys}
                reservation = home.control / "service-exchange.json"
                no_links(reservation)
                if history and not reservation.exists():
                    raise invalid("service_exchange_reservation_missing")
                if reservation.exists() and self._read(reservation) != expected:
                    raise invalid("service_exchange_reserved")
                for namespace in ("service-operations", "service-controls"):
                    active = home.control / namespace / "active.json"
                    no_links(active)
                    if active.exists():
                        value = self._read(active)
                        if (
                            set(value) != {"format", "operation_id", "sha256"}
                            or value.get("format") != "omni-service-active-1"
                            or (value.get("operation_id"), value.get("sha256")) not in allowed
                        ):
                            raise invalid("service_operation_unresolved")
            root = self._root(plan)
            if not root.exists():
                root.mkdir(mode=0o700)
                private_path(root, directory=True)
                sync_directory(root.parent)
            if not (root / "plan.json").exists():
                publish_json(self.deployments, root / "plan.json", json_bytes(dict(plan)))
            for home in homes:
                path = home.control / "service-exchange.json"
                no_links(path)
                if not path.exists():
                    publish_json(self.deployments, path, json_bytes(expected))

    def append(self, plan: ExchangePlan, record: Record) -> None:
        self._require_owned(plan)
        history = self.history(plan)
        progress(plan, [*history, record])
        limit = MAX_EVENTS if record["state"] in {"isolating", "isolated", "finished", "abandoned"} else MAX_EVENTS - 2
        if len(history) >= limit or (record["state"] == "dispatch" and len(history) >= MAX_EVENTS - 4):
            raise invalid("service_exchange_journal_full")
        root = self._root(plan)
        previous = self._read(root / f"{len(history) - 1:04}.json")["sha256"] if history else "0" * 64
        entry = {"sequence": len(history), "previous_sha256": previous, "record": record}
        data = json_bytes({**entry, "sha256": digest(entry)})
        if len(data) > 8192:
            raise invalid("service_exchange_record_limit")
        publish_json(self.deployments, root / f"{len(history):04}.json", data)

    def check_idle(self, plan: ExchangePlan) -> None:
        self._require_owned(plan)
        for home in self.homes(plan):
            for namespace in ("service-operations", "service-controls"):
                active = home.control / namespace / "active.json"
                no_links(active)
                if active.exists():
                    raise invalid("service_exchange_child_unresolved")

    def settled(self, plan: ExchangePlan) -> None:
        self._require_owned(plan)
        _, _, state = progress(plan, self.history(plan))
        if state not in {"finished", "abandoned"}:
            raise invalid("service_exchange_unresolved")
        with self.maintenance(plan) as homes:
            self.check_idle(plan)
            # Verify both reservations before removing either; partial unlink is safely repeatable.
            for home in homes:
                path = home.control / "service-exchange.json"
                no_links(path)
                if path.exists() and self._read(path) != marker(plan):
                    raise invalid("service_exchange_reserved")
            for home in homes:
                path = home.control / "service-exchange.json"
                if path.exists():
                    path.unlink()
                    sync_directory(home.control)
