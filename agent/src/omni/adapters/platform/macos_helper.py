"""Resolve the fixed, owner-installed Swift bundle; never build, install or request TCC permissions."""

from __future__ import annotations

import hashlib
import json
import os
import plistlib
import stat
from pathlib import Path

from ...domain.contracts import BusinessError
from ...domain.desktop import VERSION

BUNDLE_ID = "org.omnioperator.desktophelper"
BUNDLE_NAME = "OmniOperator Desktop Helper.app"
EXECUTABLE = "OmniDesktopHelper"
MAX_EXECUTABLE_BYTES = 64 * 1024 * 1024


def _invalid() -> BusinessError:
    return BusinessError("desktop_macos_helper_invalid", "Rebuild and reinstall the fixed macOS helper", status=503)


def bundle_path() -> Path:
    # Not configurable by environment, HTTP, a tool argument or the current directory.
    return Path.home() / "Applications" / BUNDLE_NAME


def source_fingerprints() -> dict[str, str]:
    root = Path(__file__).resolve().parents[1] / "workers/macos"
    result: dict[str, str] = {}
    for path in sorted(root.rglob("*")):
        if (
            not path.is_file()
            or path.suffix not in {".swift", ".c", ".h"}
            or {".build", ".swiftpm"}.intersection(path.parts)
        ):
            continue
        if path.is_symlink() or path.stat().st_size > 1024 * 1024 or len(result) >= 64:
            raise _invalid()
        result[path.relative_to(root).as_posix()] = hashlib.sha256(path.read_bytes()).hexdigest()
    if not result:
        raise _invalid()
    return result


def _checked(root: Path, relative: str, maximum: int, uid: int) -> bytes:
    path = root / relative
    for candidate in (path, *path.parents):
        info = candidate.lstat()
        if stat.S_ISLNK(info.st_mode) or info.st_uid != uid or info.st_mode & 0o022:
            raise _invalid()
        if candidate == root:
            break
    descriptor = os.open(path, os.O_RDONLY | getattr(os, "O_NOFOLLOW", 0))
    with os.fdopen(descriptor, "rb") as stream:
        before = os.fstat(stream.fileno())
        if not stat.S_ISREG(before.st_mode) or before.st_nlink != 1 or not 0 < before.st_size <= maximum:
            raise _invalid()
        content = stream.read(maximum + 1)
        after = os.fstat(stream.fileno())

        def identity(info: os.stat_result) -> tuple[int, int, int, int, int]:
            return (info.st_dev, info.st_ino, info.st_size, info.st_mtime_ns, info.st_ctime_ns)

        if identity(before) != identity(after) or len(content) != before.st_size:
            raise _invalid()
    return content


def resolve() -> Path | None:
    """Called on the existing bounded desktop file executor, not the event loop."""
    root = bundle_path()
    if not root.exists() and not root.is_symlink():
        return None
    try:
        uid_function = getattr(os, "getuid", None)
        if uid_function is None:
            raise _invalid()
        uid = int(uid_function())
        # Check the fixed installation parent too; reject linked bundle or parent.
        for parent in (root.parent, root.parent.parent):
            info = parent.lstat()
            if stat.S_ISLNK(info.st_mode) or info.st_uid != uid or info.st_mode & 0o022:
                raise _invalid()
        info = plistlib.loads(_checked(root, "Contents/Info.plist", 16384, uid))
        if (
            info.get("CFBundleIdentifier") != BUNDLE_ID
            or info.get("CFBundleExecutable") != EXECUTABLE
            or info.get("OmniDesktopProtocol") != VERSION
            or info.get("LSMinimumSystemVersion") != "14.0"
        ):
            raise _invalid()
        # Outside the signed bundle: embedding the executable's final signature hash
        # in its own sealed resources would create a signature/hash dependency cycle.
        manifest = json.loads(_checked(root.parent, BUNDLE_NAME + ".build.json", 65536, uid))
        content = _checked(root, "Contents/MacOS/" + EXECUTABLE, MAX_EXECUTABLE_BYTES, uid)
        if (
            manifest.get("format") != "omni-macos-helper-build-1"
            or manifest.get("protocol") != VERSION
            or manifest.get("bundle_id") != BUNDLE_ID
            or manifest.get("source_files") != source_fingerprints()
            or manifest.get("executable_sha256") != hashlib.sha256(content).hexdigest()
        ):
            raise _invalid()
        executable = root / "Contents/MacOS" / EXECUTABLE
        if not executable.stat().st_mode & stat.S_IXUSR:
            raise _invalid()
        return executable
    except BusinessError:
        raise
    except (OSError, ValueError, TypeError, AttributeError, plistlib.InvalidFileException):
        raise _invalid() from None
