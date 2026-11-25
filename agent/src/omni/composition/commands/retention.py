"""Read, plan, apply and cancel offline retention work."""

from __future__ import annotations

import argparse
import json
from collections.abc import Callable
from pathlib import Path

from ...adapters.persistence.audit_pruning import (
    apply_audit_retention,
    cancel_audit_retention,
    plan_audit_retention,
    read_audit_plan,
)
from ...adapters.persistence.event_pruning import (
    apply_event_retention,
    cancel_event_retention,
    plan_event_retention,
    read_event_plan,
)
from ...adapters.persistence.execution_pruning import apply_pruning, cancel_pruning, plan_pruning, read_pruning_plan
from ...adapters.persistence.operations_read import offline_read
from ...adapters.persistence.reference_release import (
    apply_reference_release,
    cancel_reference_release,
    plan_reference_release,
    read_reference_plan,
)
from ...adapters.platform.runtime.paths import InstanceLock, InstancePaths
from ...adapters.primitives import SystemClock, UUIDs
from ...application.ports import Record, UnitOfWork
from . import CommandPlugin


def page_arguments(parser: argparse.ArgumentParser) -> None:
    parser.add_argument("--after", default="")
    parser.add_argument("--limit", type=int, default=20)


def agent_page_arguments(parser: argparse.ArgumentParser) -> None:
    parser.add_argument("--agent", required=True)
    parser.add_argument("--cursor", default="")
    parser.add_argument("--limit", type=int, default=20)
    parser.add_argument("--as-of", default="")


def plan_arguments(parser: argparse.ArgumentParser) -> None:
    parser.add_argument("output", type=Path)
    page_arguments(parser)


def agent_plan_arguments(parser: argparse.ArgumentParser) -> None:
    parser.add_argument("output", type=Path)
    agent_page_arguments(parser)


def apply_arguments(parser: argparse.ArgumentParser) -> None:
    parser.add_argument("plan", type=Path)
    parser.add_argument("--backup", type=Path, required=True)


def cancel_arguments(parser: argparse.ArgumentParser) -> None:
    parser.add_argument("plan", type=Path)
    parser.add_argument("--reason", required=True)


def _read_preview(paths: InstancePaths, read: Callable[[UnitOfWork], Record]) -> None:
    lock = InstanceLock(paths)
    lock.acquire()
    try:
        report = offline_read(paths.database, SystemClock(), UUIDs(), read)
        print(json.dumps(report))
    finally:
        lock.close()


def run_retention_preview(args: argparse.Namespace, paths: InstancePaths) -> int:
    _read_preview(paths, lambda u: u.retention_preview(args.after, args.limit))
    return 0


def run_retention_audit_preview(args: argparse.Namespace, paths: InstancePaths) -> int:
    _read_preview(
        paths, lambda u: u.audit_retention_preview(args.agent, cursor=args.cursor, limit=args.limit, as_of=args.as_of)
    )
    return 0


def run_retention_audit_plan(args: argparse.Namespace, paths: InstancePaths) -> int:
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
    return 0


def run_retention_audit_apply(args: argparse.Namespace, paths: InstancePaths) -> int:
    print(json.dumps(apply_audit_retention(paths, read_audit_plan(args.plan), args.backup)))
    return 0


def run_retention_audit_cancel(args: argparse.Namespace, paths: InstancePaths) -> int:
    print(json.dumps(cancel_audit_retention(paths, read_audit_plan(args.plan, allow_future=True), args.reason)))
    return 0


def run_retention_events_preview(args: argparse.Namespace, paths: InstancePaths) -> int:
    _read_preview(
        paths, lambda u: u.event_retention_preview(args.agent, cursor=args.cursor, limit=args.limit, as_of=args.as_of)
    )
    return 0


def run_retention_events_plan(args: argparse.Namespace, paths: InstancePaths) -> int:
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
    return 0


def run_retention_events_apply(args: argparse.Namespace, paths: InstancePaths) -> int:
    print(json.dumps(apply_event_retention(paths, read_event_plan(args.plan), args.backup)))
    return 0


def run_retention_events_cancel(args: argparse.Namespace, paths: InstancePaths) -> int:
    print(json.dumps(cancel_event_retention(paths, read_event_plan(args.plan, allow_future=True), args.reason)))
    return 0


def run_retention_plan(args: argparse.Namespace, paths: InstancePaths) -> int:
    print(json.dumps(plan_pruning(paths, args.output, after=args.after, limit=args.limit)))
    return 0


def run_retention_apply(args: argparse.Namespace, paths: InstancePaths) -> int:
    print(json.dumps(apply_pruning(paths, read_pruning_plan(args.plan), args.backup)))
    return 0


def run_retention_cancel(args: argparse.Namespace, paths: InstancePaths) -> int:
    print(json.dumps(cancel_pruning(paths, read_pruning_plan(args.plan, allow_future=True), args.reason)))
    return 0


def run_retention_refs_preview(args: argparse.Namespace, paths: InstancePaths) -> int:
    _read_preview(paths, lambda u: u.reference_preview(args.after, args.limit))
    return 0


def run_retention_refs_plan(args: argparse.Namespace, paths: InstancePaths) -> int:
    print(json.dumps(plan_reference_release(paths, args.output, after=args.after, limit=args.limit)))
    return 0


def run_retention_refs_apply(args: argparse.Namespace, paths: InstancePaths) -> int:
    print(json.dumps(apply_reference_release(paths, read_reference_plan(args.plan), args.backup)))
    return 0


def run_retention_refs_cancel(args: argparse.Namespace, paths: InstancePaths) -> int:
    print(json.dumps(cancel_reference_release(paths, read_reference_plan(args.plan, allow_future=True), args.reason)))
    return 0


RETENTION_COMMANDS = (
    CommandPlugin(
        "retention-preview",
        run_retention_preview,
        help="Exclusive read-only 30-day execution-body preview; does not delete",
        configure=page_arguments,
    ),
    CommandPlugin(
        "retention-audit-preview",
        run_retention_audit_preview,
        help="Exclusive read-only 180-day Agent audit preview; does not delete",
        configure=agent_page_arguments,
    ),
    CommandPlugin(
        "retention-audit-plan",
        run_retention_audit_plan,
        help="Write a reviewable 180-day Agent audit-pruning plan outside the instance",
        configure=agent_plan_arguments,
    ),
    CommandPlugin(
        "retention-audit-apply",
        run_retention_audit_apply,
        help="Apply or resume audit deletion with its verified original snapshot",
        configure=apply_arguments,
    ),
    CommandPlugin(
        "retention-audit-cancel",
        run_retention_audit_cancel,
        help="Cancel pending audit deletions; committed deletions remain recorded",
        configure=cancel_arguments,
    ),
    CommandPlugin(
        "retention-events-preview",
        run_retention_events_preview,
        help="Exclusive read-only 180-day Agent event preview; does not delete",
        configure=agent_page_arguments,
    ),
    CommandPlugin(
        "retention-events-plan",
        run_retention_events_plan,
        help="Write a reviewable 180-day Agent event-pruning plan outside the instance",
        configure=agent_plan_arguments,
    ),
    CommandPlugin(
        "retention-events-apply",
        run_retention_events_apply,
        help="Apply or resume event deletion with its verified original snapshot",
        configure=apply_arguments,
    ),
    CommandPlugin(
        "retention-events-cancel",
        run_retention_events_cancel,
        help="Cancel pending event deletions; committed deletions remain recorded",
        configure=cancel_arguments,
    ),
    CommandPlugin(
        "retention-plan",
        run_retention_plan,
        help="Write a reviewable 30-day body-pruning plan outside the instance",
        configure=plan_arguments,
    ),
    CommandPlugin(
        "retention-apply",
        run_retention_apply,
        help="Apply or resume a plan with its verified pre-prune snapshot",
        configure=apply_arguments,
    ),
    CommandPlugin(
        "retention-cancel",
        run_retention_cancel,
        help="Cancel pending prune targets; committed pruning remains recorded",
        configure=cancel_arguments,
    ),
    CommandPlugin(
        "retention-refs-preview",
        run_retention_refs_preview,
        help="Preview retired action-object references; no deletion",
        configure=page_arguments,
    ),
    CommandPlugin(
        "retention-refs-plan",
        run_retention_refs_plan,
        help="Review reference release separately from object GC",
        configure=plan_arguments,
    ),
    CommandPlugin(
        "retention-refs-apply",
        run_retention_refs_apply,
        help="Release reviewed references after verified backup; no files deleted",
        configure=apply_arguments,
    ),
    CommandPlugin(
        "retention-refs-cancel",
        run_retention_refs_cancel,
        help="Cancel pending reference releases without rebuilding committed references",
        configure=cancel_arguments,
    ),
)
