"""Wheel artifact verification: hash, archive safety, V2 assets; read-only and never installs."""

import hashlib
import json
import sys
import zipfile

import pytest

from omni.adapters.platform.releases.wheel_artifact import inspect_wheel
from omni.composition.cli import main
from omni.domain.contracts import BusinessError

REQUIRED = (
    "omni/web_assets/index.html",
    "omni/web_assets/ui/v2/index.html",
    "omnioperator-0.1.0.dist-info/METADATA",
)


def wheel(tmp_path, extra=()):
    path = tmp_path / "candidate.whl"
    with zipfile.ZipFile(path, "w") as archive:
        for name in (*REQUIRED, *extra):
            archive.writestr(name, "x")
    return path, hashlib.sha256(path.read_bytes()).hexdigest()


def test_verified_wheel_reports_hash_and_v2_assets(tmp_path):
    path, sha256 = wheel(tmp_path)
    report = inspect_wheel(path, sha256)
    assert report["verified"] is True and report["has_v2_assets"] is True
    assert report["sha256"] == sha256 and report["files"] == len(REQUIRED)


def test_checksum_unsafe_path_and_missing_assets_fail_closed(tmp_path):
    path, _ = wheel(tmp_path)
    with pytest.raises(BusinessError) as checksum:
        inspect_wheel(path, "0" * 64)
    assert checksum.value.code == "artifact_checksum"

    unsafe, unsafe_sha = wheel(tmp_path, ("../escape.txt",))
    with pytest.raises(BusinessError) as traversal:
        inspect_wheel(unsafe, unsafe_sha)
    assert traversal.value.code == "artifact_unsafe_path"

    missing = tmp_path / "missing.whl"
    with zipfile.ZipFile(missing, "w") as archive:
        archive.writestr("omni/web_assets/index.html", "x")
        archive.writestr("omnioperator-0.1.0.dist-info/METADATA", "x")
    missing_sha = hashlib.sha256(missing.read_bytes()).hexdigest()
    with pytest.raises(BusinessError) as absent:
        inspect_wheel(missing, missing_sha)
    assert absent.value.code == "artifact_invalid"


def test_cli_artifact_emits_json(tmp_path, monkeypatch, capsys):
    path, sha256 = wheel(tmp_path)
    monkeypatch.setattr(sys, "argv", ["omni", "installation", "artifact", str(path), "--sha256", sha256])
    assert main() == 0
    payload = json.loads(capsys.readouterr().out)
    assert payload["format"] == "omni-wheel-artifact-1" and payload["verified"] is True
