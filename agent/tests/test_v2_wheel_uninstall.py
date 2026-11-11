"""Uninstall one staged candidate or deactivate the pointer; data and receipts stay."""

import asyncio
import hashlib
import json
import sys
import zipfile
from pathlib import Path

import pytest

from omni.adapters.platform.paths import InstanceLock, InstancePaths
from omni.adapters.platform.wheel_backup import backup_candidate
from omni.adapters.platform.wheel_install import switch_candidate
from omni.adapters.platform.wheel_prepare import prepare_wheel
from omni.adapters.platform.wheel_rollback import rollback_check
from omni.adapters.platform.wheel_smoke import smoke_candidate
from omni.adapters.platform.wheel_stage import stage_wheel
from omni.adapters.platform.wheel_uninstall import uninstall_candidate
from omni.composition.cli import main, seal_installation
from omni.composition.instance import open_instance
from omni.domain.contracts import BusinessError

REQUIRED = (
    "omni/__init__.py",
    "omni/web_assets/index.html",
    "omni/web_assets/ui/v2/index.html",
    "omnioperator-0.1.0.dist-info/METADATA",
)


async def _open_and_close(home):
    instance = await open_instance(home)
    await instance.close()


def active_candidate(tmp_path):
    home = tmp_path / "home"
    asyncio.run(_open_and_close(home))
    paths = InstancePaths.resolve(home)
    seal_installation(paths)
    path = tmp_path / "candidate.whl"
    with zipfile.ZipFile(path, "w") as archive:
        for name in REQUIRED:
            archive.writestr(name, "" if name.endswith("__init__.py") else "x")
    sha256 = hashlib.sha256(path.read_bytes()).hexdigest()
    stage_wheel(home, path, sha256)
    prepare_wheel(home, sha256)
    smoke_candidate(paths, sha256)
    backup_candidate(paths, sha256)
    rollback_check(paths, sha256)
    switch_candidate(paths, sha256)
    return paths, sha256


def test_uninstall_active_candidate_removes_code_and_pointer_but_keeps_data(tmp_path):
    paths, sha256 = active_candidate(tmp_path)
    result = uninstall_candidate(paths, sha256)
    assert result["deactivated"] is True and result["removed"] is True and result["data_kept"] is True
    assert not (paths.home / "updates" / sha256[:16]).exists()
    assert not (paths.home / "runtime" / "current.json").exists()
    assert not (paths.home / "omni.cmd").exists() and not (paths.home / "omni").exists()
    assert paths.database.is_file() and (paths.home / "installation.json").is_file()
    assert result["backup_kept"]


def test_uninstall_without_sha_only_deactivates(tmp_path):
    paths, sha256 = active_candidate(tmp_path)
    result = uninstall_candidate(paths)
    assert result["deactivated"] is True and result["removed"] is False
    assert (paths.home / "updates" / sha256[:16]).is_dir()
    assert not (paths.home / "runtime" / "current.json").exists()
    with pytest.raises(BusinessError) as absent:
        uninstall_candidate(paths)
    assert absent.value.code == "uninstall_missing"


def test_uninstall_rejects_non_active_unknown_and_running(tmp_path):
    paths, sha256 = active_candidate(tmp_path)
    with pytest.raises(BusinessError) as inactive:
        uninstall_candidate(paths, "b" * 64)
    assert inactive.value.code == "uninstall_missing"

    other = paths.home / "updates" / ("c" * 16)
    other.mkdir(parents=True)
    with pytest.raises(BusinessError) as not_active:
        uninstall_candidate(paths, "c" * 64)
    assert not_active.value.code == "uninstall_not_active"

    lock = InstanceLock(paths)
    lock.acquire()
    try:
        with pytest.raises(BusinessError) as running:
            uninstall_candidate(paths, sha256)
        assert running.value.code == "uninstall_instance_running"
    finally:
        lock.close()
    assert (paths.home / "updates" / sha256[:16]).is_dir()


def test_cli_uninstall_emits_json(tmp_path, monkeypatch, capsys):
    paths, sha256 = active_candidate(tmp_path)
    monkeypatch.setattr(
        sys, "argv", ["omni", "--home", str(paths.home), "installation", "uninstall", "--sha256", sha256]
    )
    assert main() == 0
    payload = json.loads(capsys.readouterr().out)
    assert payload["format"] == "omni-uninstall-1" and payload["removed"] is True


def test_purge_data_removes_local_state_but_keeps_home_and_backup(tmp_path):
    paths, _ = active_candidate(tmp_path)
    purged = uninstall_candidate(paths, purge=True)
    assert purged["data_kept"] is False and purged["backup_kept"]
    assert "data" in purged["purged"] and "updates" in purged["purged"]
    assert paths.home.is_dir()
    assert not (paths.home / "data").exists() and not (paths.home / "installation.json").exists()
    assert not (paths.home / "runtime").exists() and not (paths.home / "omni.cmd").exists()
    assert Path(purged["backup_kept"]).is_dir()


def test_purge_requires_explicit_flag_without_sha_and_idle_instance(tmp_path):
    paths, sha256 = active_candidate(tmp_path)
    with pytest.raises(BusinessError) as conflict:
        uninstall_candidate(paths, sha256, purge=True)
    assert conflict.value.code == "uninstall_purge_conflict"

    lock = InstanceLock(paths)
    lock.acquire()
    try:
        with pytest.raises(BusinessError) as running:
            uninstall_candidate(paths, purge=True)
        assert running.value.code == "uninstall_instance_running"
    finally:
        lock.close()
    assert paths.database.is_file()


def test_cli_purge_data_emits_json(tmp_path, monkeypatch, capsys):
    paths, _ = active_candidate(tmp_path)
    monkeypatch.setattr(sys, "argv", ["omni", "--home", str(paths.home), "installation", "uninstall", "--purge-data"])
    assert main() == 0
    payload = json.loads(capsys.readouterr().out)
    assert payload["format"] == "omni-uninstall-1" and "data" in payload["purged"]
