"""Prepare step: safely unpack a staged wheel into a candidate tree, no venv/switch/migration."""

import hashlib
import json
import sys
import zipfile

import pytest

from omni.adapters.platform.wheel_prepare import prepare_wheel
from omni.adapters.platform.wheel_stage import stage_wheel
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
            archive.writestr(name, "content:" + name)
    return path, hashlib.sha256(path.read_bytes()).hexdigest()


def staged(tmp_path):
    home = tmp_path / "home"
    home.mkdir(exist_ok=True)
    path, sha256 = wheel(tmp_path)
    stage_wheel(home, path, sha256)
    return home, sha256


def test_prepare_unpacks_once_and_is_idempotent(tmp_path):
    home, sha256 = staged(tmp_path)
    first = prepare_wheel(home, sha256)
    assert first["written"] is True and first["manifest"]["files"] == len(REQUIRED)
    tree = home / "updates" / sha256[:16] / "tree"
    assert (tree / "omni/web_assets/ui/v2/index.html").read_text(encoding="utf-8") == (
        "content:omni/web_assets/ui/v2/index.html"
    )
    receipt = json.loads((tree.parent / "prepared.json").read_text(encoding="utf-8"))
    assert receipt["wheel_sha256"] == sha256 and receipt["tree"] == "tree"

    again = prepare_wheel(home, sha256)
    assert again["written"] is False and again["manifest"] == first["manifest"]


def test_prepare_requires_stage_and_unchanged_artifact(tmp_path):
    home = tmp_path / "home"
    home.mkdir()
    with pytest.raises(BusinessError) as absent:
        prepare_wheel(home, "1" * 64)
    assert absent.value.code == "prepare_not_staged"

    home, sha256 = staged(tmp_path)
    artifact = home / "updates" / sha256[:16] / "artifact.whl"
    with artifact.open("ab") as stream:
        stream.write(b"tampered")
    with pytest.raises(BusinessError) as changed:
        prepare_wheel(home, sha256)
    assert changed.value.code == "prepare_artifact_changed"


def test_cli_prepare_emits_json(tmp_path, monkeypatch, capsys):
    home, sha256 = staged(tmp_path)
    monkeypatch.setattr(sys, "argv", ["omni", "--home", str(home), "installation", "prepare", "--sha256", sha256])
    assert main() == 0
    payload = json.loads(capsys.readouterr().out)
    assert payload["format"] == "omni-prepared-update-1" and payload["written"] is True
