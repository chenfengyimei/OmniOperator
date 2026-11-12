"""Publish a reviewable stdlib-only administrator artifact; never installs it or elevates privileges."""

from __future__ import annotations

import hashlib
import sys
import uuid
from pathlib import Path

from ...application.ports import Record
from ...domain.contracts import BusinessError
from ..object_store import sync_directory
from .deployment_files import json_bytes, no_links, path_of, read_file, write_file
from .permissions import private_path
from .publication import publish_directory

SOURCES = (
    "__init__.py",
    "domain/__init__.py",
    "domain/contracts.py",
    "domain/service_deployment.py",
    "domain/service_operations.py",
    "domain/service_control.py",
    "domain/releases.py",
    "domain/recovery_authority.py",
    "domain/schema_upgrade.py",
    "domain/service_activation.py",
    "domain/service_exchange.py",
    "domain/service_admin.py",
    "adapters/__init__.py",
    "adapters/platform/__init__.py",
    "adapters/platform/service_templates.py",
    "adapters/platform/systemd_state.py",
    "adapters/platform/service_admin_policy.py",
    "adapters/platform/service_admin_commands.py",
    "adapters/platform/service_admin.py",
    "adapters/platform/macos_service_state.py",
    "adapters/platform/darwin_admin_files.py",
    "adapters/platform/darwin_admin_commands.py",
    "adapters/platform/darwin_admin_probe.py",
    "adapters/platform/darwin_admin_peer.py",
    "adapters/platform/darwin_admin_policy.py",
    "adapters/platform/darwin_service_admin.py",
)
BOOT = "adapters/workers/service_admin_boot.py"
PROBE = "adapters/workers/darwin_admin_probe.py"


def materialize_admin_bundle(destination: str | Path) -> Record:
    target = path_of(destination)
    no_links(target)
    source = Path(__file__).resolve().parents[2]
    if (
        target.exists()
        or not target.parent.is_dir()
        or target == source
        or target.is_relative_to(source)
        or source.is_relative_to(target)
    ):
        raise BusinessError("service_admin_destination_unsafe", "Choose a new directory outside the program")
    for prefix in (
        Path(sys.prefix),
        Path("/etc/systemd"),
        Path("/run"),
        Path("/var/lib/omnioperator-service-admin"),
        Path("/private/var/run"),
        Path("/private/var/db/omnioperator-service-admin"),
        Path("/Library/LaunchDaemons"),
    ):
        if target == prefix or target.is_relative_to(prefix):
            raise BusinessError(
                "service_admin_destination_unsafe", "Build an unprivileged review artifact outside managed paths"
            )
    files: dict[str, tuple[str | None, bytes]] = {
        "lib/omni/" + name: (name, read_file(source / name, 1024 * 1024)) for name in SOURCES
    }
    files["service-admin.py"] = (BOOT, read_file(source / BOOT, 1024 * 1024))
    files["service-admin-probe.py"] = (PROBE, read_file(source / PROBE, 1024 * 1024))
    notes = {
        "format": "omni-service-admin-instructions-4",
        "installed": False,
        "requirements": [
            "A staged exchange approves source and target phases separately, each bound to its own immutable bundle SHA and protocol4. Source close releases the native lock before acknowledgement; no target intent while that proof is missing.",
            "An approved Linux or macOS system exchange authorizes only its exact remove_source/install_target/start_target operations. Child settle proves native cleanup; the parent owns the socket and root instance lock until final close.",
            "For a single-parent exchange, administrator hashes must match BOTH runtimes. For a separately approved phase, hashes must match that phase's own installation; the other runtime is metadata and never executed as root.",
            "Review the entire artifact and its manifest before a root administrator installs a root-owned immutable copy outside owner-writable directories.",
            "Use a trusted root-owned Python 3.12+ and a clean environment; invoke service-admin.py with -I -S. No owner Python/package, .pth, sitecustomize or plugin is loaded as root.",
            "Owner and broker source fingerprints must match the reviewed immutable deployment. Build from that installation.",
            "The broker reads only the explicit private owner operation JSON, never the business database or credentials.",
            "Linux and macOS system single operations and whole exchanges are implemented in source, pending native acceptance. Mac registration enables future activation without loading; bootout has a declared potential stop effect. No root stop/restart/force/implicit elevation.",
            "Darwin uses a separate root-only stdlib probe and public system-domain APIs, never the owner-writable GUI helper. Exact local dscl account, root ACL/local-volume paths and kernel Unix peer identity are required.",
            "Darwin commands receive an exec permit only after kqueue exit/fork observation. Any fork makes cleanup unverifiable; no unsupported NOTE_TRACK, bare historical descendant PID or false tree-cleanup claim. Keep the root broker/lock for operator review.",
            "A successful native manager submission is not readiness. The owner separately reserves and proves the reviewed boot; root reads no application DB, boot key or credential.",
            "The root operator separately reviews OPERATION_SHA and OWNER_UID. The owner client separately pins this bundle SHA and authenticates root through its native Unix kernel peer credentials.",
        ],
        "command_not_executed": [
            "/usr/bin/env",
            "-i",
            "PATH=/usr/bin:/bin",
            "LANG=C.UTF-8",
            "ROOT_OWNED_PYTHON_3_12",
            "-I",
            "-S",
            "ROOT_OWNED_IMMUTABLE_BUNDLE/service-admin.py",
            "--operation",
            "PRIVATE_OWNER_OPERATION_JSON",
            "--approve",
            "OPERATION_SHA256",
            "--owner-uid",
            "OWNER_UID",
        ],
    }
    files["README.json"] = (None, json_bytes(notes))
    entries = [
        {"path": name, "source": origin, "bytes": len(data), "sha256": hashlib.sha256(data).hexdigest()}
        for name, (origin, data) in sorted(files.items())
    ]
    manifest = {"format": "omni-service-admin-bundle-4", "files": entries}
    manifest["sha256"] = hashlib.sha256(json_bytes(manifest)).hexdigest()
    temporary = target.with_name("." + uuid.uuid4().hex + ".admin-bundle.partial")
    temporary.mkdir(mode=0o700)
    private_path(temporary, directory=True)
    # Interrupted artifacts remain explicitly partial. Nothing is installed or executed.
    for name, (_, data) in files.items():
        path = temporary / name
        path.parent.mkdir(parents=True, exist_ok=True)
        write_file(path, data)
    write_file(temporary / "manifest.json", json_bytes(manifest))
    for path in sorted(temporary.rglob("*"), reverse=True):
        if path.is_dir():
            private_path(path, directory=True)
            sync_directory(path)
    sync_directory(temporary)
    no_links(target)
    publish_directory(temporary, target)
    return {
        "bundle": str(target),
        "sha256": manifest["sha256"],
        "files": len(entries),
        "installed": False,
        "elevated": False,
        "protocol": "omni-service-admin-4",
    }
