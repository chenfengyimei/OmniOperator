"""Exclusive diagnostics and authenticated local diagnostic download."""

from __future__ import annotations

import argparse
import asyncio
import json
from pathlib import Path
from typing import cast
from urllib.parse import urlsplit

from ...adapters.diagnostic_bundle import DiagnosticFiles, StoredDiagnostics, publish_diagnostic
from ...adapters.persistence.maintenance import inspect_database, object_report
from ...adapters.persistence.operations_read import offline_status
from ...adapters.platform.runtime.paths import InstanceLock, InstancePaths
from ...adapters.primitives import SystemClock, UUIDs
from ...application.ports import Record
from ...domain.contracts import BusinessError
from . import CommandPlugin


def local_origin(value: str) -> str:
    try:
        target = urlsplit(value)
        if (
            target.scheme != "http"
            or target.hostname not in {"127.0.0.1", "localhost", "::1"}
            or target.username is not None
            or target.password is not None
            or target.query
            or target.fragment
            or target.path not in {"", "/"}
        ):
            raise ValueError
        target.port
    except ValueError as exc:
        raise BusinessError("invalid_local_endpoint", "CLI requests require a local API origin") from exc
    return value.rstrip("/")


def diagnostic_download(paths: InstancePaths, url: str) -> bytes:
    import httpx

    origin = local_origin(url)
    token_path = paths.control / "owner.token"
    if token_path.is_symlink() or token_path.is_junction():
        raise BusinessError("unsafe_owner_token", "Owner token cannot be a link")
    token = token_path.read_text(encoding="utf-8").strip()
    try:
        with (
            httpx.Client(
                trust_env=False, follow_redirects=False, timeout=10, headers={"Accept-Encoding": "identity"}
            ) as client,
            client.stream(
                "GET", origin + "/api/v1/system/diagnostics", headers={"Authorization": "Bearer " + token}
            ) as response,
        ):
            response.raise_for_status()
            if response.headers.get("content-encoding", "identity").lower() != "identity":
                raise BusinessError(
                    "invalid_diagnostic_response", "Diagnostic downloads cannot use HTTP content encoding"
                )
            if response.headers.get("content-type") != "application/zip":
                raise BusinessError(
                    "invalid_diagnostic_response", "The local service did not return a diagnostic package"
                )
            chunks: list[bytes] = []
            size = 0
            for chunk in response.iter_raw(chunk_size=65536):
                size += len(chunk)
                if size > 2 * 1024 * 1024:
                    raise BusinessError("diagnostics_too_large", "Diagnostic download exceeds 2 MiB")
                chunks.append(chunk)
            return b"".join(chunks)
    except httpx.HTTPError as exc:
        raise BusinessError(
            "local_api_unavailable",
            "The local diagnostic service is unavailable or did not authorize the request",
            status=503,
        ) from exc


def diagnostics_arguments(diagnostics: argparse.ArgumentParser) -> None:
    diagnostics.add_argument("destination", type=Path)
    diagnostics.add_argument(
        "--offline",
        action="store_true",
        help="Exclusive read-only export without starting the runtime or upgrading schema",
    )
    diagnostics.add_argument("--url", default="http://127.0.0.1:8766")


def run_doctor(_args: argparse.Namespace, paths: InstancePaths) -> int:
    lock = InstanceLock(paths)
    lock.acquire()
    try:
        report = cast(Record, {**inspect_database(paths.database), "objects": object_report(paths)})
        objects = cast(Record, report["objects"])
        print(json.dumps(report))
        if (
            report["integrity"] != "ok"
            or report["foreign_key_errors"]
            or report["document_erasure_attention"]
            or report["document_index_building"]
            or report["execution_retention_attention"]
            or report["reference_release_attention"]
            or report["audit_retention_attention"]
            or report["event_retention_attention"]
            or report["schedule_attention"]
            or report["unknown_model_calls"]
            or report["unknown_actions"]
            or report["delivery_attention"]
            or report["held_dispatch_resources"]
            or not objects["ok"]
            or objects.get("pending_collection")
            or objects.get("collection_scan_limit_reached")
        ):
            return 1
    finally:
        lock.close()
    return 0


def run_diagnostics(args: argparse.Namespace, paths: InstancePaths) -> int:
    if args.offline:
        lock = InstanceLock(paths)
        lock.acquire()
        try:
            persisted = offline_status(paths.database, SystemClock(), UUIDs())

            async def export_offline():
                files = DiagnosticFiles(paths, StoredDiagnostics(paths))
                try:
                    information: Record = {
                        "schema": 1,
                        "persisted": persisted,
                        "measurements": {"available": False, "reason": "offline_process"},
                        "host": await files.describe(),
                        "runtime": {"ready": False, "state": "offline"},
                    }
                    return await files.bundle(information)
                finally:
                    await files.close()

            content = asyncio.run(export_offline())
        finally:
            lock.close()
    else:
        content = diagnostic_download(paths, args.url)
    print(json.dumps(publish_diagnostic(paths, args.destination, content)))
    return 0


DOCTOR = CommandPlugin("doctor", run_doctor, help="Read-only, exclusive database diagnostics")
DIAGNOSTICS = CommandPlugin(
    "diagnostics",
    run_diagnostics,
    help="Export bounded metadata without chat, credentials or model/device calls",
    configure=diagnostics_arguments,
)
