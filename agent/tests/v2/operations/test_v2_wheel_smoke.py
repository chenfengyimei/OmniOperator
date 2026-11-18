"""Candidate smoke test: child interpreter imports the prepared tree; no install or switch."""

import json
import sys

import pytest

from omni.adapters.platform.releases.wheel_smoke import smoke_candidate
from omni.adapters.platform.runtime.paths import InstancePaths
from omni.composition.cli import main
from omni.domain.contracts import BusinessError


def prepared_tree(tmp_path, sha256="a" * 64):
    home = tmp_path / "home"
    tree = home / "updates" / sha256[:16] / "tree"
    (tree / "omni" / "web_assets" / "ui" / "v2").mkdir(parents=True)
    (tree / "omni" / "__init__.py").write_text("", encoding="utf-8")
    (tree / "omni" / "web_assets" / "ui" / "v2" / "index.html").write_text("x", encoding="utf-8")
    (tree.parent / "prepared.json").write_text(
        json.dumps(
            {"format": "omni-prepared-update-1", "wheel_sha256": sha256, "files": 1, "bytes": 0, "tree": "tree"}
        ),
        encoding="utf-8",
    )
    return InstancePaths.resolve(home), sha256


def test_smoke_imports_candidate_once(tmp_path):
    paths, sha256 = prepared_tree(tmp_path)
    first = smoke_candidate(paths, sha256)
    assert first["written"] is True and first["verified"] is True and first["wheel_sha256"] == sha256
    assert (paths.home / "updates" / sha256[:16] / "smoke.json").is_file()
    again = smoke_candidate(paths, sha256)
    assert again["written"] is False and again["verified"] is True


def test_smoke_requires_prepared_and_runnable_tree(tmp_path):
    home = tmp_path / "home"
    paths = InstancePaths.resolve(home)
    with pytest.raises(BusinessError) as absent:
        smoke_candidate(paths, "b" * 64)
    assert absent.value.code == "smoke_not_prepared"

    paths, sha256 = prepared_tree(tmp_path, "c" * 64)
    (paths.home / "updates" / sha256[:16] / "tree" / "omni" / "__init__.py").unlink()
    with pytest.raises(BusinessError) as broken:
        smoke_candidate(paths, sha256)
    assert broken.value.code == "smoke_failed"


def test_cli_smoke_emits_json(tmp_path, monkeypatch, capsys):
    paths, sha256 = prepared_tree(tmp_path, "d" * 64)
    monkeypatch.setattr(sys, "argv", ["omni", "--home", str(paths.home), "installation", "smoke", "--sha256", sha256])
    assert main() == 0
    payload = json.loads(capsys.readouterr().out)
    assert payload["format"] == "omni-candidate-smoke-1" and payload["verified"] is True
