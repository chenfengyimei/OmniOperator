"""Switch-plan: read-only preconditions and ordered steps; never executes stop/switch/migrate."""

import asyncio
import hashlib
import json
import sys
import zipfile

import pytest

from omni.adapters.platform.paths import InstanceLock, InstancePaths
from omni.adapters.platform.wheel_prepare import prepare_wheel
from omni.adapters.platform.wheel_stage import stage_wheel
from omni.adapters.platform.wheel_switch import switch_plan
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


def candidate(tmp_path, seal=True):
    home = tmp_path / "home"
    asyncio.run(_open_and_close(home))
    paths = InstancePaths.resolve(home)
    if seal:
        seal_installation(paths)
    path = tmp_path / "candidate.whl"
    with zipfile.ZipFile(path, "w") as archive:
        for name in REQUIRED:
            archive.writestr(name, "x")
    sha256 = hashlib.sha256(path.read_bytes()).hexdigest()
    stage_wheel(home, path, sha256)
    prepare_wheel(home, sha256)
    return paths, sha256


def test_plan_is_read_only_and_orders_safe_steps(tmp_path):
    paths, sha256 = candidate(tmp_path)
    plan = switch_plan(paths, sha256)
    assert plan["executable"] is False and plan["requires_owner_approval"] is True
    assert [step["id"] for step in plan["steps"]] == [
        "backup",
        "stop",
        "switch",
        "migrate",
        "seal",
        "start",
        "health",
    ]
    assert plan["source_schema"] == plan["target_schema"]
    assert plan["wheel_sha256"] == sha256


def test_plan_requires_prepared_candidate_and_sealed_receipt(tmp_path):
    home = tmp_path / "home"
    asyncio.run(_open_and_close(home))
    paths = InstancePaths.resolve(home)
    with pytest.raises(BusinessError) as absent:
        switch_plan(paths, "0" * 64)
    assert absent.value.code == "switch_plan_not_prepared"

    paths, sha256 = candidate(tmp_path, seal=False)
    with pytest.raises(BusinessError) as unsealed:
        switch_plan(paths, sha256)
    assert unsealed.value.code == "switch_plan_unsealed"


def test_running_instance_blocks_the_plan(tmp_path):
    paths, sha256 = candidate(tmp_path)
    lock = InstanceLock(paths)
    lock.acquire()
    try:
        with pytest.raises(BusinessError) as running:
            switch_plan(paths, sha256)
        assert running.value.code == "instance_running"
    finally:
        lock.close()


def test_cli_switch_plan_emits_json(tmp_path, monkeypatch, capsys):
    paths, sha256 = candidate(tmp_path)
    monkeypatch.setattr(
        sys,
        "argv",
        ["omni", "--home", str(paths.home), "installation", "switch-plan", "--sha256", sha256],
    )
    assert main() == 0
    payload = json.loads(capsys.readouterr().out)
    assert payload["format"] == "omni-switch-plan-1" and payload["executable"] is False
