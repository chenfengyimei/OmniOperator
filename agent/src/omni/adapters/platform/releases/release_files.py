"""Private release journals, installation bytes and startup fences; no database or native registration."""

from __future__ import annotations

import itertools
from collections.abc import Generator
from contextlib import contextmanager
from pathlib import Path
from typing import Any, cast

from omni.application.ports import Record
from omni.domain.contracts import BusinessError
from omni.domain.recovery_authority import RecoveryAuthority
from omni.domain.release_fence import fence
from omni.domain.releases import ReleaseEvent, ReleasePlan, digest, validate_release
from omni.domain.service_deployment import created
from omni.domain.service_exchange import ExchangePlan, marker
from omni.adapters.object_store import sync_directory
from omni.adapters.platform.services.deployment_files import LAYERS, MAX_BYTES, CurrentDeploymentFiles, json_bytes, no_links, path_of, proof, publish_json, python_files, read_file, walk
from omni.adapters.platform.runtime.paths import InstanceLock, InstancePaths, PrivateFileLock
from omni.adapters.platform.runtime.permissions import assert_private, private_path

MAX_EVENTS = 32
TRANSITIONS: dict[str, set[str]] = {
    "": {"intent", "drain_intent"},
    "drain_intent": {"drained", "drain_unknown"},
    "drain_unknown": {"drained", "drain_unknown"},
    "drained": {"intent"},
    "intent": {"snapshot", "abandoned"},
    "snapshot": {"restored", "abandoned"},
    "restored": {"prepared", "abandoned"},
    "prepared": {"selection_intent", "abandoned"},
    "selection_intent": {"selection_committed"},
    "selection_committed": {"selected"},
    "selected": set(),
    "abandoned": set(),
}


def failed(code: str = "release_journal_invalid") -> BusinessError:
    return BusinessError(code, "Inspect the exact release journal; incomplete work cannot be replayed or overwritten")


class ReleaseFiles:
    def __init__(self, deployments: CurrentDeploymentFiles, *, exchange: Record | None = None) -> None:
        self.deployments = deployments
        self.exchange = exchange
        self._claims: set[Path] = set()

    @staticmethod
    def homes(plan: ReleasePlan) -> tuple[InstancePaths, InstancePaths]:
        return (
            InstancePaths(path_of(plan["source"]["request"]["home"])),
            InstancePaths(path_of(plan["target"]["request"]["home"])),
        )

    def publish(self, plan: ReleasePlan, output: str) -> Record:
        checked = validate_release(plan)
        path = path_of(output)
        no_links(path)
        for side in ("source", "target"):
            for text in (
                checked[side]["request"]["home"],
                checked[side]["request"]["bundle_dir"],
                checked[side]["runtime"]["python_prefix"],
                checked[side]["runtime"]["package_root"],
            ):
                root = Path(text)
                if path == root or path.is_relative_to(root) or root.is_relative_to(path):
                    raise failed("release_output_unsafe")
        protected = list(checked["paths"].values())
        if checked["recovery"] is not None:
            protected.append(checked["recovery"]["snapshot"])
            if checked["recovery"]["isolation"] is not None:
                protected.append(checked["recovery"]["isolation"]["exchange_journal"])
        if any(path == Path(text) or path.is_relative_to(Path(text)) for text in protected):
            raise failed("release_output_unsafe")
        publish_json(self.deployments, path, json_bytes(checked))
        return {"path": str(path), "release_id": checked["id"], "sha256": checked["sha256"], "executed": False}

    def installations(self, plan: ReleasePlan) -> None:
        validate_release(plan)
        self.isolation_authority(plan)
        if self.deployments.capture() != plan["target"]["runtime"]:
            raise failed("release_target_program_changed")
        self.reviewed_installation(plan["source"])

    def reviewed_installation(self, deployment: dict[str, Any]) -> None:
        """Static proof for removal only; never imports or executes the reviewed installation."""
        from omni.domain.service_deployment import validate_plan

        deployment = validate_plan(deployment)
        if self.deployments.artifact_plan(deployment["request"]["bundle_dir"]) != deployment:
            raise failed("release_source_bundle_changed")
        bundle = Path(deployment["request"]["bundle_dir"])
        for artifact in (bundle, *bundle.iterdir()):
            assert_private(artifact)
        source = deployment["runtime"]
        root = path_of(source["package_root"])
        no_links(root)
        candidates = [("__init__.py", root / "__init__.py")]
        candidates += [
            (layer + "/" + name, path) for layer in LAYERS for name, path in walk(root / layer, python_only=True)
        ]
        candidates += [("web_assets/" + name, path) for name, path in walk(root / "web_assets")]
        rows: list[Record] = []
        total = 0
        for name, path in sorted(candidates):
            data = read_file(path, MAX_BYTES)
            total += len(data)
            if total > MAX_BYTES:
                raise failed("release_source_program_changed")
            rows.append(proof(name, data))
        program = python_files(
            path_of(source["python"]), path_of(source["python_prefix"]), path_of(source["process_python"])
        )
        if rows != source["files"] or any(source[key] != value for key, value in program.items()):
            raise failed("release_source_program_changed")

    def isolation_authority(self, plan: ReleasePlan) -> RecoveryAuthority | None:
        original = self.isolation_exchange(plan)
        recovery = plan["recovery"]
        return None if original is None or recovery is None else recovery["isolation"]

    def isolation_exchange(self, plan: ReleasePlan) -> ExchangePlan | None:
        recovery = plan["recovery"]
        if recovery is None or recovery["isolation"] is None:
            return None
        from omni.domain.service_exchange import validate_exchange
        from omni.adapters.platform.services.exchange_files import ExchangeFiles

        authority = recovery["isolation"]
        original = validate_exchange(
            ExchangeFiles(self.deployments).read(path_of(authority["exchange_journal"]) / "plan.json")
        )
        history = ExchangeFiles(self.deployments).history(original)
        ref = cast(Record, history[-1]["proof"]) if history else cast(Record, {})
        release = original["release"]
        prepared = [row["data"] for row in self.history(release) if row["phase"] == "prepared"]
        if (
            not history
            or history[-1]["state"] != "isolated"
            or len(prepared) != 1
            or original["id"] != authority["exchange_id"]
            or original["sha256"] != authority["exchange_sha256"]
            or release["id"] != authority["release_id"]
            or release["sha256"] != authority["release_sha256"]
            or release["target"] != plan["source"]
            or original["journal"] != authority["exchange_journal"]
            or ref["isolation_id"] != authority["isolation_id"]
            or ref["isolation_sha256"] != authority["isolation_sha256"]
            or prepared[0]["backup"] != recovery["snapshot"]
            or prepared[0]["backup_manifest_sha256"] != recovery["manifest_sha256"]
        ):
            raise failed("release_isolation_changed")
        for side in ("source", "target"):
            self.require_fence(release, side)
        return original

    @staticmethod
    def _fence_path(home: InstancePaths) -> Path:
        return home.control / "release-fence.json"

    def require_fence(self, plan: ReleasePlan, side: str, missing_ok: bool = False) -> None:
        home = self.homes(plan)[0 if side == "source" else 1]
        path = self._fence_path(home)
        no_links(path)
        if not path.exists():
            if missing_ok:
                return
            raise failed("release_fence_changed")
        assert_private(path)
        recovery = plan["recovery"]
        authority = recovery["isolation"] if recovery is not None else None
        expected = (
            fence(authority["release_id"], authority["release_sha256"], "target")
            if side == "source" and authority is not None
            else fence(plan["id"], plan["sha256"], side)
        )
        if self.deployments.read(path) != expected:
            raise failed("release_fence_changed")

    def _publish_fence(self, plan: ReleasePlan, side: str) -> None:
        home = self.homes(plan)[0 if side == "source" else 1]
        path = self._fence_path(home)
        if path.exists():
            self.require_fence(plan, side)
        else:
            publish_json(self.deployments, path, json_bytes(fence(plan["id"], plan["sha256"], side)))

    @contextmanager
    def preparation(self, plan: ReleasePlan) -> Generator[None, None, None]:
        validate_release(plan)
        source = self.homes(plan)[0]
        no_links(source.control)
        assert_private(source.control)
        lock = PrivateFileLock(source.control / "release-preparation.lock")
        lock.acquire()
        try:
            yield
        finally:
            lock.close()

    @contextmanager
    def exclusive(self, plan: ReleasePlan, create_target: bool = False) -> Generator[None, None, None]:
        checked = validate_release(plan)
        source, target = self.homes(checked)
        if self._claims or not source.database.is_file():
            raise failed("release_instance_unavailable")
        for home in (source.home, target.home):
            no_links(home)
        source_lock, target_lock = InstanceLock(source), InstanceLock(target)
        original = self.isolation_exchange(checked)
        recovery = checked["recovery"]
        authority = None if original is None or recovery is None else recovery["isolation"]
        source_key = (
            (authority["release_id"], authority["release_sha256"])
            if authority is not None
            else (checked["id"], checked["sha256"])
        )
        source_lock.acquire(release=source_key)
        try:
            if authority is not None:
                self._rescue_selection(checked)
            if create_target:
                if target.home.exists() or target.home.is_symlink() or not target.home.parent.is_dir():
                    raise failed("release_target_exists")
                target.home.mkdir(mode=0o700)
                target.initialize()
            elif not target.home.is_dir():
                raise failed("release_target_missing")
            target_lock.acquire(release=(checked["id"], checked["sha256"]))
            self._claims = {source.home, target.home}
            try:
                for home in (source, target):
                    reservation = home.control / "service-exchange.json"
                    no_links(reservation)
                    if reservation.exists():
                        assert_private(reservation)
                        expected = marker(original) if original is not None and home == source else self.exchange
                        if expected is None or self.deployments.read(reservation) != expected:
                            raise failed("service_exchange_reserved")
                if create_target:
                    self._publish_fence(checked, "target")
                yield
            finally:
                self._claims.clear()
                target_lock.close()
        finally:
            source_lock.close()

    def _journal(self, plan: ReleasePlan) -> Path:
        path = path_of(plan["paths"]["journal"])
        no_links(path)
        if path.exists():
            assert_private(path)
        return path

    def history(self, plan: ReleasePlan) -> list[ReleaseEvent]:
        validate_release(plan)
        root = self._journal(plan)
        if not root.exists():
            return []
        assert_private(root / "plan.json")
        if self.deployments.read(root / "plan.json") != plan:
            raise failed("release_journal_changed")
        names = list(itertools.islice(root.iterdir(), MAX_EVENTS + 2))
        events = sorted(path for path in names if path.name != "plan.json")
        if len(names) > MAX_EVENTS + 1 or any(path.name != f"{index:04}.json" for index, path in enumerate(events)):
            raise failed()
        result: list[ReleaseEvent] = []
        previous, phase = "0" * 64, ""
        for index, path in enumerate(events):
            assert_private(path)
            value = self.deployments.read(path)
            if len(json_bytes(value)) > 16384 or set(value) != {"sequence", "previous_sha256", "record", "sha256"}:
                raise failed()
            content = {key: item for key, item in value.items() if key != "sha256"}
            raw_record = value["record"]
            if (
                type(value["sequence"]) is not int
                or value["sequence"] != index
                or value["previous_sha256"] != previous
                or digest(content) != value["sha256"]
                or not isinstance(raw_record, dict)
            ):
                raise failed()
            record = cast(Record, raw_record)
            if set(record) != {"release_id", "release_sha256", "phase", "recorded_at", "data"}:
                raise failed()
            if (
                record["release_id"] != plan["id"]
                or record["release_sha256"] != plan["sha256"]
                or not isinstance(record.get("phase"), str)
                or cast(str, record["phase"]) not in TRANSITIONS[phase]
                or not isinstance(record.get("data"), dict)
            ):
                raise failed()
            created(cast(str, record["recorded_at"]))
            phase_value = cast(str, record["phase"])
            if phase_value in {"drain_intent", "drain_unknown", "drained"}:
                stop, data = plan["source_stop"], cast(Record, record["data"])
                keys = {"operation_id", "operation_sha256"} | (
                    set[str]() if phase_value == "drain_intent" else {"error_code"}
                )
                if (
                    stop is None
                    or set(data) != keys
                    or data["operation_id"] != stop["id"]
                    or data["operation_sha256"] != stop["sha256"]
                    or (
                        "error_code" in keys
                        and (not isinstance(data["error_code"], str) or len(data["error_code"]) > 128)
                    )
                    or (phase_value == "drained" and data["error_code"] != "")
                ):
                    raise failed("release_drain_witness_changed")
            result.append(cast(ReleaseEvent, record))
            phase = phase_value
            previous = value["sha256"]
        return result

    def begin(self, plan: ReleasePlan) -> None:
        root = self._journal(plan)
        if root.exists() or not root.parent.is_dir():
            raise failed("release_journal_exists")
        root.mkdir(mode=0o700)
        private_path(root, directory=True)
        sync_directory(root.parent)
        publish_json(self.deployments, root / "plan.json", json_bytes(plan))

    def append(self, plan: ReleasePlan, phase: str, data: Record, at: str) -> None:
        history = self.history(plan)
        if phase not in TRANSITIONS[history[-1]["phase"] if history else ""]:
            raise failed("release_transition_invalid")
        if len(history) >= MAX_EVENTS - 1:
            raise failed("release_journal_full")
        root = self._journal(plan)
        previous = self.deployments.read(root / f"{len(history) - 1:04}.json")["sha256"] if history else "0" * 64
        content = {
            "sequence": len(history),
            "previous_sha256": previous,
            "record": {
                "release_id": plan["id"],
                "release_sha256": plan["sha256"],
                "phase": phase,
                "recorded_at": at,
                "data": data,
            },
        }
        raw = json_bytes({**content, "sha256": digest(content)})
        if len(raw) > 16384:
            raise failed("release_journal_record_limit")
        publish_json(self.deployments, root / f"{len(history):04}.json", raw)

    def retire_source(self, plan: ReleasePlan) -> None:
        self.owned(plan)
        if plan["recovery"] is not None and plan["recovery"]["isolation"] is not None:
            self.require_fence(plan, "source")
            self._rescue_selection(plan, publish=True)
            return
        self._publish_fence(plan, "source")

    def _rescue_selection(self, plan: ReleasePlan, *, publish: bool = False, required: bool = False) -> None:
        path = self.homes(plan)[0].control / "rescue-selection.json"
        no_links(path)
        expected = {
            "format": "omni-rescue-selection-1",
            "release_id": plan["id"],
            "release_sha256": plan["sha256"],
            "target_sha256": plan["target"]["sha256"],
        }
        if path.exists():
            assert_private(path)
            if self.deployments.read(path) != expected:
                raise failed("release_rescue_already_selected")
        elif publish:
            publish_json(self.deployments, path, json_bytes(expected))
        elif required:
            raise failed("release_rescue_selection_missing")

    def isolate(self, plan: ReleasePlan) -> None:
        self.owned(plan)
        self.retire_source(plan)
        self._publish_fence(plan, "target")

    def owned(self, plan: ReleasePlan) -> None:
        if self._claims != {home.home for home in self.homes(plan)}:
            raise failed("release_instance_unavailable")

    def activate_target(self, plan: ReleasePlan) -> None:
        self.owned(plan)
        self.require_fence(plan, "source")
        if plan["recovery"] is not None and plan["recovery"]["isolation"] is not None:
            self._rescue_selection(plan, required=True)
        self.require_fence(plan, "target", missing_ok=True)
        target = self._fence_path(self.homes(plan)[1])
        target.unlink(missing_ok=True)
        sync_directory(target.parent)
