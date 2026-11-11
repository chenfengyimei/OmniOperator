"""Rollback drill: restore the recorded update backup into a new directory and verify; live home untouched."""

import asyncio
import hashlib
import json
import sys
import zipfile

import pytest

from omni.adapters.platform.paths import InstancePaths
from omni.adapters.platform.wheel_backup import backup_candidate
from omni.adapters.platform.wheel_prepare import prepare_wheel
from omni.adapters.platform.wheel_rollback import rollback_check
from omni.adapters.platform.wheel_stage import stage_wheel
from omni.composition.cli import main, seal_installation
from omni.composition.instance import open_instance
from omni.domain.contracts import BusinessError

REQUIRED = (
    "omni/web_assets/index.html",
    "omni/web_assets/ui/v2/index.html",
    "omnioperator-0.1.0.dist-info/METADATA",
)


async def _open_and_close(home):
    instance = await open_instance(home)
    await instance.close()


def backed_up(tmp_path):
    home = tmp_path / "home"
    asyncio.run(_open_and_close(home))
    paths = InstancePaths.resolve(home)
    seal_installation(paths)
    path = tmp_path / "candidate.whl"
    with zipfile.ZipFile(path, "w") as archive:
        for name in REQUIRED:
            archive.writestr(name, "x")
    sha256 = hashlib.sha256(path.read_bytes()).hexdigest()
    stage_wheel(home, path, sha256)
    prepare_wheel(home, sha256)
    backup_candidate(paths, sha256)
    return paths, sha256


def test_rollback_drill_restores_and_verifies_once(tmp_path):
    paths, sha256 = backed_up(tmp_path)
    first = rollback_check(paths, sha256)
    assert first["written"] is True and first["verified"] is True
    target = paths.home.with_name(f"{paths.home.name}-rollback-{sha256[:16]}")
    assert (target / "data/omni.db").is_file()
    assert first["schema"] and first["target"] == str(target)
    assert (paths.home / "updates" / sha256[:16] / "rollback.json").is_file()

    again = rollback_check(paths, sha256)
    assert again["written"] is False and again["verified"] is True


def test_rollback_requires_backup_and_new_target(tmp_path):
    home = tmp_path / "home"
    asyncio.run(_open_and_close(home))
    paths = InstancePaths.resolve(home)
    with pytest.raises(BusinessError) as absent:
        rollback_check(paths, "0" * 64)
    assert absent.value.code == "rollback_not_backed_up"

    paths, sha256 = backed_up(tmp_path)
    paths.home.with_name(f"{paths.home.name}-rollback-{sha256[:16]}").mkdir()
    with pytest.raises(BusinessError) as exists:
        rollback_check(paths, sha256)
    assert exists.value.code == "rollback_target_exists"


def test_cli_rollback_check_emits_json(tmp_path, monkeypatch, capsys):
    paths, sha256 = backed_up(tmp_path)
    monkeypatch.setattr(
        sys, "argv", ["omni", "--home", str(paths.home), "installation", "rollback-check", "--sha256", sha256]
    )
    assert main() == 0
    payload = json.loads(capsys.readouterr().out)
    assert payload["format"] == "omni-rollback-check-1" and payload["written"] is True
