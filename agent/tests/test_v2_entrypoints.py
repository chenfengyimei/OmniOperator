"""Explicit candidate entrypoints share V2 serving, independent homes and no legacy bootstrap."""

from __future__ import annotations

import json
import subprocess
import sys
import tomllib
from pathlib import Path

import pytest

from omni.composition import cli
from omni.domain.contracts import BusinessError

ROOT = Path(__file__).resolve().parents[2]


@pytest.mark.parametrize(
    "options",
    [
        [],
        ["--port", "8912"],
        ["--host", "::1"],
        ["--host", "localhost"],
        ["--external-url", "https://fixture.invalid"],
        ["--master-key-file", "private-key-file"],
        ["--stdout-logs"],
        ["--host", "0.0.0.0", "--external-url", "https://fixture.invalid", "--port", "8913"],
    ],
)
def test_web_and_cli_serve_share_identical_arguments_and_instance_home(tmp_path, monkeypatch, options):
    calls = []
    monkeypatch.setattr(cli, "serve_instance", lambda home, **kwargs: calls.append((home, kwargs)))
    home = tmp_path / "independent-home"
    assert cli.main(["--home", str(home), "serve", *options]) == 0
    assert cli.web_main(["--home", str(home), *options]) == 0
    assert len(calls) == 2 and calls[0] == calls[1] and calls[0][0] == home
    assert not home.exists()
    if not options:
        assert calls[0][1] == {
            "host": "127.0.0.1",
            "port": 8766,
            "external_url": None,
            "master_key_file": None,
            "stdout_logs": False,
        }


@pytest.mark.parametrize("options", [["--port", "bad"], ["--home"], ["init"], ["--unknown"]])
def test_web_invalid_arguments_do_not_start_a_server(monkeypatch, options):
    calls = []
    monkeypatch.setattr(cli, "serve_instance", lambda *args, **kwargs: calls.append(True))
    with pytest.raises(SystemExit) as caught:
        cli.web_main(options)
    assert caught.value.code == 2 and not calls


def test_web_keeps_remote_binding_policy_and_standard_business_error(tmp_path, capsys):
    home = tmp_path / "home"
    assert cli.web_main(["--home", str(home), "--host", "0.0.0.0"]) == 1
    result = json.loads(capsys.readouterr().err)
    assert result["error"] == "https_origin_required" and not home.exists()


def test_explicit_browser_scope_is_closed_if_server_startup_fails(tmp_path, monkeypatch, capsys):
    from omni.adapters.platform.runtime import workbench

    calls = []

    class Browser:
        def __init__(self, home, host, port):
            assert (home, host, port) == (tmp_path, "127.0.0.1", 8766)

        def __enter__(self):
            calls.append("start")

        def __exit__(self, *args):
            calls.append("close")

    def serve(*args, **kwargs):
        raise BusinessError("fixture_startup_failure", "Fixture error")

    monkeypatch.setattr(workbench, "WorkbenchBrowser", Browser)
    monkeypatch.setattr(cli, "serve_instance", serve)
    assert cli.web_main(["--home", str(tmp_path), "--open-browser"]) == 1
    assert calls == ["start", "close"]
    assert json.loads(capsys.readouterr().err)["error"] == "fixture_startup_failure"


def test_explicit_cli_arguments_keep_init_doctor_and_legacy_home_rejection(tmp_path, capsys):
    home = tmp_path / "new"
    assert cli.main(["--home", str(home), "init"]) == 0
    assert json.loads(capsys.readouterr().out)["initialized"]
    assert cli.main(["--home", str(home), "doctor"]) == 0
    assert json.loads(capsys.readouterr().out)["integrity"] == "ok"
    legacy = tmp_path / "legacy"
    legacy.mkdir()
    (legacy / "memory.db").write_bytes(b"preserve V1 bytes")
    assert cli.main(["--home", str(legacy), "init"]) == 1
    assert json.loads(capsys.readouterr().err)["error"] == "filesystem_error"
    assert (legacy / "memory.db").read_bytes() == b"preserve V1 bytes"
    assert not (legacy / "data").exists()


def test_explicit_command_metadata_keeps_both_generations_available():
    scripts = tomllib.loads((ROOT / "agent/pyproject.toml").read_text(encoding="utf-8"))["project"]["scripts"]
    assert {name: scripts[name] for name in ("omni-v1", "omni-web-v1", "omni-v2", "omni-web-v2")} == {
        "omni-v1": "omni.channels.cli:main",
        "omni-web-v1": "omni.channels.web:main",
        "omni-v2": "omni.composition.cli:main",
        "omni-web-v2": "omni.composition.cli:web_main",
    }


def test_fresh_explicit_cli_process_initializes_without_importing_legacy_runtime(tmp_path):
    code = """import importlib,json,sys,tomllib
from pathlib import Path
scripts=tomllib.loads(Path(sys.argv[1]).read_text(encoding='utf-8'))['project']['scripts']
module,attribute=scripts['omni-v2'].split(':')
status=getattr(importlib.import_module(module),attribute)(['--home',sys.argv[2],'init'])
assert status==0
assert not any(name=='omni.bootstrap' or name.startswith(('omni.channels.','omni.memory.','omni.core.')) for name in sys.modules)
"""
    result = subprocess.run(
        [sys.executable, "-c", code, str(ROOT / "agent/pyproject.toml"), str(tmp_path / "home")],
        cwd=tmp_path,
        capture_output=True,
        text=True,
        timeout=180,
        check=False,
    )
    assert result.returncode == 0, result.stderr[-2000:]
    assert json.loads(result.stdout)["initialized"]
