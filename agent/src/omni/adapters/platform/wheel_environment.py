"""Create an offline candidate environment: a venv that installs the staged wheel without a network."""

from __future__ import annotations

import json
import re
import shutil
import subprocess
import sys
import zipfile
from pathlib import Path
from typing import cast

from ...application.ports import Record
from ...domain.contracts import BusinessError
from ..persistence.maintenance import durable_json
from .dependency_bundle import SHA, capture_bundle, no_links
from .paths import InstanceLock, InstancePaths
from .permissions import private_path
from .runtime_identity import read_private
from .wheel_artifact import inspect_wheel
from .wheel_stage import FORMAT as STAGED_FORMAT

FORMAT = "omni-environment-1"


def invalid(code: str = "environment_invalid") -> BusinessError:
    return BusinessError(code, "Create an environment only for a staged, hash-matched wheel", status=422)


def _python(venv: Path) -> Path:
    return venv / ("Scripts/python.exe" if sys.platform == "win32" else "bin/python")


def _run(command: list[str], cwd: Path) -> subprocess.CompletedProcess[str]:
    try:
        return subprocess.run(
            command, cwd=cwd, capture_output=True, text=True, encoding="utf-8", timeout=300, check=False
        )
    except (OSError, subprocess.TimeoutExpired) as exc:
        raise invalid("environment_failed") from exc


def _installable(artifact: Path, stage: Path, sha256: str) -> Path:
    """pip requires the wheel filename to match its metadata; the staged name is generic."""
    try:
        with zipfile.ZipFile(artifact) as archive:
            metadata = next((name for name in archive.namelist() if name.endswith(".dist-info/METADATA")), "")
            if not metadata or archive.getinfo(metadata).file_size > 1024 * 1024:
                raise invalid("environment_wheel_invalid")
            text = archive.read(metadata).decode("utf-8")
    except (OSError, KeyError, zipfile.BadZipFile, UnicodeError):
        raise invalid("environment_wheel_invalid") from None
    fields: dict[str, str] = {}
    for line in text.splitlines():
        key, _, value = line.partition(":")
        if key in {"Name", "Version"} and value.strip():
            if key in fields:
                raise invalid("environment_wheel_invalid")
            fields[key] = value.strip()
    if (
        set(fields) != {"Name", "Version"}
        or fields["Name"] != "omnioperator"
        or re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9.+_-]{0,99}", fields["Version"]) is None
    ):
        raise invalid("environment_wheel_invalid")
    folder = stage / "install"
    no_links(folder)
    folder.mkdir(exist_ok=True)
    target = folder / f"{fields['Name']}-{fields['Version']}-py3-none-any.whl"
    no_links(target)
    if not target.is_file():
        shutil.copyfile(artifact, target)
    inspect_wheel(target, sha256)
    return target


def create_environment(
    paths: InstancePaths, sha256: str, *, dependency_bundle: Path | None = None, dependency_sha256: str = ""
) -> Record:
    if SHA.fullmatch(sha256) is None or (dependency_bundle is None) != (not dependency_sha256):
        raise invalid()
    if dependency_sha256 and SHA.fullmatch(dependency_sha256) is None:
        raise invalid()
    stage = paths.home / "updates" / sha256[:16]
    no_links(stage)
    staged_path = stage / "staged.json"
    artifact = stage / "artifact.whl"
    if not staged_path.is_file() or not artifact.is_file():
        raise invalid("environment_not_staged")
    lock = InstanceLock(paths)
    lock.acquire()
    try:
        return _create(stage, sha256, dependency_bundle, dependency_sha256)
    finally:
        lock.close()


def _remove_environment(venv: Path, stage: Path) -> None:
    no_links(venv)
    if venv.parent.resolve() != stage.resolve() or venv.name != "venv":
        raise invalid()
    if venv.exists():
        shutil.rmtree(venv)


def installed_environment(stage: Path, sha256: str) -> Record | None:
    path = stage / "environment.json"
    no_links(path)
    if not path.exists():
        return None
    try:
        value = json.loads(read_private(path))
    except (ValueError, OSError, UnicodeError) as exc:
        raise invalid("environment_receipt_invalid") from exc
    receipt = cast(Record, value) if isinstance(value, dict) else None
    venv = stage / "venv"
    no_links(venv)
    if (
        receipt is None
        or receipt.get("format") != FORMAT
        or receipt.get("wheel_sha256") != sha256
        or receipt.get("installed") is not True
        or receipt.get("stable_interpreter_path") is not True
        or receipt.get("mode") not in ("system-site-packages", "isolated-wheelhouse")
        or receipt.get("python") != str(_python(venv))
        or receipt.get("venv") != str(venv)
        or not _python(venv).is_file()
    ):
        raise invalid("environment_receipt_invalid")
    no_links(_python(venv))
    if (
        receipt["mode"] == "isolated-wheelhouse"
        and "include-system-site-packages = false" not in read_private(venv / "pyvenv.cfg").decode("utf-8").lower()
    ):
        raise invalid("environment_receipt_invalid")
    return receipt


def _create(stage: Path, sha256: str, dependency_bundle: Path | None, dependency_sha256: str) -> Record:
    artifact = stage / "artifact.whl"
    try:
        staged = json.loads(read_private(stage / "staged.json"))
    except (ValueError, OSError, UnicodeError):
        raise invalid("environment_receipt_invalid") from None
    if (
        not isinstance(staged, dict)
        or cast(dict[str, object], staged).get("format") != STAGED_FORMAT
        or cast(dict[str, object], staged).get("wheel_sha256") != sha256
    ):
        raise invalid("environment_receipt_invalid")
    inspect_wheel(artifact, sha256)
    mode = "isolated-wheelhouse" if dependency_bundle is not None else "system-site-packages"
    result_path = stage / "environment.json"
    venv = stage / "venv"
    no_links(venv)
    no_links(result_path)
    receipt = installed_environment(stage, sha256)
    if receipt is not None:
        if (
            receipt.get("mode") == mode
            and receipt.get("dependency_bundle_sha256", "") == dependency_sha256
            and receipt.get("python") == str(_python(venv))
            and _python(venv).is_file()
        ):
            if dependency_bundle is not None:
                capture_bundle(dependency_bundle, dependency_sha256, stage)
            return {"format": FORMAT, "written": False, **receipt}
        raise invalid("environment_receipt_invalid")
    if venv.exists() or (stage / "venv.partial").exists():
        raise invalid("environment_incomplete")
    requirements = (
        capture_bundle(dependency_bundle, dependency_sha256, stage) if dependency_bundle is not None else None
    )
    wheel = _installable(artifact, stage, sha256)
    # Console scripts embed the interpreter path. Build in the final private location,
    # under the instance lock, and publish the receipt only after all checks pass.
    venv.mkdir(mode=0o700)
    private_path(venv, directory=True)
    python = _python(venv)
    try:
        made = _run(
            [
                sys.executable,
                "-I",
                "-X",
                "utf8",
                "-m",
                "venv",
                "--copies",
                *(["--system-site-packages"] if requirements is None else []),
                str(venv),
            ],
            stage,
        )
        if made.returncode != 0:
            raise invalid("environment_failed")
        private_path(venv / "pyvenv.cfg")
        pip = [str(python), "-I", "-X", "utf8", "-m", "pip", "--isolated", "--disable-pip-version-check"]
        if requirements is not None:
            dependencies = _run(
                [
                    *pip,
                    "install",
                    "--no-index",
                    "--no-deps",
                    "--no-cache-dir",
                    "--only-binary=:all:",
                    "--require-hashes",
                    "-r",
                    str(requirements),
                ],
                stage,
            )
            if dependencies.returncode != 0:
                raise invalid("environment_dependencies_failed")
        installed = _run([*pip, "install", "--no-index", "--no-deps", str(wheel)], stage)
        if installed.returncode != 0:
            raise invalid("environment_install_failed")
        if requirements is not None and _run([*pip, "check"], stage).returncode != 0:
            raise invalid("environment_dependencies_failed")
        check = "import omni; print(omni.__file__)"
        if requirements is not None:
            check = (
                "import omni,site,sys; from pathlib import Path; root=Path(sys.prefix).resolve(); "
                "assert sys.prefix!=sys.base_prefix and site.ENABLE_USER_SITE is False; "
                "assert all(Path(p).resolve().is_relative_to(root) for p in sys.path if Path(p).name=='site-packages'); "
                "print(omni.__file__)"
            )
        imported = _run([str(python), "-I", "-X", "utf8", "-c", check], stage)
        if imported.returncode != 0 or not Path(imported.stdout.strip()).resolve().is_relative_to(venv.resolve()):
            raise invalid("environment_import_failed")
    except BaseException:
        _remove_environment(venv, stage)
        raise
    record: Record = {
        "format": FORMAT,
        "wheel_sha256": sha256,
        "installed": True,
        "stable_interpreter_path": True,
        "venv": str(venv),
        "python": str(python),
        "mode": mode,
        "dependency_bundle_sha256": dependency_sha256,
        "import_path": imported.stdout.strip().splitlines()[-1] if imported.stdout.strip() else "",
    }
    durable_json(result_path, record)
    private_path(result_path)
    return {"format": FORMAT, "written": True, **record}
