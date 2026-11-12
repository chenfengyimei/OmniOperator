"""Safely unpack one staged wheel into a candidate tree; never creates a venv, switches or migrates."""

from __future__ import annotations

import hashlib
import json
import os
import shutil
import zipfile
from pathlib import Path, PurePosixPath
from typing import cast

from ...domain.contracts import BusinessError
from ...application.ports import Record
from ..persistence.maintenance import durable_json
from .permissions import private_path
from .wheel_artifact import MAX_BYTES, MAX_FILES
from .wheel_stage import FORMAT as STAGED_FORMAT

FORMAT = "omni-prepared-update-1"


def invalid(code: str = "prepare_invalid") -> BusinessError:
    return BusinessError(code, "Prepare only a verified, already staged wheel", status=422)


def _digest(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for block in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def _safe_target(root: Path, name: str) -> Path:
    parts = PurePosixPath(name).parts
    if name.startswith("/") or "\\" in name or ".." in parts or not parts:
        raise invalid("prepare_unsafe_path")
    return root.joinpath(*parts)


def prepare_wheel(home: Path, sha256: str) -> dict[str, object]:
    stage = home / "updates" / sha256[:16]
    receipt_path = stage / "staged.json"
    artifact = stage / "artifact.whl"
    if stage.is_symlink() or not receipt_path.is_file() or not artifact.is_file():
        raise invalid("prepare_not_staged")
    try:
        receipt = json.loads(receipt_path.read_text(encoding="utf-8"))
    except (ValueError, OSError, UnicodeError):
        raise invalid("prepare_receipt_invalid") from None
    if (
        not isinstance(receipt, dict)
        or cast(Record, receipt).get("format") != STAGED_FORMAT
        or cast(Record, receipt).get("wheel_sha256") != sha256
        or cast(Record, receipt).get("extracted") is not False
    ):
        raise invalid("prepare_receipt_invalid")
    if _digest(artifact) != sha256:
        raise invalid("prepare_artifact_changed")

    tree = stage / "tree"
    prepared_path = stage / "prepared.json"
    if prepared_path.is_file():
        try:
            prepared = json.loads(prepared_path.read_text(encoding="utf-8"))
        except (ValueError, OSError, UnicodeError):
            raise invalid("prepare_receipt_invalid") from None
        if (
            not isinstance(prepared, dict)
            or cast(Record, prepared).get("format") != FORMAT
            or cast(Record, prepared).get("wheel_sha256") != sha256
            or not (tree / "omni/web_assets/ui/v2/index.html").is_file()
        ):
            raise invalid("prepare_receipt_invalid")
        return {"format": FORMAT, "written": False, "tree": str(tree), "manifest": prepared}
    if tree.exists() or tree.is_symlink():
        raise invalid("prepare_incomplete")

    temporary = stage / ".tree.partial"
    if temporary.exists() or temporary.is_symlink():
        raise invalid("prepare_incomplete")
    files = 0
    total = 0
    try:
        temporary.mkdir()
        with zipfile.ZipFile(artifact) as archive:
            infos = archive.infolist()
            if not infos or len(infos) > MAX_FILES or archive.testzip() is not None:
                raise invalid()
            for info in infos:
                target = _safe_target(temporary, info.filename)
                if info.is_dir():
                    target.mkdir(parents=True, exist_ok=True)
                    continue
                total += info.file_size
                if total > MAX_BYTES:
                    raise invalid("prepare_too_large")
                target.parent.mkdir(parents=True, exist_ok=True)
                if target.exists():
                    raise invalid("prepare_duplicate_path")
                with archive.open(info) as source, target.open("xb") as sink:
                    shutil.copyfileobj(source, sink)
                    sink.flush()
                    os.fsync(sink.fileno())
                files += 1
        if not (temporary / "omni/web_assets/ui/v2/index.html").is_file():
            raise invalid("prepare_assets_missing")
        os.replace(temporary, tree)
    except (zipfile.BadZipFile, OSError, RuntimeError) as exc:
        shutil.rmtree(temporary, ignore_errors=True)
        raise invalid() from exc
    except BusinessError:
        shutil.rmtree(temporary, ignore_errors=True)
        raise
    private_path(tree, directory=True)
    manifest = {"format": FORMAT, "wheel_sha256": sha256, "files": files, "bytes": total, "tree": "tree"}
    durable_json(prepared_path, cast(Record, manifest))
    private_path(prepared_path)
    return {"format": FORMAT, "written": True, "tree": str(tree), "manifest": manifest}
