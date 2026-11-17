"""Offline candidate environment: venv installs the staged wheel without network and proves import."""

import asyncio
import hashlib
import json
import sys
import zipfile
from pathlib import Path

import pytest

from omni.adapters.platform.paths import InstancePaths
from omni.adapters.platform.wheel_backup import backup_candidate
from omni.adapters.platform.wheel_environment import create_environment
from omni.adapters.platform.wheel_install import switch_candidate
from omni.adapters.platform.wheel_prepare import prepare_wheel
from omni.adapters.platform.wheel_rollback import rollback_check
from omni.adapters.platform.wheel_smoke import smoke_candidate
from omni.adapters.platform.wheel_stage import stage_wheel
from omni.composition.cli import main, seal_installation
from omni.composition.instance import open_instance
from omni.domain.contracts import BusinessError

DIST = "omnioperator-0.1.0.dist-info"


async def _open_and_close(home):
    instance = await open_instance(home)
    await instance.close()


def installable_wheel(tmp_path):
    path = tmp_path / "omnioperator-0.1.0-py3-none-any.whl"
    members = {
        "omni/__init__.py": "",
        "omni/web_assets/index.html": "x",
        "omni/web_assets/ui/v2/index.html": "x",
        f"{DIST}/METADATA": "Metadata-Version: 2.1\nName: omnioperator\nVersion: 0.1.0\n",
        f"{DIST}/WHEEL": "Wheel-Version: 1.0\nGenerator: test\nRoot-Is-Purelib: true\nTag: py3-none-any\n",
    }
    members[f"{DIST}/RECORD"] = "".join(f"{name},,\n" for name in members) + f"{DIST}/RECORD,,\n"
    with zipfile.ZipFile(path, "w") as archive:
        for name, data in members.items():
            archive.writestr(name, data)
    return path, hashlib.sha256(path.read_bytes()).hexdigest()


def staged(tmp_path):
    home = tmp_path / "home"
    asyncio.run(_open_and_close(home))
    paths = InstancePaths.resolve(home)
    seal_installation(paths)
    path, sha256 = installable_wheel(tmp_path)
    stage_wheel(home, path, sha256)
    return paths, sha256


def test_environment_installs_offline_and_is_idempotent(tmp_path):
    paths, sha256 = staged(tmp_path)
    first = create_environment(paths, sha256)
    assert first["written"] is True and first["installed"] is True
    assert first["mode"] == "system-site-packages"
    python = (
        paths.home
        / "updates"
        / sha256[:16]
        / "venv"
        / ("Scripts/python.exe" if sys.platform == "win32" else "bin/python")
    )
    assert python.is_file()
    record = json.loads((paths.home / "updates" / sha256[:16] / "environment.json").read_text(encoding="utf-8"))
    assert record["wheel_sha256"] == sha256
    assert str(paths.home / "updates" / sha256[:16] / "venv") in record["import_path"]
    again = create_environment(paths, sha256)
    assert again["written"] is False and again["installed"] is True


def test_environment_requires_staged_wheel_and_valid_receipt(tmp_path):
    paths = InstancePaths.resolve(tmp_path / "home")
    with pytest.raises(BusinessError) as missing:
        create_environment(paths, "a" * 64)
    assert missing.value.code == "environment_not_staged"

    paths, sha256 = staged(tmp_path)
    stage = paths.home / "updates" / sha256[:16]
    (stage / "staged.json").write_text(json.dumps({"format": "other", "wheel_sha256": sha256}), encoding="utf-8")
    with pytest.raises(BusinessError) as corrupt:
        create_environment(paths, sha256)
    assert corrupt.value.code == "environment_receipt_invalid"


def test_cli_environment_emits_json(tmp_path, monkeypatch, capsys):
    paths, sha256 = staged(tmp_path)
    monkeypatch.setattr(
        sys, "argv", ["omni", "--home", str(paths.home), "installation", "environment", "--sha256", sha256]
    )
    assert main() == 0
    payload = json.loads(capsys.readouterr().out)
    assert payload["format"] == "omni-environment-1" and payload["installed"] is True


def test_switch_prefers_the_installed_environment(tmp_path):
    paths, sha256 = staged(tmp_path)
    environment = create_environment(paths, sha256)
    prepare_wheel(paths.home, sha256)
    smoke_candidate(paths, sha256)
    backup_candidate(paths, sha256)
    rollback_check(paths, sha256)
    switched = switch_candidate(paths, sha256)
    assert switched["mode"] == "venv" and switched["python"] == environment["python"]
    relative = Path(environment["python"]).relative_to(paths.home)
    assert f'"%~dp0{relative}" -I -X utf8 ' in (paths.home / "omni.cmd").read_text(encoding="utf-8")
    assert environment["python"] in (paths.home / "omni").read_text(encoding="utf-8")
    launch = (paths.home / "runtime" / "launch.py").read_text(encoding="utf-8")
    assert environment["python"] not in launch and 'pointer.get("mode") != "venv"' in launch


def test_environment_import_proof_preserves_non_legacy_unicode_path(tmp_path):
    paths, sha256 = staged(tmp_path / "用户🙂")
    result = create_environment(paths, sha256)
    assert result["installed"] is True
    assert Path(result["import_path"]).is_relative_to(paths.home / "updates" / sha256[:16] / "venv")
    assert "🙂" in result["import_path"]
