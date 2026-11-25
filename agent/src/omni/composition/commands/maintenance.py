"""Offline backup, restore, object and index maintenance."""

from __future__ import annotations

import argparse
import json
from pathlib import Path
from typing import cast

from ...adapters.persistence.maintenance import backup, derived_indexes, durable_json, object_report, restore
from ...adapters.persistence.object_gc import apply_collection, plan_collection
from ...adapters.platform.runtime.paths import InstanceLock, InstancePaths
from ...application.ports import Record
from ...domain.contracts import BusinessError
from . import CommandPlugin


def vectors_arguments(vector: argparse.ArgumentParser) -> None:
    vector_commands = vector.add_subparsers(dest="vector_operation", required=True)
    vector_inspect = vector_commands.add_parser("inspect")
    vector_inspect.add_argument("bundle", type=Path)
    vector_configure = vector_commands.add_parser("configure")
    vector_configure.add_argument("bundle", type=Path)
    vector_configure.add_argument("--sha256", required=True, help="SHA256 of canonical reviewed manifest JSON")
    vector_commands.add_parser("disable")


def indexes_arguments(indexes: argparse.ArgumentParser) -> None:
    indexes.add_argument("operation", choices=["check", "rebuild"])


def backup_arguments(snapshot: argparse.ArgumentParser) -> None:
    snapshot.add_argument("destination", type=Path)


def restore_arguments(recover: argparse.ArgumentParser) -> None:
    recover.add_argument("source", type=Path)
    recover.add_argument("destination", type=Path)


def objects_arguments(objects: argparse.ArgumentParser) -> None:
    operations = objects.add_subparsers(dest="operation", required=True)
    operations.add_parser("check", help="Verify registered objects and pending collection")
    plan = operations.add_parser("gc-plan", help="Write a reviewable collection plan outside the instance")
    plan.add_argument("output", type=Path)
    apply = operations.add_parser("gc-apply", help="Apply or resume the exact reviewed plan")
    apply.add_argument("plan", type=Path)


def run_vectors(args: argparse.Namespace, paths: InstancePaths) -> int:
    from ...adapters.vector_files import configure, identity, inspect_bundle

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
    return 0


def run_indexes(args: argparse.Namespace, paths: InstancePaths) -> int:
    print(json.dumps(derived_indexes(paths, rebuild=args.operation == "rebuild")))
    return 0


def run_backup(args: argparse.Namespace, paths: InstancePaths) -> int:
    print(json.dumps(backup(paths, args.destination)))
    return 0


def run_restore(args: argparse.Namespace, _paths: InstancePaths) -> int:
    print(json.dumps(restore(args.source, args.destination)))
    return 0


def run_objects(args: argparse.Namespace, paths: InstancePaths) -> int:
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
    return 0


VECTORS = CommandPlugin(
    "vectors",
    run_vectors,
    help="Select or disable a reviewed local ONNX bundle for the next startup",
    configure=vectors_arguments,
)
INDEXES = CommandPlugin(
    "indexes", run_indexes, help="Exclusive offline full-text index maintenance", configure=indexes_arguments
)
BACKUP = CommandPlugin("backup", run_backup, configure=backup_arguments)
RESTORE = CommandPlugin("restore", run_restore, configure=restore_arguments)
OBJECTS = CommandPlugin(
    "objects", run_objects, help="Exclusive offline object maintenance", configure=objects_arguments
)
