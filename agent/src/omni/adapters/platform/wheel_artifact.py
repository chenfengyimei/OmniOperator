"""Read-only wheel artifact verification: hash, archive safety and V2 asset presence; never installs."""

from __future__ import annotations

import hashlib
import re
import zipfile
from pathlib import Path, PurePosixPath

from ...domain.contracts import BusinessError

FORMAT = "omni-wheel-artifact-1"
MAX_FILES = 20_000
MAX_BYTES = 512 * 1024 * 1024
SHA = re.compile(r"[0-9a-f]{64}")


def invalid(code: str = "artifact_invalid") -> BusinessError:
    return BusinessError(code, "Use the published wheel and its recorded SHA256", status=422)


def inspect_wheel(path: Path, sha256: str) -> dict[str, object]:
    if SHA.fullmatch(sha256) is None:
        raise invalid("artifact_checksum")
    if path.is_symlink() or not path.is_file():
        raise invalid("artifact_missing")
    digest = hashlib.sha256()
    size = 0
    with path.open("rb") as stream:
        for block in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(block)
            size += len(block)
    if size > MAX_BYTES or digest.hexdigest() != sha256:
        raise invalid("artifact_checksum")
    try:
        with zipfile.ZipFile(path) as archive:
            infos = archive.infolist()
            names = [info.filename for info in infos]
            if not infos or len(infos) > MAX_FILES or archive.testzip() is not None:
                raise invalid()
            total = sum(info.file_size for info in infos)
    except (zipfile.BadZipFile, OSError, RuntimeError):
        raise invalid() from None
    if len(set(names)) != len(names) or total > MAX_BYTES:
        raise invalid()
    for name in names:
        parts = PurePosixPath(name).parts
        if name.startswith("/") or "\\" in name or ".." in parts or not parts:
            raise invalid("artifact_unsafe_path")
    required = {"omni/web_assets/index.html", "omni/web_assets/ui/v2/index.html"}
    if not required.issubset(names) or not any(name.endswith(".dist-info/METADATA") for name in names):
        raise invalid()
    return {
        "format": FORMAT,
        "sha256": sha256,
        "bytes": size,
        "files": len(names),
        "uncompressed_bytes": total,
        "has_v2_assets": True,
        "verified": True,
    }
