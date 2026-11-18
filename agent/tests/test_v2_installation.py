"""V2 installation receipt: read-only preflight and one explicit seal; no upgrade execution."""

import asyncio
import json
import sys

import pytest

from omni.adapters.platform.runtime.paths import InstancePaths
from omni.composition.cli import installation_report, main, seal_installation
from omni.composition.instance import open_instance
from omni.domain.contracts import BusinessError


def initialized(tmp_path):
    home = tmp_path / "home"
    asyncio.run(_open_and_close(home))
    return InstancePaths.resolve(home)


async def _open_and_close(home):
    instance = await open_instance(home)
    await instance.close()


def test_verify_is_read_only_and_seal_is_idempotent(tmp_path):
    paths = initialized(tmp_path)
    manifest_path = paths.home / "installation.json"
    report = installation_report(paths)
    assert report["manifest"] is None and report["manifest_problem"] == ""
    assert report["database"]["exists"] is True and report["database"]["schema"] == report["expected_schema"]
    assert report["upgrade_required"] is False and report["ready"] is False
    assert not manifest_path.exists()

    first = seal_installation(paths)
    assert first["written"] is True
    receipt = json.loads(manifest_path.read_text(encoding="utf-8"))
    assert receipt["schema_version"] == report["expected_schema"]
    seeded = installation_report(paths)
    assert seeded["ready"] is True and seeded["manifest"] == first["manifest"]

    again = seal_installation(paths)
    assert again["written"] is False and again["manifest"] == first["manifest"]


def test_tampered_or_changed_receipt_is_rejected(tmp_path):
    paths = initialized(tmp_path)
    seal_installation(paths)
    manifest_path = paths.home / "installation.json"
    receipt = json.loads(manifest_path.read_text(encoding="utf-8"))
    receipt["schema_version"] = "v2_9999"
    manifest_path.write_text(json.dumps(receipt), encoding="utf-8")
    report = installation_report(paths)
    assert report["manifest_problem"] == "manifest_mismatch" and report["ready"] is False


def test_seal_refuses_missing_home_and_pending_migrations(tmp_path, monkeypatch):
    with pytest.raises(BusinessError) as missing:
        seal_installation(InstancePaths.resolve(tmp_path / "absent"))
    assert missing.value.code == "installation_missing"

    paths = initialized(tmp_path)
    monkeypatch.setattr("omni.adapters.persistence.migration_config.head", lambda config=None: "v2_9999")
    with pytest.raises(BusinessError) as pending:
        seal_installation(paths)
    assert pending.value.code == "installation_pending_upgrade"
    report = installation_report(paths)
    assert report["upgrade_required"] is True and report["ready"] is False


def test_runtime_accepts_sealed_receipt_and_rejects_tampering(tmp_path):
    paths = initialized(tmp_path)
    seal_installation(paths)
    asyncio.run(_open_and_close(paths.home))

    manifest_path = paths.home / "installation.json"
    receipt = json.loads(manifest_path.read_text(encoding="utf-8"))
    receipt["schema_version"] = "v2_9999"
    manifest_path.write_text(json.dumps(receipt), encoding="utf-8")
    with pytest.raises(BusinessError) as mismatch:
        asyncio.run(_open_and_close(paths.home))
    assert mismatch.value.code == "installation_mismatch"

    manifest_path.write_text("{", encoding="utf-8")
    with pytest.raises(BusinessError) as invalid:
        asyncio.run(_open_and_close(paths.home))
    assert invalid.value.code == "installation_invalid"


def test_cli_verify_emits_json(tmp_path, monkeypatch, capsys):
    paths = initialized(tmp_path)
    monkeypatch.setattr(sys, "argv", ["omni", "--home", str(paths.home), "installation", "verify"])
    assert main() == 0
    payload = json.loads(capsys.readouterr().out)
    assert payload["format"] == "omni-installation-report-1"
    assert payload["database"]["schema"] == payload["expected_schema"]
