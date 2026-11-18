"""Execute generated launchers in isolated temporary paths, retaining literal characters, argv and exit status."""

import json
import os
import shlex
import shutil
import subprocess
import sys
from pathlib import Path

import pytest

from omni.adapters.platform.wheel_install import _wrapper


def shell():
    if os.name != "nt":
        return "/bin/sh"
    git = shutil.which("git")
    assert git is not None, "These POSIX launcher protocol checks require the existing Git shell on Windows"
    resolved = Path(git).resolve()
    for ancestor in resolved.parents:
        candidate = ancestor / "bin/sh.exe"
        if candidate.is_file():
            return str(candidate)
    raise AssertionError("Git for Windows sh.exe not found above " + str(resolved))


def fixture(home):
    runtime = home / "runtime"
    runtime.mkdir(parents=True)
    (runtime / "launch.py").write_text(
        "import json,sys\nprint(json.dumps({'args':sys.argv[1:],'isolated':sys.flags.isolated}))\nraise SystemExit(37)\n",
        encoding="utf-8",
    )
    poison = home / "poison"
    poison.mkdir()
    marker = home / "startup-hook-ran"
    (poison / "sitecustomize.py").write_text(
        f"from pathlib import Path\nPath({str(marker)!r}).touch()\n", encoding="utf-8"
    )
    return {**os.environ, "PYTHONPATH": str(poison), "PYTHONUTF8": "1", "OMNI_WRAPPER_EXPAND": "wrong-location"}, marker


@pytest.mark.parametrize(
    "suffix",
    [
        "with spaces",
        "single'quote",
        "dollar$OMNI_WRAPPER_EXPAND",
        "command$(false)`false`",
        "用户 !literal! %OMNI_WRAPPER_EXPAND%",
    ],
)
def test_posix_launcher_preserves_literal_home_and_arguments_without_python_startup_hooks(tmp_path, suffix):
    home = tmp_path / suffix
    environment, marker = fixture(home)
    _wrapper(home, sys.executable)
    arguments = [str(home / "omni"), "one space", "你好🙂", "literal'$()!%"]
    # Feed POSIX quoting through the shell itself. Git's Windows argv decoder
    # otherwise consumes a literal single quote before the script starts.
    source = "exec /bin/sh " + " ".join(shlex.quote(value) for value in arguments) + "\n"
    result = subprocess.run(
        [shell()],
        input=source.encode("utf-8"),
        cwd=tmp_path,
        env=environment,
        capture_output=True,
        timeout=30,
        check=False,
    )
    assert result.returncode == 37, result.stderr
    assert json.loads(result.stdout) == {"args": ["one space", "你好🙂", "literal'$()!%"], "isolated": 1}
    assert not marker.exists()


def test_windows_launcher_keeps_unicode_percent_and_bang_home_under_inherited_delayed_expansion(tmp_path):
    if os.name != "nt":
        # The same contract is executed by native Windows CI; assert the batch
        # bytes on POSIX without falsely claiming native cmd execution there.
        home = tmp_path / "home"
        home.mkdir()
        _wrapper(home, sys.executable)
        assert b"DisableDelayedExpansion\r\n" in (home / "omni.cmd").read_bytes()
        return
    home = tmp_path / "用户🙂 %OMNI_WRAPPER_EXPAND% !literal!"
    environment, marker = fixture(home)
    venv = home / "updates" / ("a" * 16) / "venv"
    created = subprocess.run(
        [sys.executable, "-I", "-m", "venv", "--without-pip", "--copies", str(venv)],
        capture_output=True,
        timeout=30,
        check=False,
    )
    assert created.returncode == 0
    _wrapper(home, str(venv / "Scripts/python.exe"))
    batch = (home / "omni.cmd").read_bytes()
    assert b'"%~dp0updates\\aaaaaaaaaaaaaaaa\\venv\\Scripts\\python.exe" -I -X utf8 ' in batch
    assert b"\r\r\n" not in batch
    comspec = Path(os.environ["SystemRoot"]) / "System32/cmd.exe"
    result = subprocess.run(
        [str(comspec), "/d", "/v:on", "/c", "omni.cmd", "one space"],
        cwd=home,
        env=environment,
        capture_output=True,
        encoding="utf-8",
        timeout=30,
        check=False,
    )
    assert result.returncode == 37, result.stderr
    assert json.loads(result.stdout) == {"args": ["one space"], "isolated": 1}
    assert not marker.exists()
