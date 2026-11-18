"""Installation status: read-only projection of receipt, database and staged candidates."""

import asyncio
import hashlib
import json
import sys
import zipfile

from omni.adapters.platform.runtime.paths import InstancePaths
from omni.adapters.platform.wheel_backup import backup_candidate
from omni.adapters.platform.wheel_install import switch_candidate
from omni.adapters.platform.wheel_prepare import prepare_wheel
from omni.adapters.platform.wheel_rollback import rollback_check
from omni.adapters.platform.wheel_smoke import smoke_candidate
from omni.adapters.platform.wheel_stage import stage_wheel
from omni.adapters.platform.wheel_status import installation_status
from omni.composition.cli import main, seal_installation
from omni.composition.instance import open_instance

REQUIRED = (
    "omni/__init__.py",
    "omni/web_assets/index.html",
    "omni/web_assets/ui/v2/index.html",
    "omnioperator-0.1.0.dist-info/METADATA",
)


async def _open_and_close(home):
    instance = await open_instance(home)
    await instance.close()


def home_with_receipt(tmp_path):
    home = tmp_path / "home"
    asyncio.run(_open_and_close(home))
    paths = InstancePaths.resolve(home)
    seal_installation(paths)
    return paths


def wheel(tmp_path):
    path = tmp_path / "candidate.whl"
    with zipfile.ZipFile(path, "w") as archive:
        for name in REQUIRED:
            archive.writestr(name, "" if name.endswith("__init__.py") else "x")
    return path, hashlib.sha256(path.read_bytes()).hexdigest()


def test_status_is_read_only_and_empty_without_candidates(tmp_path):
    paths = home_with_receipt(tmp_path)
    report = installation_status(paths)
    assert report["format"] == "omni-installation-status-1"
    assert report["manifest_problem"] == "" and report["database"]["schema"] == report["expected_schema"]
    assert report["upgrade_required"] is False and report["candidates"] == []
    assert report["runtime"]["active"] is False and report["runtime"]["rollback_live"] is False
    assert not (paths.home / "updates").exists()


def test_status_tracks_candidate_progress(tmp_path):
    paths = home_with_receipt(tmp_path)
    path, sha256 = wheel(tmp_path)
    stage_wheel(paths.home, path, sha256)
    first = installation_status(paths)["candidates"][0]
    assert first["stage"] == sha256[:16] and first["staged"] is True
    assert first["prepared"] is False and first["next_step"] == "prepare"

    prepare_wheel(paths.home, sha256)
    assert installation_status(paths)["candidates"][0]["next_step"] == "backup"
    backup_candidate(paths, sha256)
    assert installation_status(paths)["candidates"][0]["next_step"] == "rollback-check"
    rollback_check(paths, sha256)
    done = installation_status(paths)["candidates"][0]
    assert done["next_step"] == "switch" and done["backup"] is True and done["rollback"] is True
    assert done["wheel_sha256"] == sha256


def test_cli_status_emits_json(tmp_path, monkeypatch, capsys):
    paths = home_with_receipt(tmp_path)
    monkeypatch.setattr(sys, "argv", ["omni", "--home", str(paths.home), "installation", "status"])
    assert main() == 0
    payload = json.loads(capsys.readouterr().out)
    assert payload["format"] == "omni-installation-status-1"


def test_status_projects_runtime_and_candidate_flags(tmp_path):
    paths = home_with_receipt(tmp_path)
    path, sha256 = wheel(tmp_path)
    stage_wheel(paths.home, path, sha256)
    prepare_wheel(paths.home, sha256)
    smoke_candidate(paths, sha256)
    backup_candidate(paths, sha256)
    rollback_check(paths, sha256)
    switch_candidate(paths, sha256)
    report = installation_status(paths)
    assert report["runtime"]["active"] is True and report["runtime"]["wheel_sha256"] == sha256
    candidate = report["candidates"][0]
    assert candidate["active"] is True and candidate["next_step"] == "switch"
    assert candidate["environment"] is False and candidate["migrate"] is False
