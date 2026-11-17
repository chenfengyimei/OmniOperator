"""The installed launcher owns interpreter handoff until its child actually completes."""

import json
import subprocess
import sys
from pathlib import Path

import pytest

from omni.adapters.platform.wheel_install import LAUNCHER


@pytest.mark.parametrize("exit_code", [0, 7])
def test_alternate_interpreter_propagates_output_and_exit_status(tmp_path, exit_code):
    venv = tmp_path / "alternate interpreter"
    subprocess.run([sys.executable, "-I", "-m", "venv", "--without-pip", str(venv)], check=True, timeout=30)
    python = venv / ("Scripts/python.exe" if sys.platform == "win32" else "bin/python")
    _launch(tmp_path, str(python), exit_code)


def test_selected_interpreter_runs_without_another_handoff(tmp_path):
    _launch(tmp_path, sys.executable, 9)


def _launch(tmp_path, python, exit_code):
    home = tmp_path / "instance with spaces"
    runtime, tree = home / "runtime", home / "tree"
    runtime.mkdir(parents=True)
    cli = tree / "omni/composition"
    cli.mkdir(parents=True)
    (tree / "omni/__init__.py").write_text("", encoding="utf-8")
    (cli / "__init__.py").write_text("", encoding="utf-8")
    (cli / "cli.py").write_text(
        "import json,sys,time\n"
        "def main():\n"
        " time.sleep(0.25)\n"
        " print(json.dumps({'python':sys.executable,'arguments':sys.argv[1:]}),flush=True)\n"
        f" return {exit_code}\n",
        encoding="utf-8",
    )
    (runtime / "current.json").write_text(
        json.dumps({"format": "omni-runtime-1", "mode": "tree", "python": python, "tree": str(tree)}),
        encoding="utf-8",
    )
    launch = runtime / "launch.py"
    launch.write_text(LAUNCHER, encoding="utf-8")
    result = subprocess.run(
        [sys.executable, str(launch), "doctor", "argument with spaces"],
        cwd=tmp_path,
        capture_output=True,
        text=True,
        timeout=30,
        check=False,
    )
    assert result.returncode == exit_code, result.stderr
    output = json.loads(result.stdout)
    assert Path(output["python"]) == Path(python)
    assert output["arguments"] == ["--home", str(home), "doctor", "argument with spaces"]
