"""Smoke-test a prepared candidate tree in a child interpreter; no install, switch or migration."""

from __future__ import annotations

import json
import os
import subprocess
import sys
from typing import cast

from ...application.ports import Record
from ...domain.contracts import BusinessError
from ..persistence.maintenance import durable_json
from .paths import InstancePaths
from .permissions import private_path
from .wheel_prepare import FORMAT as PREPARED_FORMAT

FORMAT = "omni-candidate-smoke-1"
CODE = (
    "import sys\n"
    "from pathlib import Path\n"
    "root = Path(sys.argv[1]).resolve()\n"
    "import omni\n"
    "path = Path(omni.__file__).resolve()\n"
    "assert root in path.parents or path == root / 'omni' / '__init__.py', (root, path)\n"
    "assert (root / 'omni/web_assets/ui/v2/index.html').is_file()\n"
)


def invalid(code: str = "smoke_invalid") -> BusinessError:
    return BusinessError(code, "Smoke-test only a prepared candidate tree", status=422)


def smoke_candidate(paths: InstancePaths, sha256: str) -> Record:
    stage = paths.home / "updates" / sha256[:16]
    prepared_path = stage / "prepared.json"
    tree = stage / "tree"
    if not prepared_path.is_file() or not tree.is_dir():
        raise invalid("smoke_not_prepared")
    try:
        prepared = json.loads(prepared_path.read_text(encoding="utf-8"))
    except (ValueError, OSError, UnicodeError):
        raise invalid("smoke_receipt_invalid") from None
    if (
        not isinstance(prepared, dict)
        or cast(dict[str, object], prepared).get("format") != PREPARED_FORMAT
        or cast(dict[str, object], prepared).get("wheel_sha256") != sha256
    ):
        raise invalid("smoke_receipt_invalid")
    result_path = stage / "smoke.json"
    if result_path.is_file():
        try:
            existing = json.loads(result_path.read_text(encoding="utf-8"))
        except (ValueError, OSError, UnicodeError):
            raise invalid("smoke_receipt_invalid") from None
        if not isinstance(existing, dict):
            raise invalid("smoke_receipt_invalid")
        receipt = cast(Record, existing)
        if (
            receipt.get("format") != FORMAT
            or receipt.get("wheel_sha256") != sha256
            or receipt.get("verified") is not True
        ):
            raise invalid("smoke_receipt_invalid")
        return {"format": FORMAT, "written": False, **receipt}
    environment = {**os.environ, "PYTHONPATH": str(tree), "PYTHONDONTWRITEBYTECODE": "1"}
    try:
        completed = subprocess.run(
            [sys.executable, "-c", CODE, str(tree)],
            cwd=tree,
            env=environment,
            capture_output=True,
            text=True,
            timeout=30,
            check=False,
        )
    except (OSError, subprocess.TimeoutExpired) as exc:
        raise invalid("smoke_failed") from exc
    if completed.returncode != 0:
        raise invalid("smoke_failed")
    result: Record = {
        "format": FORMAT,
        "wheel_sha256": sha256,
        "python": sys.version.split()[0],
        "verified": True,
    }
    durable_json(result_path, result)
    private_path(result_path)
    return {"written": True, **result}
