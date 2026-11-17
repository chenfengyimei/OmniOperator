"""Capture a hash-pinned local wheel bundle for an isolated environment; never downloads or executes wheels."""

from __future__ import annotations

import hashlib
import json
import os
import platform
import re
import shutil
import stat
import sys
import tempfile
import uuid
from pathlib import Path
from typing import BinaryIO, cast

from ...domain.contracts import BusinessError
from .permissions import private_path

FORMAT = "omni-dependency-bundle-1"
MAX_MANIFEST = 262144
MAX_WHEELS = 512
MAX_WHEEL_BYTES = 512 * 1024 * 1024
MAX_TOTAL_BYTES = 2 * 1024 * 1024 * 1024
SHA = re.compile(r"[a-f0-9]{64}")


def invalid() -> BusinessError:
    return BusinessError(
        "dependency_bundle_invalid", "Use a local, platform-matched wheel bundle and its SHA256", status=422
    )


def no_links(path: Path) -> None:
    if any(part.is_symlink() or part.is_junction() for part in (path, *path.parents)):
        raise invalid()


def _open(path: Path) -> BinaryIO:
    flags = os.O_RDONLY | getattr(os, "O_BINARY", 0) | getattr(os, "O_NOFOLLOW", 0) | getattr(os, "O_NONBLOCK", 0)
    return os.fdopen(os.open(path, flags), "rb")


def _pairs(items: list[tuple[str, object]]) -> dict[str, object]:
    result: dict[str, object] = {}
    for key, value in items:
        if key in result:
            raise invalid()
        result[key] = value
    return result


def _manifest(root: Path, expected: str) -> tuple[bytes, tuple[tuple[str, int, str], ...]]:
    no_links(root)
    path = root / "manifest.json"
    no_links(path)
    if SHA.fullmatch(expected) is None or not root.is_dir():
        raise invalid()
    with _open(path) as stream:
        info = os.fstat(stream.fileno())
        if not stat.S_ISREG(info.st_mode) or not 1 <= info.st_size <= MAX_MANIFEST:
            raise invalid()
        raw = stream.read(MAX_MANIFEST + 1)
    if len(raw) > MAX_MANIFEST or hashlib.sha256(raw).hexdigest() != expected:
        raise invalid()
    try:
        value = json.loads(raw, object_pairs_hook=_pairs)
    except (ValueError, UnicodeError, RecursionError) as exc:
        raise invalid() from exc
    if not isinstance(value, dict):
        raise invalid()
    data = cast(dict[str, object], value)
    if (
        set(data) != {"format", "platform", "python", "machine", "wheels"}
        or data["format"] != FORMAT
        or data["platform"] != sys.platform
        or data["python"] != f"{sys.version_info.major}.{sys.version_info.minor}"
        or data["machine"] != platform.machine().casefold()
        or not isinstance(data["wheels"], list)
    ):
        raise invalid()
    rows = cast(list[object], data["wheels"])
    if len(rows) > MAX_WHEELS:
        raise invalid()
    result: list[tuple[str, int, str]] = []
    seen: set[str] = set()
    total = 0
    for value in rows:
        if not isinstance(value, dict):
            raise invalid()
        row = cast(dict[str, object], value)
        name, size, digest = row.get("file"), row.get("bytes"), row.get("sha256")
        if (
            set(row) != {"file", "bytes", "sha256"}
            or not isinstance(name, str)
            or re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9_.+-]{0,239}\.whl", name) is None
            or name.casefold() in seen
            or type(size) is not int
            or not 1 <= size <= MAX_WHEEL_BYTES
            or not isinstance(digest, str)
            or SHA.fullmatch(digest) is None
        ):
            raise invalid()
        total += size
        if total > MAX_TOTAL_BYTES:
            raise invalid()
        seen.add(name.casefold())
        result.append((name, size, digest))
    return raw, tuple(sorted(result))


def _copy_wheel(source: Path, expected_bytes: int, expected_sha256: str, target: Path | None = None) -> None:
    no_links(source)
    digest, length = hashlib.sha256(), 0
    with _open(source) as stream:
        info = os.fstat(stream.fileno())
        if not stat.S_ISREG(info.st_mode) or info.st_size != expected_bytes:
            raise invalid()
        output = target.open("xb") if target is not None else None
        try:
            while block := stream.read(min(1024 * 1024, expected_bytes + 1 - length)):
                length += len(block)
                if length > expected_bytes:
                    raise invalid()
                digest.update(block)
                if output is not None:
                    output.write(block)
        finally:
            if output is not None:
                output.close()
    if length != expected_bytes or digest.hexdigest() != expected_sha256:
        raise invalid()


def capture_bundle(source: Path, expected_sha256: str, stage: Path) -> Path:
    """Publish only checked wheel bytes and generated local hash requirements inside the candidate stage."""
    try:
        return _capture_bundle(source, expected_sha256, stage)
    except (OSError, ValueError) as exc:
        raise invalid() from exc


def _capture_bundle(source: Path, expected_sha256: str, stage: Path) -> Path:
    no_links(stage)
    destination = stage / "dependencies"
    no_links(destination)
    if destination.exists():
        _, rows = _manifest(destination, expected_sha256)
        for name, size, digest in rows:
            _copy_wheel(destination / name, size, digest)
        requirements = destination / "requirements.txt"
        no_links(requirements)
        expected = "".join(
            f"{(destination / name).resolve().as_uri()} --hash=sha256:{digest}\n" for name, _, digest in rows
        )
        with _open(requirements) as stream:
            if stream.read(len(expected.encode()) + 1) != expected.encode():
                raise invalid()
        return requirements
    raw, rows = _manifest(source, expected_sha256)
    partial = stage / (".dependencies-" + uuid.uuid4().hex)
    partial.mkdir(mode=0o700)
    try:
        for name, size, digest in rows:
            _copy_wheel(source / name, size, digest, partial / name)
        (partial / "manifest.json").write_bytes(raw)
        requirements = "".join(
            f"{(destination / name).resolve().as_uri()} --hash=sha256:{digest}\n" for name, _, digest in rows
        )
        (partial / "requirements.txt").write_text(requirements, encoding="utf-8", newline="\n")
        private_path(partial, directory=True)
        partial.rename(destination)
    finally:
        if partial.exists():
            no_links(partial)
            if partial.parent.resolve() != stage.resolve():
                raise invalid()
            shutil.rmtree(partial)
    return destination / "requirements.txt"


def build_bundle(source: Path, destination: Path) -> dict[str, object]:
    """Build a local distribution bundle from wheel files without downloading or installing them."""
    no_links(source)
    no_links(destination)
    source, destination = source.absolute(), destination.absolute()
    if (
        not source.is_dir()
        or destination.exists()
        or source == destination
        or destination.is_relative_to(source)
        or source.is_relative_to(destination)
    ):
        raise invalid()
    wheels: list[dict[str, object]] = []
    total = 0
    with os.scandir(source) as entries:
        for entry in entries:
            if len(wheels) >= MAX_WHEELS or not entry.name.endswith(".whl"):
                raise invalid()
            path = Path(entry.path)
            no_links(path)
            digest, length = hashlib.sha256(), 0
            with _open(path) as stream:
                info = os.fstat(stream.fileno())
                if not stat.S_ISREG(info.st_mode) or not 1 <= info.st_size <= MAX_WHEEL_BYTES:
                    raise invalid()
                while block := stream.read(min(1024 * 1024, info.st_size + 1 - length)):
                    length += len(block)
                    if length > info.st_size:
                        raise invalid()
                    digest.update(block)
            if length != info.st_size:
                raise invalid()
            total += length
            if total > MAX_TOTAL_BYTES:
                raise invalid()
            wheels.append({"file": entry.name, "bytes": length, "sha256": digest.hexdigest()})
    data = {
        "format": FORMAT,
        "platform": sys.platform,
        "python": f"{sys.version_info.major}.{sys.version_info.minor}",
        "machine": platform.machine().casefold(),
        "wheels": sorted(wheels, key=lambda row: str(row["file"])),
    }
    raw = json.dumps(data, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode()
    manifest_sha256 = hashlib.sha256(raw).hexdigest()
    destination.parent.mkdir(parents=True, exist_ok=True)
    no_links(destination.parent)
    temporary = Path(tempfile.mkdtemp(prefix=".deps-", dir=destination.parent))
    try:
        (temporary / "manifest.json").write_bytes(raw)
        _, checked = _manifest(temporary, manifest_sha256)
        for name, size, digest_value in checked:
            _copy_wheel(source / name, size, digest_value, temporary / name)
        private_path(temporary, directory=True)
        temporary.rename(destination)
    finally:
        if temporary.exists():
            no_links(temporary)
            if temporary.parent.resolve() != destination.parent.resolve() or not temporary.name.startswith(".deps-"):
                raise invalid()
            shutil.rmtree(temporary)
    return {
        "format": FORMAT,
        "bundle": str(destination),
        "manifest_sha256": manifest_sha256,
        "wheels": len(wheels),
        "bytes": total,
        "dependency_closure_verified": False,
    }
