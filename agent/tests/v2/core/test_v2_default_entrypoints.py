"""Default V2 commands and module entry preserve isolated homes, failure codes and explicit V1 fallback."""

import json
import subprocess
import sys
import tomllib

import pytest
from omni_test_support.paths import REPOSITORY_ROOT as REPOSITORY_ROOT

ROOT = REPOSITORY_ROOT


def test_default_metadata_selects_v2_while_explicit_v1_is_preserved():
    scripts = tomllib.loads((ROOT / "agent/pyproject.toml").read_text(encoding="utf-8"))["project"]["scripts"]
    assert scripts["omni"] == scripts["omni-v2"] == "omni.composition.cli:main"
    assert scripts["omni-web"] == scripts["omni-web-v2"] == "omni.composition.cli:web_main"
    assert scripts["omni-v1"] == "omni.channels.cli:main"
    assert scripts["omni-web-v1"] == "omni.channels.web:main"


@pytest.mark.parametrize("legacy", [False, True])
def test_default_module_initializes_a_new_home_and_propagates_legacy_rejection(tmp_path, legacy):
    home = tmp_path / "instance with spaces"
    if legacy:
        home.mkdir()
        (home / "memory.db").write_bytes(b"preserve V1 data")
    result = subprocess.run(
        [sys.executable, "-m", "omni", "--home", str(home), "init"],
        cwd=tmp_path,
        capture_output=True,
        text=True,
        timeout=180,
        check=False,
    )
    if legacy:
        assert result.returncode == 1 and json.loads(result.stderr)["error"] == "filesystem_error"
        assert (home / "memory.db").read_bytes() == b"preserve V1 data" and not (home / "data").exists()
    else:
        assert result.returncode == 0, result.stderr
        assert json.loads(result.stdout)["initialized"] and (home / "data/omni.db").is_file()


def test_default_command_process_does_not_import_a_v1_runtime(tmp_path):
    script = """
import importlib,sys,tomllib
from pathlib import Path
scripts=tomllib.loads(Path(sys.argv[1]).read_text(encoding='utf-8'))['project']['scripts']
module,attribute=scripts['omni'].split(':')
assert getattr(importlib.import_module(module),attribute)(['--home',sys.argv[2],'init'])==0
assert not any(n=='omni.bootstrap' or n.startswith(('omni.channels.','omni.memory.','omni.core.')) for n in sys.modules)
"""
    result = subprocess.run(
        [sys.executable, "-c", script, str(ROOT / "agent/pyproject.toml"), str(tmp_path / "home")],
        cwd=tmp_path,
        capture_output=True,
        text=True,
        timeout=180,
        check=False,
    )
    assert result.returncode == 0, result.stderr
    assert json.loads(result.stdout)["initialized"]
