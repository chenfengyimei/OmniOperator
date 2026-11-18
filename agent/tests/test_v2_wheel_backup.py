"""Update backup step: consistent snapshot outside home, idempotent receipt, no switch."""

import asyncio
import hashlib
import json
import sys
import zipfile

import pytest

from omni.adapters.platform.runtime.paths import InstanceLock, InstancePaths
from omni.adapters.platform.wheel_backup import backup_candidate
from omni.adapters.platform.wheel_prepare import prepare_wheel
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


def candidate(tmp_path):
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
    return paths, sha256


def test_backup_runs_once_outside_home(tmp_path):
    paths, sha256 = candidate(tmp_path)
    first = backup_candidate(paths, sha256)
    assert first["written"] is True
    destination = paths.home.with_name(f"{paths.home.name}-backup-{sha256[:16]}")
    assert destination.is_dir() and (destination / "manifest.json").is_file()
    record = json.loads((paths.home / "updates" / sha256[:16] / "backup.json").read_text(encoding="utf-8"))
    assert record["destination"] == str(destination) and record["wheel_sha256"] == sha256

    again = backup_candidate(paths, sha256)
    assert again["written"] is False and again["backup"] is None


def test_backup_refuses_running_instance(tmp_path):
    paths, sha256 = candidate(tmp_path)
    lock = InstanceLock(paths)
    lock.acquire()
    try:
        with pytest.raises(BusinessError) as running:
            backup_candidate(paths, sha256)
        assert running.value.code == "instance_running"
    finally:
        lock.close()
    assert not (paths.home / "updates" / sha256[:16] / "backup.json").exists()


def test_backup_never_overwrites_an_existing_destination(tmp_path):
    paths, sha256 = candidate(tmp_path)
    paths.home.with_name(f"{paths.home.name}-backup-{sha256[:16]}").mkdir()
    with pytest.raises(BusinessError) as exists:
        backup_candidate(paths, sha256)
    assert exists.value.code == "destination_exists"


def test_cli_backup_emits_json(tmp_path, monkeypatch, capsys):
    paths, sha256 = candidate(tmp_path)
    monkeypatch.setattr(sys, "argv", ["omni", "--home", str(paths.home), "installation", "backup", "--sha256", sha256])
    assert main() == 0
    payload = json.loads(capsys.readouterr().out)
    assert payload["format"] == "omni-update-backup-1" and payload["written"] is True
