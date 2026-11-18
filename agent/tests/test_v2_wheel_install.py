"""Switch/migrate step: runtime pointer only after prepared/smoke/backup/rollback and reseal."""

import asyncio
import hashlib
import json
import sqlite3
import sys
import zipfile

import pytest
from alembic import command

from omni.adapters.persistence.maintenance import durable_json
from omni.adapters.persistence.migration_config import configuration, head
from omni.adapters.persistence.sqlalchemy_access import database_engine
from omni.adapters.platform.runtime.paths import InstanceLock, InstancePaths
from omni.adapters.platform.releases.wheel_backup import backup_candidate
from omni.adapters.platform.releases.wheel_install import install_candidate, migrate_candidate, rollback_candidate, runtime_current, switch_candidate
from omni.adapters.platform.releases.wheel_prepare import prepare_wheel
from omni.adapters.platform.releases.wheel_rollback import rollback_check
from omni.adapters.platform.releases.wheel_smoke import smoke_candidate
from omni.adapters.platform.releases.wheel_stage import stage_wheel
from omni.adapters.primitives import SystemClock
from omni.composition.cli import main, seal_installation
from omni.composition.instance import open_instance
from omni.domain.contracts import BusinessError
from omni.domain.installation import make_installation

REQUIRED = (
    "omni/__init__.py",
    "omni/web_assets/index.html",
    "omni/web_assets/ui/v2/index.html",
    "omnioperator-0.1.0.dist-info/METADATA",
)


async def _open_and_close(home):
    instance = await open_instance(home)
    await instance.close()


def candidate(tmp_path, drilled=True):
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
    if drilled:
        backup_candidate(paths, sha256)
        rollback_check(paths, sha256)
    return paths, sha256, path


def test_switch_writes_pointer_launcher_and_is_idempotent(tmp_path):
    paths, sha256, _ = candidate(tmp_path)
    first = switch_candidate(paths, sha256)
    assert first["written"] is True and first["wheel_sha256"] == sha256
    current = json.loads((paths.home / "runtime" / "current.json").read_text(encoding="utf-8"))
    assert current["tree"] == str(paths.home / "updates" / sha256[:16] / "tree")
    assert (paths.home / "runtime" / "launch.py").is_file()
    assert (paths.home / "omni.cmd").is_file() and (paths.home / "omni").is_file()
    again = switch_candidate(paths, sha256)
    assert again["written"] is False and again["previous"] == sha256
    state = runtime_current(paths)
    assert state["active"] is True and state["wheel_sha256"] == sha256


def test_switch_requires_smoke_backup_and_rollback(tmp_path):
    paths, sha256, _ = candidate(tmp_path, drilled=False)
    with pytest.raises(BusinessError) as missing:
        switch_candidate(paths, sha256)
    assert missing.value.code == "switch_missing_backup"
    assert not (paths.home / "runtime" / "current.json").exists()


def test_switch_refuses_running_instance(tmp_path):
    paths, sha256, _ = candidate(tmp_path)
    lock = InstanceLock(paths)
    lock.acquire()
    try:
        with pytest.raises(BusinessError) as running:
            switch_candidate(paths, sha256)
        assert running.value.code == "instance_running"
    finally:
        lock.close()
    assert not (paths.home / "runtime" / "current.json").exists()


def test_install_runs_the_whole_chain_and_switches(tmp_path):
    paths, sha256, wheel = candidate(tmp_path, drilled=False)
    result = install_candidate(paths, wheel, sha256)
    assert result["format"] == "omni-install-1" and result["verified"] is True
    assert result["migrate"]["written"] is False and result["migrate"]["resealed"] is False
    assert result["switch"]["written"] is True and result["requires_owner_approval"] is True
    assert runtime_current(paths)["active"] is True
    again = install_candidate(paths, wheel, sha256)
    assert again["staged"]["written"] is False and again["switch"]["written"] is False


def test_migrate_runs_pending_schema_and_reseals_before_switch(tmp_path):
    home = tmp_path / "home"
    (home / "data").mkdir(parents=True)
    paths = InstancePaths.resolve(home)
    database = paths.database
    sqlite3.connect(database).close()
    cfg = configuration()
    engine = database_engine(database)
    try:
        with engine.connect() as connection:
            connection.info["omni_write"] = True
            cfg.attributes["connection"] = connection
            command.upgrade(cfg, "v2_0036")
            if connection.in_transaction():
                connection.commit()
    finally:
        engine.dispose()
    old = "v2_0036"
    receipt = make_installation(SystemClock().now(), old, head())
    durable_json(home / "installation.json", receipt)

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

    migrated = migrate_candidate(paths, sha256)
    assert migrated["written"] is True and migrated["migrated"] is True
    assert migrated["before"] == old and migrated["after"] == head() and migrated["resealed"] is True
    assert json.loads((home / "installation.json").read_text(encoding="utf-8"))["schema_version"] == head()
    assert switch_candidate(paths, sha256)["written"] is True
    assert migrate_candidate(paths, sha256)["written"] is False


def test_cli_switch_and_current_emit_json(tmp_path, monkeypatch, capsys):
    paths, sha256, _ = candidate(tmp_path)
    monkeypatch.setattr(sys, "argv", ["omni", "--home", str(paths.home), "installation", "switch", "--sha256", sha256])
    assert main() == 0
    payload = json.loads(capsys.readouterr().out)
    assert payload["format"] == "omni-runtime-1" and payload["written"] is True

    monkeypatch.setattr(sys, "argv", ["omni", "--home", str(paths.home), "installation", "current"])
    assert main() == 0
    current = json.loads(capsys.readouterr().out)
    assert current["active"] is True and current["wheel_sha256"] == sha256


def _second_candidate(tmp_path, paths, tag):
    path = tmp_path / f"{tag}.whl"
    with zipfile.ZipFile(path, "w") as archive:
        for name in REQUIRED:
            archive.writestr(name, tag if name.endswith(".html") else "")
    sha256 = hashlib.sha256(path.read_bytes()).hexdigest()
    stage_wheel(paths.home, path, sha256)
    prepare_wheel(paths.home, sha256)
    smoke_candidate(paths, sha256)
    backup_candidate(paths, sha256)
    rollback_check(paths, sha256)
    switch_candidate(paths, sha256)
    return sha256


def test_live_rollback_returns_to_previous_pointer(tmp_path):
    paths, first, _ = candidate(tmp_path)
    switch_candidate(paths, first)
    second = _second_candidate(tmp_path, paths, "second")
    assert runtime_current(paths)["wheel_sha256"] == second
    rolled = rollback_candidate(paths)
    assert rolled["written"] is True and rolled["from"] == second and rolled["to"] == first
    assert runtime_current(paths)["wheel_sha256"] == first
    assert rollback_candidate(paths)["written"] is False
    assert switch_candidate(paths, second)["written"] is True
    assert (paths.home / "runtime" / "rollback-live.json").exists() is False


def test_live_rollback_requires_previous_and_idle_instance(tmp_path):
    paths, sha256, _ = candidate(tmp_path)
    with pytest.raises(BusinessError) as missing:
        rollback_candidate(paths)
    assert missing.value.code == "rollback_missing"

    switch_candidate(paths, sha256)
    second = _second_candidate(tmp_path, paths, "third")
    lock = InstanceLock(paths)
    lock.acquire()
    try:
        with pytest.raises(BusinessError) as running:
            rollback_candidate(paths)
        assert running.value.code == "instance_running"
    finally:
        lock.close()
    assert runtime_current(paths)["wheel_sha256"] == second


def test_cli_rollback_emits_json(tmp_path, monkeypatch, capsys):
    paths, first, _ = candidate(tmp_path)
    switch_candidate(paths, first)
    _second_candidate(tmp_path, paths, "cli-second")
    monkeypatch.setattr(sys, "argv", ["omni", "--home", str(paths.home), "installation", "rollback"])
    assert main() == 0
    payload = json.loads(capsys.readouterr().out)
    assert payload["format"] == "omni-live-rollback-1" and payload["to"] == first
