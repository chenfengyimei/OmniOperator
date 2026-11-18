"""Reusable isolated_environment fixtures, extracted without collecting test cases."""

from __future__ import annotations

import hashlib
import json
import platform
import shutil
import subprocess
import sys
import zipfile
from pathlib import Path

import pytest

from omni.adapters.platform.releases import dependency_bundle as bundle
from omni.adapters.platform.releases import wheel_environment as environment
from omni.adapters.platform.releases.wheel_backup import backup_candidate
from omni.adapters.platform.releases.wheel_install import switch_candidate
from omni.adapters.platform.releases.wheel_prepare import prepare_wheel
from omni.adapters.platform.releases.wheel_rollback import rollback_check
from omni.adapters.platform.releases.wheel_smoke import smoke_candidate
from omni.adapters.platform.releases.wheel_stage import stage_wheel
from omni.adapters.platform.releases.wheel_uninstall import uninstall_candidate
from omni.adapters.platform.runtime.paths import InstanceLock
from omni.adapters.platform.runtime.permissions import private_path
from omni.composition.cli import main
from omni.domain.contracts import BusinessError
from omni_test_support.v2.wheel_environment import staged


def wheel(folder, name, module, body, *, requires=None, console=False):
    path = folder / f"{name}-1.0.0-py3-none-any.whl"
    dist = name + "-1.0.0.dist-info"
    files = {
        f"{module}/__init__.py": body,
        f"{dist}/METADATA": f"Metadata-Version: 2.1\nName: {name}\nVersion: 1.0.0\n"
        + (f"Requires-Dist: {requires}\n" if requires else ""),
        f"{dist}/WHEEL": "Wheel-Version: 1.0\nRoot-Is-Purelib: true\nTag: py3-none-any\n",
    }
    if name == "omnioperator":
        files.update({"omni/web_assets/index.html": "x", "omni/web_assets/ui/v2/index.html": "x"})
    if console:
        files.update(
            {
                "omni/fixture_console.py": "import json,sys\nfrom omni_fixture_dependency import VALUE\ndef main():\n print(json.dumps({'dependency':VALUE,'prefix':sys.prefix})); return 0\n",
                "omni/composition/__init__.py": "",
                "omni/composition/cli.py": "from omni.fixture_console import main\nraise SystemExit(main())\n",
                f"{dist}/entry_points.txt": "[console_scripts]\nomni-v2 = omni.fixture_console:main\n",
            }
        )
    files[f"{dist}/RECORD"] = "".join(f"{name},,\n" for name in files) + f"{dist}/RECORD,,\n"
    with zipfile.ZipFile(path, "w") as archive:
        for name, content in files.items():
            archive.writestr(name, content)
    return path


def manifest(root, wheel_paths=(), **changes):
    value = {
        "format": bundle.FORMAT,
        "platform": sys.platform,
        "python": f"{sys.version_info.major}.{sys.version_info.minor}",
        "machine": platform.machine().casefold(),
        "wheels": [
            {"file": path.name, "bytes": path.stat().st_size, "sha256": hashlib.sha256(path.read_bytes()).hexdigest()}
            for path in wheel_paths
        ],
        **changes,
    }
    raw = json.dumps(value).encode()
    (root / "manifest.json").write_bytes(raw)
    return hashlib.sha256(raw).hexdigest()


def dependency(tmp_path):
    root = tmp_path / "bundle with space"
    root.mkdir()
    path = wheel(root, "omni_fixture_dependency", "omni_fixture_dependency", 'VALUE="pinned local value"\n')
    return root, path, manifest(root, [path])


__all__ = [
    "BusinessError",
    "InstanceLock",
    "Path",
    "backup_candidate",
    "bundle",
    "dependency",
    "environment",
    "hashlib",
    "json",
    "main",
    "manifest",
    "platform",
    "prepare_wheel",
    "private_path",
    "pytest",
    "rollback_check",
    "shutil",
    "smoke_candidate",
    "stage_wheel",
    "staged",
    "subprocess",
    "switch_candidate",
    "sys",
    "uninstall_candidate",
    "wheel",
    "zipfile",
]
