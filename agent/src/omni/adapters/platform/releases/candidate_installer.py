"""Bootstrap a fresh offline V2 candidate with a separate maintenance interpreter; never registers services."""

from __future__ import annotations

import hashlib
import json
import shutil
import subprocess
import sys
from pathlib import Path
from typing import cast

from omni.domain.contracts import BusinessError
from omni.adapters.platform.releases.dependency_bundle import SHA, capture_bundle, no_links
from omni.adapters.platform.runtime.permissions import private_path


def _invalid(code: str = "candidate_install_invalid") -> BusinessError:
    return BusinessError(code, "Use new, separate home/manager directories and pinned local wheels", status=422)


def _run(command: list[str], cwd: Path) -> str:
    try:
        result = subprocess.run(
            command, cwd=cwd, capture_output=True, text=True, encoding="utf-8", timeout=300, check=False
        )
    except (OSError, subprocess.TimeoutExpired) as exc:
        raise _invalid("candidate_install_failed") from exc
    if result.returncode:
        raise _invalid("candidate_install_failed")
    return result.stdout


def _wheel_hash(path: Path) -> str:
    digest, size = hashlib.sha256(), 0
    with path.open("rb") as stream:
        while block := stream.read(1024 * 1024):
            size += len(block)
            if size > 512 * 1024 * 1024:
                raise _invalid()
            digest.update(block)
    return digest.hexdigest()


def install_candidate(
    wheel: Path, sha256: str, bundle: Path, bundle_sha256: str, manager: Path, home: Path
) -> dict[str, object]:
    """The manager remains outside home so it can update or uninstall a stopped candidate on Windows."""
    if SHA.fullmatch(sha256) is None or SHA.fullmatch(bundle_sha256) is None:
        raise _invalid()
    for path in (wheel, bundle, manager, home):
        no_links(path)
    wheel, bundle, manager, home = (p.resolve() for p in (wheel, bundle, manager, home))
    if manager.exists() or home.exists() or not wheel.is_file() or wheel.suffix != ".whl":
        raise _invalid()
    for left, right in ((manager, home), (manager, bundle), (home, bundle)):
        if left.is_relative_to(right) or right.is_relative_to(left):
            raise _invalid()
    if wheel.is_relative_to(home) or wheel.is_relative_to(manager):
        raise _invalid()
    if _wheel_hash(wheel) != sha256:
        raise _invalid()
    manager.mkdir(parents=True, mode=0o700)
    private_path(manager, directory=True)
    requirements = capture_bundle(bundle, bundle_sha256, manager)
    captured = manager / wheel.name
    shutil.copyfile(wheel, captured)
    if _wheel_hash(captured) != sha256:
        raise _invalid()
    private_path(captured)
    venv = manager / "venv"
    _run([sys.executable, "-I", "-X", "utf8", "-m", "venv", "--copies", str(venv)], manager)
    private_path(venv, directory=True)
    private_path(venv / "pyvenv.cfg")
    python = venv / ("Scripts/python.exe" if sys.platform == "win32" else "bin/python")
    pip = [str(python), "-I", "-X", "utf8", "-m", "pip", "--isolated", "--disable-pip-version-check"]
    _run(
        [
            *pip,
            "install",
            "--no-index",
            "--no-deps",
            "--only-binary=:all:",
            "--require-hashes",
            "-r",
            str(requirements),
        ],
        manager,
    )
    _run([*pip, "install", "--no-index", "--no-deps", str(captured)], manager)
    _run([*pip, "check"], manager)
    cli = [str(python), "-I", "-X", "utf8", "-m", "omni.composition.cli", "--home", str(home)]
    results: dict[str, object] = {}
    for name, arguments in (
        ("init", ["init"]),
        ("seal", ["installation", "seal"]),
        ("stage", ["installation", "stage", str(captured), "--sha256", sha256]),
        (
            "environment",
            [
                "installation",
                "environment",
                "--sha256",
                sha256,
                "--dependency-bundle",
                str(manager / "dependencies"),
                "--dependency-sha256",
                bundle_sha256,
            ],
        ),
        ("install", ["installation", "install", str(captured), "--sha256", sha256]),
    ):
        results[name] = json.loads(_run([*cli, *arguments], manager))
    doctor = json.loads(_run([str(python), "-I", "-X", "utf8", str(home / "runtime/launch.py"), "doctor"], manager))
    if not isinstance(doctor, dict) or cast(dict[str, object], doctor).get("integrity") != "ok":
        raise _invalid("candidate_install_failed")
    record: dict[str, object] = {
        "format": "omni-candidate-install-1",
        "wheel_sha256": sha256,
        "dependency_bundle_sha256": bundle_sha256,
        "manager_python": str(python),
        "home": str(home),
        "launcher": str(home / "omni.cmd" if sys.platform == "win32" else home / "omni"),
        "operations": results,
        "doctor": doctor,
        "services_registered": False,
    }
    receipt = manager / "bootstrap.json"
    receipt.write_text(json.dumps(record, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    private_path(receipt)
    return record
