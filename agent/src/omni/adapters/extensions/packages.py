"""Bounded zero-execution package capture and immutable verified snapshots; never opens a database."""

from __future__ import annotations

import asyncio
import contextvars
import ctypes
import hashlib
import io
import json
import os
import re
import shutil
import stat
import sys
import threading
import unicodedata
import uuid
import zipfile
from collections.abc import Callable, Mapping
from concurrent.futures import Future, ThreadPoolExecutor
from pathlib import Path
from typing import TypeVar

from ...application.ports import Record
from ...domain.contracts import BusinessError
from ...domain.extensions import (
    MAX_FILE_BYTES,
    MAX_FILES,
    MAX_MANIFEST_BYTES,
    MAX_PACKAGE_BYTES,
    PROTOCOL,
    CapturedPackage,
    catalog_fingerprint,
    parse_manifest,
)
from ..object_store import sync_directory

T = TypeVar("T")
PRIVATE = frozenset(
    {
        ".git",
        ".hg",
        ".svn",
        ".venv",
        "__pycache__",
        ".ssh",
        ".aws",
        ".azure",
        "private",
        "vault",
        "owner.token",
        "omni.db",
        "memory.db",
        "state.db",
        "cookies.json",
        "bili_cookies.json",
        "credentials.json",
        "credentials.yaml",
        "credentials.yml",
        "sitecustomize.py",
        "usercustomize.py",
    }
)
MAX_ARCHIVE_BYTES = MAX_PACKAGE_BYTES + 65_536


def _invalid(code: str = "extension_invalid", *, status: int = 422) -> BusinessError:
    return BusinessError(code, "Extension package paths, metadata or fingerprints are invalid", status=status)


def relative_path(value: object) -> str:
    if not isinstance(value, str) or not value or len(value) > 240 or "\\" in value:
        raise _invalid()
    value = unicodedata.normalize("NFC", value)
    parts = value.split("/")
    for part in parts:
        folded = unicodedata.normalize("NFKC", part).casefold()
        if (
            part in {"", ".", ".."}
            or part.endswith((".", " "))
            or any(char in '<>:"|?*' or unicodedata.category(char).startswith("C") for char in part)
            or folded in PRIVATE
            or folded == ".env"
            or folded.startswith(".env.")
            or folded.endswith((".db", ".sqlite", ".sqlite3", ".db-wal", ".db-shm", ".pem", ".key", ".pfx", ".p12"))
            or re.fullmatch(r"(?:con|prn|aux|nul|com[1-9]|lpt[1-9])(?:\..*)?", folded)
        ):
            raise _invalid()
    if len(parts) > 12:
        raise _invalid()
    value.encode("utf-8")
    return value


def no_links(path: Path) -> None:
    for candidate in (path, *path.parents):
        if candidate.is_symlink() or candidate.is_junction():
            raise _invalid()


def _names(paths: list[str]) -> None:
    seen: dict[str, tuple[str, str]] = {}
    for name in paths:
        parts = name.split("/")
        for offset in range(1, len(parts) + 1):
            current = "/".join(parts[:offset])
            kind = "file" if offset == len(parts) else "directory"
            key = current.casefold()
            if key in seen and seen[key] != (current, kind):
                raise _invalid()
            seen[key] = current, kind


def _read_file(path: Path) -> bytes:
    no_links(path)
    flags = os.O_RDONLY | getattr(os, "O_NOFOLLOW", 0) | getattr(os, "O_BINARY", 0) | getattr(os, "O_NONBLOCK", 0)
    descriptor = os.open(path, flags)
    with os.fdopen(descriptor, "rb") as stream:
        before = os.fstat(stream.fileno())
        if not stat.S_ISREG(before.st_mode) or before.st_nlink != 1 or before.st_size > MAX_FILE_BYTES:
            raise _invalid()
        data = stream.read(MAX_FILE_BYTES + 1)
        after = os.fstat(stream.fileno())
    no_links(path)
    current = path.stat(follow_symlinks=False)
    identity = lambda item: (item.st_dev, item.st_ino, item.st_size, item.st_mtime_ns)
    if len(data) != before.st_size or identity(before) != identity(after) or identity(before) != identity(current):
        raise _invalid("extension_source_changed", status=409)
    return data


def _directory_files(root: Path) -> dict[str, bytes]:
    if not root.is_absolute():
        raise _invalid()
    no_links(root)
    if not root.is_dir():
        raise _invalid()
    files: dict[str, bytes] = {}
    pending = [root]
    directories: set[str] = set()
    total = entries = 0
    while pending:
        directory = pending.pop()
        no_links(directory)
        with os.scandir(directory) as children:
            for child in children:
                entries += 1
                if entries > MAX_FILES * 16:
                    raise _invalid()
                path = Path(child.path)
                name = relative_path(path.relative_to(root).as_posix())
                no_links(path)
                # Windows DirEntry caches may omit link counts; stat the path and
                # keep the descriptor-level identity/link checks in _read_file.
                info = path.stat(follow_symlinks=False)
                if stat.S_ISDIR(info.st_mode):
                    directories.add(name)
                    pending.append(path)
                elif stat.S_ISREG(info.st_mode) and info.st_nlink == 1:
                    if name in files or len(files) >= MAX_FILES:
                        raise _invalid()
                    payload = _read_file(path)
                    total += len(payload)
                    if total > MAX_PACKAGE_BYTES:
                        raise _invalid()
                    files[name] = payload
                else:
                    raise _invalid()
    _names(list(files))
    expected_directories = {str(parent) for name in files for parent in Path(name).parents if str(parent) != "."}
    if directories != {path.replace("\\", "/") for path in expected_directories}:
        raise _invalid()
    return files


def _pairs(items):
    result = {}
    for key, value in items:
        if key in result:
            raise _invalid()
        result[key] = value
    return result


def _manifest(data: bytes) -> dict:
    if len(data) > MAX_MANIFEST_BYTES:
        raise _invalid()
    try:
        value = json.loads(data, object_pairs_hook=_pairs, parse_constant=lambda _: (_ for _ in ()).throw(ValueError()))
        return parse_manifest(value)
    except (ValueError, UnicodeError, RecursionError) as exc:
        raise _invalid() from exc


def _archive(files: Mapping[str, bytes]) -> bytes:
    stream = io.BytesIO()
    with zipfile.ZipFile(stream, "w", compression=zipfile.ZIP_STORED, allowZip64=False) as archive:
        for name, data in sorted(files.items()):
            entry = zipfile.ZipInfo(name, date_time=(1980, 1, 1, 0, 0, 0))
            entry.create_system = 3
            entry.external_attr = (stat.S_IFREG | 0o600) << 16
            entry.compress_type = zipfile.ZIP_STORED
            archive.writestr(entry, data)
    return stream.getvalue()


def _captured(files: dict[str, bytes]) -> CapturedPackage:
    if "extension.json" not in files:
        raise _invalid()
    manifest = _manifest(files["extension.json"])
    if relative_path(manifest["entry"]) not in files:
        raise _invalid()
    proof = tuple(
        {"path": name, "bytes": len(data), "sha256": hashlib.sha256(data).hexdigest()}
        for name, data in sorted(files.items())
    )
    content = _archive(files)
    return CapturedPackage(manifest, proof, hashlib.sha256(content).hexdigest(), catalog_fingerprint(manifest), content)


def capture_directory(root: Path) -> CapturedPackage:
    """Used by both the parent and static worker handshake; never imports the entry module."""
    return _captured(_directory_files(root))


def verify_snapshot(location: Path, package_sha256: str, catalog_sha256: str) -> CapturedPackage:
    try:
        captured = capture_directory(location)
        if captured.package_sha256 != package_sha256 or captured.catalog_sha256 != catalog_sha256:
            raise _invalid()
        return captured
    except (BusinessError, OSError, ValueError, UnicodeError) as exc:
        raise _invalid("extension_snapshot_drift", status=409) from exc


def _check_revision(revision: Record) -> tuple[dict, tuple[dict, ...], str, str]:
    try:
        manifest = parse_manifest(revision["manifest"])
        files = revision["files"]
        if not isinstance(files, (list, tuple)) or not 1 <= len(files) <= MAX_FILES:
            raise _invalid()
        proof = []
        total = 0
        for row in files:
            if not isinstance(row, dict) or set(row) != {"path", "bytes", "sha256"}:
                raise _invalid()
            name = relative_path(row["path"])
            if name != row["path"] or type(row["bytes"]) is not int or not 0 <= row["bytes"] <= MAX_FILE_BYTES:
                raise _invalid()
            if not isinstance(row["sha256"], str) or re.fullmatch(r"[0-9a-f]{64}", row["sha256"]) is None:
                raise _invalid()
            total += row["bytes"]
            proof.append(dict(row))
        paths = [row["path"] for row in proof]
        if total > MAX_PACKAGE_BYTES or paths != sorted(set(paths)):
            raise _invalid()
        _names(paths)
        for name in ("package_sha256", "catalog_sha256"):
            digest = revision[name]
            if not isinstance(digest, str) or re.fullmatch(r"[0-9a-f]{64}", digest) is None:
                raise _invalid()
        if catalog_fingerprint(manifest) != revision["catalog_sha256"]:
            raise _invalid()
        expected = {
            "entry": manifest["entry"],
            "protocol": PROTOCOL,
            "version": manifest["version"],
            "file_count": len(proof),
            "total_bytes": total,
        }
        if any(key in revision and revision[key] != value for key, value in expected.items()):
            raise _invalid()
        return manifest, tuple(proof), str(revision["package_sha256"]), str(revision["catalog_sha256"])
    except (BusinessError, KeyError, TypeError, ValueError, UnicodeError) as exc:
        raise _invalid("extension_package_invalid", status=409) from exc


def _decode_package(payload: bytes, revision: Record) -> tuple[CapturedPackage, dict[str, bytes]]:
    manifest, proof, package_sha, catalog_sha = _check_revision(revision)
    try:
        if (
            not isinstance(payload, bytes)
            or len(payload) > MAX_ARCHIVE_BYTES
            or hashlib.sha256(payload).hexdigest() != package_sha
        ):
            raise _invalid()
        files: dict[str, bytes] = {}
        with zipfile.ZipFile(io.BytesIO(payload)) as archive:
            infos = archive.infolist()
            if not 1 <= len(infos) <= MAX_FILES:
                raise _invalid()
            total = 0
            for info in infos:
                name = relative_path(info.filename)
                if (
                    name != info.filename
                    or name in files
                    or info.is_dir()
                    or info.flag_bits & 1
                    or info.compress_type != zipfile.ZIP_STORED
                    or info.file_size > MAX_FILE_BYTES
                    or info.compress_size != info.file_size
                ):
                    raise _invalid()
                total += info.file_size
                if total > MAX_PACKAGE_BYTES:
                    raise _invalid()
                files[name] = archive.read(info)
        _names(list(files))
        captured = _captured(files)
        if (
            captured.manifest != manifest
            or captured.files != proof
            or captured.package_sha256 != package_sha
            or captured.catalog_sha256 != catalog_sha
            or captured.content != payload
        ):
            raise _invalid()
        return captured, files
    except (BusinessError, OSError, ValueError, KeyError, UnicodeError, zipfile.BadZipFile, RuntimeError) as exc:
        raise _invalid("extension_package_invalid", status=409) from exc


def _publish_directory(source: Path, destination: Path) -> None:
    """Atomic no-replace publication; unavailable primitives fail closed instead of replacing a cache."""
    if os.name == "nt":
        from ctypes import wintypes

        move = ctypes.WinDLL("kernel32", use_last_error=True).MoveFileExW
        move.argtypes, move.restype = [wintypes.LPCWSTR, wintypes.LPCWSTR, wintypes.DWORD], wintypes.BOOL
        if not move(str(source), str(destination), 0x8):
            raise OSError(ctypes.get_last_error(), "Extension snapshot publication failed")
    else:
        libc = ctypes.CDLL(None, use_errno=True)
        if sys.platform == "darwin":
            rename = libc.renamex_np
            rename.argtypes, rename.restype = [ctypes.c_char_p, ctypes.c_char_p, ctypes.c_uint], ctypes.c_int
            result = rename(os.fsencode(source), os.fsencode(destination), 0x4)  # RENAME_EXCL
        elif sys.platform.startswith("linux"):
            rename = libc.renameat2
            rename.argtypes, rename.restype = (
                [ctypes.c_int, ctypes.c_char_p, ctypes.c_int, ctypes.c_char_p, ctypes.c_uint],
                ctypes.c_int,
            )
            result = rename(-100, os.fsencode(source), -100, os.fsencode(destination), 1)  # RENAME_NOREPLACE
        else:
            raise OSError("No atomic no-replace directory publication on this platform")
        if result:
            raise OSError(ctypes.get_errno(), "Extension snapshot publication failed")
        sync_directory(destination.parent)


class ExtensionPackages:
    def __init__(self, cache_root: Path) -> None:
        self._root = cache_root.absolute()
        self._executor = ThreadPoolExecutor(max_workers=2, thread_name_prefix="omni-extension-io")
        self._jobs: set[Future] = set()
        self._lock = threading.RLock()
        self._snapshots = threading.RLock()
        self._closing = self._closed = False

    async def _execute(self, operation: Callable[[], T]) -> T:
        with self._lock:
            if self._closing:
                raise BusinessError("extension_packages_closing", "Extension storage is draining", status=503)
            if len(self._jobs) >= 8:
                raise BusinessError(
                    "extension_packages_busy", "Extension storage queue is full", status=503, retryable=True
                )
            actual = self._executor.submit(contextvars.copy_context().run, operation)
            self._jobs.add(actual)

        def complete(future):
            with self._lock:
                self._jobs.discard(future)

        actual.add_done_callback(complete)
        waiting = asyncio.wrap_future(actual)
        waiting.add_done_callback(lambda future: future.exception() if not future.cancelled() else None)
        return await asyncio.shield(waiting)

    async def capture(self, source: str) -> CapturedPackage:
        def perform():
            try:
                if not isinstance(source, str) or len(source) > 4000 or "\0" in source:
                    raise _invalid()
                return capture_directory(Path(source))
            except OSError as exc:
                raise BusinessError(
                    "extension_source_unavailable", "Extension source cannot be read", status=503
                ) from exc

        return await self._execute(perform)

    def _location(self, location: str, revision: Record) -> Path:
        if not isinstance(location, str):
            raise _invalid("extension_snapshot_drift", status=409)
        path = Path(location)
        _, _, package_sha, _ = _check_revision(revision)
        if not path.is_absolute() or path.parent.parent != self._root or path.name != package_sha:
            raise _invalid("extension_snapshot_drift", status=409)
        if str(uuid.UUID(path.parent.name)) != path.parent.name or (
            "agent_id" in revision and revision["agent_id"] != path.parent.name
        ):
            raise _invalid("extension_snapshot_drift", status=409)
        no_links(path)
        return path

    def _verify(self, location: str, revision: Record) -> None:
        try:
            path = self._location(location, revision)
            if not path.exists():
                raise BusinessError(
                    "extension_snapshot_missing", "Extension snapshot must be rebuilt from its saved object", status=503
                )
            manifest, proof, package_sha, catalog_sha = _check_revision(revision)
            captured = verify_snapshot(path, package_sha, catalog_sha)
            if captured.manifest != manifest or captured.files != proof:
                raise _invalid()
        except BusinessError as exc:
            if exc.code == "extension_snapshot_missing":
                raise
            raise _invalid("extension_snapshot_drift", status=409) from exc
        except (OSError, ValueError) as exc:
            raise _invalid("extension_snapshot_drift", status=409) from exc

    async def verify(self, location: str, revision: Record) -> None:
        def perform():
            with self._snapshots:
                self._verify(location, revision)

        await self._execute(perform)

    def _prepare(self, agent_id: str, revision: Record, payload: bytes) -> str:
        try:
            if str(uuid.UUID(agent_id)) != agent_id or ("agent_id" in revision and revision["agent_id"] != agent_id):
                raise _invalid()
            captured, files = _decode_package(payload, revision)
            target = self._root / agent_id / captured.package_sha256
            no_links(target)
            if target.exists():
                self._verify(str(target), revision)
                return str(target)
            for directory in (self._root, self._root / agent_id):
                no_links(directory)
                directory.mkdir(parents=True, exist_ok=True, mode=0o700)
                no_links(directory)
                sync_directory(directory.parent)
            stage = target.parent / f".{captured.package_sha256}.{uuid.uuid4()}.partial"
            stage.mkdir(mode=0o700)
            try:
                for name, content in files.items():
                    path = stage / name
                    path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
                    descriptor = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL | getattr(os, "O_BINARY", 0), 0o600)
                    with os.fdopen(descriptor, "wb") as stream:
                        stream.write(content)
                        stream.flush()
                        os.fsync(stream.fileno())
                for directory in sorted(
                    (path for path in stage.rglob("*") if path.is_dir()), key=lambda path: len(path.parts), reverse=True
                ):
                    sync_directory(directory)
                sync_directory(stage)
                try:
                    no_links(target)
                    _publish_directory(stage, target)
                except OSError:
                    if not target.exists():
                        raise
                self._verify(str(target), revision)
                return str(target)
            finally:
                if stage.exists():
                    no_links(stage)
                    if stage.parent != target.parent or not stage.resolve().is_relative_to(self._root.resolve()):
                        raise _invalid("extension_snapshot_drift", status=409)
                    shutil.rmtree(stage)
        except BusinessError:
            raise
        except (OSError, ValueError, AttributeError) as exc:
            raise BusinessError(
                "extension_snapshot_unavailable", "Extension snapshot could not be prepared", status=503
            ) from exc

    async def prepare(self, agent_id: str, revision: Record, payload: bytes) -> str:
        def perform():
            with self._snapshots:
                return self._prepare(agent_id, revision, payload)

        return await self._execute(perform)

    async def close(self, timeout_s: float = 30) -> bool:
        with self._lock:
            self._closing = True
            pending = [asyncio.wrap_future(future) for future in self._jobs]
            for future in pending:
                future.add_done_callback(lambda item: item.exception() if not item.cancelled() else None)
        if pending:
            _, unfinished = await asyncio.wait(pending, timeout=max(0, timeout_s))
            if unfinished:
                return False
        with self._lock:
            if self._jobs:
                return False
            if not self._closed:
                self._executor.shutdown(wait=False)
                self._closed = True
        return True
