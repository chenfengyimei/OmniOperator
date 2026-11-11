"""Installation status: read-only projection of receipt, database and staged candidates."""

import asyncio
import hashlib
import json
import sys
import zipfile

from omni.adapters.platform.paths import InstancePaths
from omni.adapters.platform.wheel_backup import backup_candidate
from omni.adapters.platform.wheel_prepare import prepare_wheel
from omni.adapters.platform.wheel_rollback import rollback_check
from omni.adapters.platform.wheel_stage import stage_wheel
from omni.adapters.platform.wheel_status import installation_status
from omni.composition.cli import main, seal_installation
from omni.composition.instance import open_instance

REQUIRED = (
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
            archive.writestr(name, "x")
    return path, hashlib.sha256(path.read_bytes()).hexdigest()


def test_status_is_read_only_and_empty_without_candidates(tmp_path):
    paths = home_with_receipt(tmp_path)
    report = installation_status(paths)
    assert report["format"] == "omni-installation-status-1"
    assert report["manifest_problem"] == "" and report["database"]["schema"] == report["expected_schema"]
    assert report["upgrade_required"] is False and report["candidates"] == []
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
