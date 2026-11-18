"""The offline bundle producer publishes deterministic bytes without installing or importing input wheels."""

from __future__ import annotations

import hashlib
import json
import subprocess
import sys
from pathlib import Path

import pytest
from test_v2_isolated_environment import wheel

from omni.adapters.platform.releases.dependency_bundle import build_bundle, capture_bundle
from omni.domain.contracts import BusinessError

ROOT = Path(__file__).resolve().parents[2]


def source(tmp_path):
    root = tmp_path / "wheelhouse"
    root.mkdir()
    path = wheel(root, "omni_fixture_dependency", "omni_fixture_dependency", 'raise RuntimeError("must not import")\n')
    return root, path


def test_local_wheels_build_and_capture_with_a_deterministic_manifest(tmp_path):
    root, path = source(tmp_path)
    original = path.read_bytes()
    first = build_bundle(root, tmp_path / "first")
    second = build_bundle(root, tmp_path / "second")
    assert first["manifest_sha256"] == second["manifest_sha256"]
    assert first["wheels"] == 1 and first["dependency_closure_verified"] is False
    assert (tmp_path / "first" / path.name).read_bytes() == path.read_bytes() == original
    assert hashlib.sha256((tmp_path / "first/manifest.json").read_bytes()).hexdigest() == first["manifest_sha256"]
    stage = tmp_path / "stage"
    stage.mkdir()
    requirements = capture_bundle(tmp_path / "first", first["manifest_sha256"], stage)
    assert path.name in requirements.read_text(encoding="utf-8")


@pytest.mark.parametrize("case", ["existing", "nested", "source_itself"])
def test_bundle_build_refuses_overwrite_and_source_overlap(tmp_path, case):
    root, path = source(tmp_path)
    if case == "existing":
        target = tmp_path / "existing"
        target.mkdir()
        (target / "keep").write_bytes(b"keep")
    else:
        target = root / "nested" if case == "nested" else root
    original = path.read_bytes()
    with pytest.raises(BusinessError):
        build_bundle(root, target)
    assert path.read_bytes() == original
    if case == "existing":
        assert (target / "keep").read_bytes() == b"keep"
    elif case == "nested":
        assert not target.exists()


def test_bundle_build_rejects_non_wheel_input_without_executing_it(tmp_path):
    root, _ = source(tmp_path)
    (root / "unsafe.py").write_text('raise RuntimeError("must not execute")', encoding="utf-8")
    with pytest.raises(BusinessError):
        build_bundle(root, tmp_path / "output")
    assert not (tmp_path / "output").exists()


def test_dependency_bundle_build_command_emits_hash_and_keeps_the_input_wheel(tmp_path):
    root, path = source(tmp_path)
    output = tmp_path / "output"
    run = subprocess.run(
        [
            sys.executable,
            str(ROOT / "scripts/build_v2_dependencies.py"),
            "--wheels",
            str(root),
            "--output",
            str(output),
        ],
        capture_output=True,
        text=True,
        timeout=30,
        check=False,
    )
    assert run.returncode == 0, run.stderr
    record = json.loads(run.stdout)
    assert record["bundle"] == str(output) and record["wheels"] == 1
    assert record["manifest_sha256"] == hashlib.sha256((output / "manifest.json").read_bytes()).hexdigest()
    assert path.is_file()
