"""Atomic local staging of a verified wheel; never extracts, switches directories or runs migrations."""

from __future__ import annotations

import json
import os
import shutil
from pathlib import Path
from typing import cast

from omni.adapters.persistence.maintenance import durable_json
from omni.adapters.platform.releases.wheel_artifact import inspect_wheel
from omni.adapters.platform.runtime.permissions import private_path
from omni.application.ports import Record
from omni.domain.contracts import BusinessError

FORMAT = "omni-staged-update-1"
STAGE_ROOT = "updates"


def invalid(code: str = "staging_invalid") -> BusinessError:
    return BusinessError(code, "Stage one verified wheel for review; nothing is installed", status=422)


def stage_wheel(home: Path, wheel: Path, sha256: str) -> dict[str, object]:
    report = inspect_wheel(wheel, sha256)
    if home.is_symlink() or not home.is_dir():
        raise invalid("staging_home_invalid")
    root = home / STAGE_ROOT
    if root.is_symlink():
        raise invalid("staging_unsafe_path")
    stage = root / sha256[:16]
    if stage.is_symlink():
        raise invalid("staging_unsafe_path")
    receipt_path = stage / "staged.json"
    if stage.is_dir() and receipt_path.is_file():
        try:
            existing = json.loads(receipt_path.read_text(encoding="utf-8"))
        except (ValueError, OSError, UnicodeError):
            raise invalid("staging_receipt_invalid") from None
        if (
            not isinstance(existing, dict)
            or cast(Record, existing).get("format") != FORMAT
            or cast(Record, existing).get("wheel_sha256") != sha256
        ):
            raise invalid("staging_receipt_invalid")
        if not (stage / "artifact.whl").is_file():
            raise invalid("staging_receipt_invalid")
        return {"format": FORMAT, "written": False, "stage": str(stage), "manifest": existing}
    root.mkdir(parents=True, exist_ok=True)
    private_path(root, directory=True)
    stage.mkdir(parents=True, exist_ok=True)
    private_path(stage, directory=True)
    artifact = stage / "artifact.whl"
    temporary = stage / f".{os.getpid()}.partial"
    try:
        with wheel.open("rb") as source, temporary.open("xb") as target:
            shutil.copyfileobj(source, target)
            target.flush()
            os.fsync(target.fileno())
        private_path(temporary)
        os.replace(temporary, artifact)
    finally:
        temporary.unlink(missing_ok=True)
    manifest = {
        "format": FORMAT,
        "wheel_sha256": sha256,
        "wheel_bytes": report["bytes"],
        "wheel_files": report["files"],
        "uncompressed_bytes": report["uncompressed_bytes"],
        "extracted": False,
        "installed": False,
    }
    durable_json(receipt_path, cast(Record, manifest))
    private_path(receipt_path)
    return {"format": FORMAT, "written": True, "stage": str(stage), "manifest": manifest}
