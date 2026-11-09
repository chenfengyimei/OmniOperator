"""Consistent release snapshots and quarantined new-directory restores under caller-owned instance locks."""

from __future__ import annotations

import hashlib
import os
import stat
from pathlib import Path

from ...domain.contracts import BusinessError
from ...domain.releases import ReleasePlan, ReleasePrepared, ReleaseSnapshot, digest
from ..platform.deployment_files import _no_links
from ..platform.release_files import ReleaseFiles
from .deployed_instance import verify_deployed_instance
from .maintenance import _restore_verified, create_snapshot, fingerprint, safe_child, verify_snapshot
from .object_inventory import verify_objects
from .schema_upgrade import route, upgrade_target


def failed(code: str) -> BusinessError:
    return BusinessError(code, "Release data changed or is incomplete; preserve it and prepare a new reviewed release")


def database_witness(home: Path) -> list[dict]:
    """Main/WAL/journal bytes are a conservative continuity proof, never a copy or a live-state restore."""
    rows = []
    for name in ("omni.db", "omni.db-wal", "omni.db-journal"):
        path = safe_child(home, "data/" + name)
        if not path.exists():
            rows.append({"path": name, "sha256": None, "bytes": None})
            continue
        descriptor = os.open(path, os.O_RDONLY | getattr(os, "O_BINARY", 0) | getattr(os, "O_NOFOLLOW", 0))
        with os.fdopen(descriptor, "rb") as stream:
            before = os.fstat(stream.fileno())
            if not stat.S_ISREG(before.st_mode) or before.st_nlink != 1:
                raise failed("release_database_changed")
            proof = hashlib.sha256()
            while block := stream.read(1048576):
                proof.update(block)
            after = os.fstat(stream.fileno())
        fields = lambda info: (info.st_dev, info.st_ino, info.st_size, info.st_mtime_ns, info.st_nlink)
        if fields(before) != fields(after) or fields(after) != fields(path.stat(follow_symlinks=False)):
            raise failed("release_database_changed")
        rows.append({"path": name, "sha256": proof.hexdigest(), "bytes": before.st_size})
    return rows


class ReleaseData:
    def __init__(self, files: ReleaseFiles) -> None:
        self.files = files

    def schema_upgrade(self, source: dict, target: dict) -> dict:
        return route(source, target)

    def source(self, plan: ReleasePlan) -> None:
        self.files.owned(plan)
        if (
            plan["schema_upgrade"] is not None
            and route(plan["source"]["runtime"], plan["target"]["runtime"]) != plan["schema_upgrade"]
        ):
            raise failed("release_schema_route_changed")
        source, _ = self.files.homes(plan)
        verify_deployed_instance(
            source.database,
            plan["source"]["request"]["instance_id"],
            schema_version=plan["source"]["runtime"]["schema_head"],
        )

    def witness(self, plan: ReleasePlan, side: str) -> list[dict]:
        self.files.owned(plan)
        home = self.files.homes(plan)[0 if side == "source" else 1]
        # SQLite's read-only identity probe may create an empty WAL. Establish
        # that state before sealing the witness; never ignore real WAL changes.
        verify_deployed_instance(
            home.database, plan[side]["request"]["instance_id"], schema_version=plan[side]["runtime"]["schema_head"]
        )
        return database_witness(home.home)

    def snapshot(self, plan: ReleasePlan) -> ReleaseSnapshot:
        self.source(plan)
        source, _ = self.files.homes(plan)
        if (
            plan["recovery"] is not None
            and database_witness(source.home) != plan["recovery"]["expected_source_witness"]
        ):
            raise failed("release_recovery_source_changed")
        destination = Path(plan["paths"]["backup"])
        _no_links(destination)
        create_snapshot(source.database, source.objects, destination)
        manifest = verify_snapshot(destination)
        digest = fingerprint(destination / "manifest.json")
        restore_path, restore_sha = destination, digest
        if plan["recovery"] is not None:
            original = Path(plan["recovery"]["snapshot"])
            verify_snapshot(original)
            original_sha = fingerprint(original / "manifest.json")
            if original_sha != plan["recovery"]["manifest_sha256"]:
                raise failed("release_recovery_snapshot_changed")
            if plan["recovery"]["restore_policy"] == "original_snapshot":
                restore_path, restore_sha = original, original_sha
        restoring_original = plan["recovery"] is not None and plan["recovery"]["restore_policy"] == "original_snapshot"
        verify_deployed_instance(
            restore_path / "data/omni.db",
            plan["source"]["request"]["instance_id"],
            schema_version=plan["target" if restoring_original else "source"]["runtime"]["schema_head"],
        )
        return {
            "backup": str(destination),
            "backup_manifest_sha256": digest,
            "restore_from": str(restore_path),
            "restore_manifest_sha256": restore_sha,
            "objects": len(manifest["files"]) - 1,
            "source_witness": database_witness(source.home),
        }

    def restore(self, plan: ReleasePlan, snapshot: ReleaseSnapshot) -> ReleasePrepared:
        self.files.owned(plan)
        _, target = self.files.homes(plan)
        self.files.require_fence(plan, "target")
        source = Path(snapshot["restore_from"])
        manifest = verify_snapshot(source)
        if fingerprint(source / "manifest.json") != snapshot["restore_manifest_sha256"]:
            raise failed("release_snapshot_changed")
        if target.database.exists() or (target.home / "restore-report.json").exists():
            raise failed("release_target_exists")
        _restore_verified(source, target.home, manifest, snapshot["restore_manifest_sha256"], False)
        restored_sha = fingerprint(target.home / "restore-report.json")
        migration_sha = None
        if plan["schema_upgrade"] is not None:
            self.files.installations(plan)
            migration_sha = upgrade_target(plan, target, restored_sha)
        verify_deployed_instance(
            target.database,
            plan["target"]["request"]["instance_id"],
            schema_version=plan["target"]["runtime"]["schema_head"],
        )
        return {
            **snapshot,
            "target_witness": database_witness(target.home),
            "restore_report_sha256": restored_sha,
            "schema_upgrade_report_sha256": migration_sha,
            "credentials_restored": False,
            "execution_quarantined": True,
        }

    def verify(self, plan: ReleasePlan, prepared: ReleasePrepared) -> None:
        self.files.owned(plan)
        source, target = self.files.homes(plan)
        for text, key in (
            (prepared["backup"], "backup_manifest_sha256"),
            (prepared["restore_from"], "restore_manifest_sha256"),
        ):
            path = Path(text)
            _no_links(path)
            verify_snapshot(path)
            if fingerprint(path / "manifest.json") != prepared[key]:
                raise failed("release_snapshot_changed")
        if database_witness(source.home) != prepared["source_witness"]:
            raise failed("release_source_data_changed")
        if database_witness(target.home) != prepared["target_witness"]:
            raise failed("release_target_data_changed")
        report = safe_child(target.home, "restore-report.json")
        if fingerprint(report) != prepared["restore_report_sha256"]:
            raise failed("release_restore_report_changed")
        migration = plan["schema_upgrade"]
        if migration is not None:
            if route(plan["source"]["runtime"], plan["target"]["runtime"]) != migration:
                raise failed("release_schema_route_changed")
            proof_path = safe_child(target.home, "run/schema-upgrade-report.json")
            if fingerprint(proof_path) != prepared["schema_upgrade_report_sha256"]:
                raise failed("release_schema_report_changed")
            proof = self.files.deployments.read(proof_path)
            if (
                proof.get("complete") is not True
                or proof.get("release_sha256") != plan["sha256"]
                or proof.get("route_sha256") != digest(migration)
                or proof.get("source") != migration["source"]
                or proof.get("target") != migration["target"]
                or proof.get("restore_report_sha256") != prepared["restore_report_sha256"]
                or proof.get("integrity") != "ok"
                or proof.get("foreign_key_errors") != 0
            ):
                raise failed("release_schema_report_changed")
            intent = self.files.deployments.read(safe_child(target.home, "run/schema-upgrade-intent.json"))
            if intent != {
                key: proof[key]
                for key in (
                    "format",
                    "release_id",
                    "release_sha256",
                    "route_sha256",
                    "source",
                    "target",
                    "restore_report_sha256",
                )
            }:
                raise failed("release_schema_report_changed")
        elif prepared["schema_upgrade_report_sha256"] is not None:
            raise failed("release_schema_report_changed")
        verify_deployed_instance(
            target.database,
            plan["target"]["request"]["instance_id"],
            schema_version=plan["target"]["runtime"]["schema_head"],
        )
        verify_objects(source.database, source.objects)
        verify_objects(target.database, target.objects)
