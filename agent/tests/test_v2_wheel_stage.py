"""Local wheel staging: verified bytes copied atomically, idempotent, never extracted or installed."""

import hashlib
import json
import sys
import zipfile

import pytest

from omni.adapters.platform.releases.wheel_stage import stage_wheel
from omni.composition.cli import main
from omni.domain.contracts import BusinessError

REQUIRED = (
    "omni/web_assets/index.html",
    "omni/web_assets/ui/v2/index.html",
    "omnioperator-0.1.0.dist-info/METADATA",
)


def wheel(tmp_path):
    path = tmp_path / "candidate.whl"
    with zipfile.ZipFile(path, "w") as archive:
        for name in REQUIRED:
            archive.writestr(name, "x")
    return path, hashlib.sha256(path.read_bytes()).hexdigest()


def test_stage_copies_verified_wheel_once(tmp_path):
    home = tmp_path / "home"
    home.mkdir()
    path, sha256 = wheel(tmp_path)
    first = stage_wheel(home, path, sha256)
    assert first["written"] is True and first["manifest"]["extracted"] is False
    artifact = home / "updates" / sha256[:16] / "artifact.whl"
    assert artifact.is_file() and hashlib.sha256(artifact.read_bytes()).hexdigest() == sha256
    receipt = json.loads((artifact.parent / "staged.json").read_text(encoding="utf-8"))
    assert receipt["wheel_sha256"] == sha256 and receipt["installed"] is False

    again = stage_wheel(home, path, sha256)
    assert again["written"] is False and again["manifest"] == first["manifest"]


def test_bad_hash_or_corrupt_receipt_never_stages(tmp_path):
    home = tmp_path / "home"
    home.mkdir()
    path, sha256 = wheel(tmp_path)
    with pytest.raises(BusinessError) as checksum:
        stage_wheel(home, path, "0" * 64)
    assert checksum.value.code == "artifact_checksum"
    assert not (home / "updates").exists()

    stage = home / "updates" / sha256[:16]
    stage.mkdir(parents=True)
    (stage / "staged.json").write_text("{", encoding="utf-8")
    with pytest.raises(BusinessError) as corrupt:
        stage_wheel(home, path, sha256)
    assert corrupt.value.code == "staging_receipt_invalid"


def test_stage_requires_a_real_home(tmp_path):
    path, sha256 = wheel(tmp_path)
    with pytest.raises(BusinessError) as missing:
        stage_wheel(tmp_path / "absent", path, sha256)
    assert missing.value.code == "staging_home_invalid"


def test_cli_stage_emits_json(tmp_path, monkeypatch, capsys):
    home = tmp_path / "home"
    home.mkdir()
    path, sha256 = wheel(tmp_path)
    monkeypatch.setattr(
        sys,
        "argv",
        ["omni", "--home", str(home), "installation", "stage", str(path), "--sha256", sha256],
    )
    assert main() == 0
    payload = json.loads(capsys.readouterr().out)
    assert payload["format"] == "omni-staged-update-1" and payload["written"] is True
