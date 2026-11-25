"""Bind diagnostics, debug maintenance and runtime health to an owned service."""

from __future__ import annotations

from typing import cast

from ...adapters.diagnostic_bundle import DiagnosticFiles
from ...application.debugging import DebugService
from ...application.operations import OperationsService
from ...application.ports import Record
from . import FeatureBinding, KernelFeatureContext


async def install_operations(context: KernelFeatureContext) -> FeatureBinding:
    base = context.base
    diagnostic = DiagnosticFiles(base.paths, context.sink)
    base.lifetime.own("operations", 0, diagnostic.close)
    debugging = DebugService(base.database, diagnostic, base.clock, base.ids)
    service = OperationsService(
        base.database,
        context.metrics,
        diagnostic,
        lambda: operational_health(context.runtime_health()),
        debugging,
    )
    base.lifetime.own("operations", 0, service.close, service.stop_accepting)
    debugging.start()
    return FeatureBinding(service)


def operational_health(health: Record) -> Record:
    deliveries = cast(Record, health["deliveries"])
    maintenance = cast(Record, health["maintenance"])
    memory_organization = cast(Record, health["memory_organization"])
    extensions = cast(Record, health["extensions"])
    hosts = cast(Record, extensions["hosts"])
    terminals = cast(Record, health["terminals"])
    media = cast(Record, health["media"])
    mcp_transports = cast(Record, cast(Record, health["mcp"])["transports"])
    sessions = mcp_transports["sessions"]
    desktop = cast(Record, health["desktop"])
    desktop_backend = cast(Record, desktop["backend"])
    portal = cast(Record, health["desktop_portal"])
    portal_backend = cast(Record, portal["backend"])
    control = cast(Record, health["desktop_control"])
    control_backend_health = cast(Record, control["backend"])
    terminal_reviews = cast(Record, health["terminal_reviews"])
    schedules = cast(Record, health["schedules"])
    documents = cast(Record, health["documents"])
    vector = cast(Record, documents["vector"])
    residual = health["shutdown_residual"]
    return {
        "ready": health["ready"],
        "closing": health["closing"],
        "active_runs": health["active_runs"],
        "active_deliveries": deliveries["active"],
        "active_maintenance": maintenance["active"],
        "memory_organization_pending": memory_organization["pending"],
        "extension_hosts": hosts["resident"],
        "extension_cleanup_pending": hosts["cleanup_pending"],
        "active_terminals": terminals["active"],
        "media_jobs_pending": media["pending"],
        "mcp_sessions": mcp_transports["resident"],
        "mcp_cleanup_pending": sum(
            1
            for session in (sessions if isinstance(sessions, list) else [])
            if isinstance(session, dict) and cast(dict[str, object], session).get("cleanup_pending")
        ),
        "desktop_observations": desktop["pending"],
        "desktop_portal_pending": portal["pending"],
        "desktop_portal_resident": portal_backend["resident"],
        "desktop_portal_cleanup_pending": portal_backend["cleanup_pending"],
        "desktop_control_pending": control["pending"],
        "desktop_control_resident": control_backend_health["resident"],
        "desktop_control_cleanup_pending": control_backend_health["cleanup_pending"],
        "desktop_cleanup_pending": desktop_backend.get("cleanup_pending", 0),
        "desktop_capture_staging": desktop_backend.get("capture_staging"),
        "terminal_cleanup_pending": terminals["cleanup_pending"],
        "terminal_state_pending": terminals["state_pending"],
        "terminal_reviews_pending": terminal_reviews["pending"],
        "database": health["database"],
        "scheduler_ready": schedules["ready"],
        "delivery_ready": deliveries["ready"],
        "document_queries": documents["pending"],
        "vector_available": vector["available"],
        "installed_delivery_transports": deliveries["installed_transports"],
        "stalled_deliveries": deliveries["stalled"],
        "credentials": health["credentials"],
        "shutdown_residual_count": len(residual) if isinstance(residual, list) else 0,
    }
