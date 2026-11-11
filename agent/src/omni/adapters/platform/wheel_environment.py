"""Create an offline candidate environment: a venv that installs the staged wheel without a network."""

from __future__ import annotations

import json
import shutil
import subprocess
import sys
import zipfile
from pathlib import Path

from ...domain.contracts import BusinessError
from ..persistence.maintenance import durable_json
from .permissions import private_path
from .wheel_stage import FORMAT as STAGED_FORMAT

FORMAT = "omni-environment-1"


def invalid(code: str = "environment_invalid") -> BusinessError:
    return BusinessError(code, "Create an environment only for a staged, hash-matched wheel", status=422)


def _python(venv: Path) -> Path:
    return venv / ("Scripts/python.exe" if sys.platform == "win32" else "bin/python")


def _run(command: list[str], cwd: Path) -> subprocess.CompletedProcess[str]:
    try:
        return subprocess.run(command, cwd=cwd, capture_output=True, text=True, timeout=300, check=False)
    except (OSError, subprocess.TimeoutExpired) as exc:
        raise invalid("environment_failed") from exc


def _installable(artifact: Path, stage: Path) -> Path:
    """pip requires the wheel filename to match its metadata; the staged name is generic."""
    try:
        with zipfile.ZipFile(artifact) as archive:
            metadata = next((name for name in archive.namelist() if name.endswith(".dist-info/METADATA")), "")
            text = archive.read(metadata).decode("utf-8", "replace") if metadata else ""
    except (OSError, KeyError, zipfile.BadZipFile, UnicodeError):
        raise invalid("environment_wheel_invalid") from None
    fields = {}
    for line in text.splitlines():
        key, _, value = line.partition(":")
        if key in {"Name", "Version"} and value.strip():
            fields[key] = value.strip()
    if set(fields) != {"Name", "Version"}:
        raise invalid("environment_wheel_invalid")
    folder = stage / "install"
    folder.mkdir(exist_ok=True)
    target = folder / f"{fields['Name']}-{fields['Version']}-py3-none-any.whl"
    if not target.is_file():
        shutil.copyfile(artifact, target)
    return target


def create_environment(paths, sha256: str) -> dict[str, object]:
    stage = paths.home / "updates" / sha256[:16]
    staged_path = stage / "staged.json"
    artifact = stage / "artifact.whl"
    if not staged_path.is_file() or not artifact.is_file():
        raise invalid("environment_not_staged")
    try:
        staged = json.loads(staged_path.read_text(encoding="utf-8"))
    except (ValueError, OSError, UnicodeError):
        raise invalid("environment_receipt_invalid") from None
    if not isinstance(staged, dict) or staged.get("format") != STAGED_FORMAT or staged.get("wheel_sha256") != sha256:
        raise invalid("environment_receipt_invalid")
    result_path = stage / "environment.json"
    venv = stage / "venv"
    if result_path.is_file():
        try:
            existing = json.loads(result_path.read_text(encoding="utf-8"))
        except (ValueError, OSError, UnicodeError):
            raise invalid("environment_receipt_invalid") from None
        if (
            isinstance(existing, dict)
            and existing.get("format") == FORMAT
            and existing.get("wheel_sha256") == sha256
            and existing.get("installed") is True
            and _python(venv).is_file()
        ):
            return {"format": FORMAT, "written": False, **existing}
        raise invalid("environment_receipt_invalid")
    partial = stage / "venv.partial"
    shutil.rmtree(partial, ignore_errors=True)
    made = _run([sys.executable, "-m", "venv", "--system-site-packages", str(partial)], stage)
    if made.returncode != 0:
        shutil.rmtree(partial, ignore_errors=True)
        raise invalid("environment_failed")
    python = _python(partial)
    installed = _run(
        [
            str(python),
            "-m",
            "pip",
            "install",
            "--no-index",
            "--no-deps",
            "--disable-pip-version-check",
            str(_installable(artifact, stage)),
        ],
        stage,
    )
    if installed.returncode != 0:
        shutil.rmtree(partial, ignore_errors=True)
        raise invalid("environment_install_failed")
    if venv.exists():
        shutil.rmtree(venv)
    partial.rename(venv)
    python = _python(venv)
    imported = _run([str(python), "-c", "import omni; print(omni.__file__)"], stage)
    if imported.returncode != 0 or str(venv) not in imported.stdout:
        shutil.rmtree(venv, ignore_errors=True)
        raise invalid("environment_import_failed")
    record = {
        "format": FORMAT,
        "wheel_sha256": sha256,
        "installed": True,
        "venv": str(venv),
        "python": str(python),
        "mode": "system-site-packages",
        "import_path": imported.stdout.strip().splitlines()[-1] if imported.stdout.strip() else "",
    }
    durable_json(result_path, record)
    private_path(result_path)
    return {"format": FORMAT, "written": True, **record}
