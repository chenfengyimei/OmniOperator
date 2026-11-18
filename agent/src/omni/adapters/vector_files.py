"""Verify explicit local vector files and startup selection; never imports a model or downloads data."""

from __future__ import annotations

import hashlib
import json
import os
import platform
import stat
import sys
import uuid
from pathlib import Path
from typing import cast

from ..domain.local_vectors import RUNTIME, canonical, fingerprint, invalid, manifest
from .object_store import sync_directory
from omni.adapters.platform.runtime.permissions import private_path


def runtime_versions() -> dict[str, str]:
    # Current ORT wheels no longer cover Intel Macs; keep that ABI explicit in index identities.
    return {
        **RUNTIME,
        "onnxruntime": "1.23.2" if sys.platform == "darwin" and platform.machine() == "x86_64" else "1.31.1",
    }


def identity(path: Path) -> tuple[int, ...]:
    for part in (path, *path.parents):
        if part.is_symlink() or part.is_junction():
            raise invalid("vector_bundle_path_unsafe")
    info = path.stat()
    if not stat.S_ISREG(info.st_mode) or info.st_nlink != 1:
        raise invalid("vector_bundle_path_unsafe")
    return info.st_dev, info.st_ino, info.st_size, info.st_mtime_ns, info.st_ctime_ns


def read_file(path: Path, maximum: int, digest: str | None = None) -> tuple[bytes, tuple[int, ...]]:
    before = identity(path)
    if before[2] > maximum:
        raise invalid()
    with path.open("rb") as stream:
        data = stream.read(maximum + 1)
    if (
        len(data) != before[2]
        or before != identity(path)
        or len(data) > maximum
        or digest is not None
        and hashlib.sha256(data).hexdigest() != digest
    ):
        raise invalid("vector_bundle_changed")
    return data, before


def read_manifest(root: Path, expected: str | None) -> tuple[dict[str, object], tuple[int, ...]]:
    if not root.is_absolute():
        raise invalid("vector_bundle_path_unsafe")
    raw, captured = read_file(root / "manifest.json", 65536)
    try:
        value = manifest(json.loads(raw))
    except (ValueError, UnicodeError, RecursionError):
        raise invalid() from None
    if expected is not None and fingerprint(value) != expected:
        raise invalid("vector_bundle_changed")
    return value, captured


def inspect_bundle(root: Path) -> dict[str, object]:
    value, _ = read_manifest(root, None)
    for name, row in cast(dict[str, dict[str, object]], value["files"]).items():
        data, _ = read_file(root / name, cast(int, row["bytes"]), cast(str, row["sha256"]))
        if len(data) != row["bytes"]:
            raise invalid("vector_bundle_changed")
    return {"manifest": value, "sha256": fingerprint(value), "files_verified": True, "native_model_verified": False}


def selection(home: Path) -> tuple[Path, str] | None:
    path = home / "config" / "local-vector.json"
    if not path.exists() and not path.is_symlink() and not path.is_junction():
        return None
    raw, _ = read_file(path, 16384)
    decoded = json.loads(raw)
    if not isinstance(decoded, dict):
        raise invalid()
    row = cast(dict[str, object], decoded)
    if (
        set(row) != {"schema", "bundle", "sha256"}
        or type(row["schema"]) is not int
        or row["schema"] != 1
        or not isinstance(row["bundle"], str)
        or not isinstance(row["sha256"], str)
    ):
        raise invalid()
    return Path(row["bundle"]), row["sha256"]


def configure(home: Path, bundle: Path, expected: str) -> dict[str, object]:
    # The CLI owns the exclusive instance lock before calling this function.
    value, _ = read_manifest(bundle, expected)
    for name, row in cast(dict[str, dict[str, object]], value["files"]).items():
        info = identity(bundle / name)
        if info[2] != row["bytes"]:
            raise invalid("vector_bundle_changed")
    path = home / "config" / "local-vector.json"
    path.parent.mkdir(parents=True, exist_ok=True)
    for parent in (path.parent, *path.parent.parents):
        if parent.is_symlink() or parent.is_junction():
            raise invalid("vector_bundle_path_unsafe")
    if path.exists():
        identity(path)
    temporary = path.with_name(f".local-vector-{uuid.uuid4()}.pending")
    try:
        with temporary.open("xb") as stream:
            private_path(temporary)
            stream.write(canonical({"schema": 1, "bundle": str(bundle), "sha256": expected}))
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(temporary, path)
        sync_directory(path.parent)
    finally:
        if temporary.exists():
            identity(temporary)
            temporary.unlink()
    return {
        "configured": True,
        "manifest_sha256": expected,
        "model_id": value["model_id"],
        "applies": "next_start",
        "native_model_verified": False,
    }
