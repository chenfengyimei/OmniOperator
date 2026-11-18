"""Pinned local wheels create an independent, runnable candidate environment and retain recovery boundaries."""

from __future__ import annotations

import hashlib
import json
import platform
import shutil
import subprocess
import sys
import zipfile
from pathlib import Path

import pytest
from test_v2_wheel_environment import staged

from omni.adapters.platform import dependency_bundle as bundle
from omni.adapters.platform import wheel_environment as environment
from omni.adapters.platform.runtime.paths import InstanceLock
from omni.adapters.platform.runtime.permissions import private_path
from omni.adapters.platform.wheel_backup import backup_candidate
from omni.adapters.platform.wheel_install import switch_candidate
from omni.adapters.platform.wheel_prepare import prepare_wheel
from omni.adapters.platform.wheel_rollback import rollback_check
from omni.adapters.platform.wheel_smoke import smoke_candidate
from omni.adapters.platform.wheel_stage import stage_wheel
from omni.adapters.platform.wheel_uninstall import uninstall_candidate
from omni.composition.cli import main
from omni.domain.contracts import BusinessError


def wheel(folder, name, module, body, *, requires=None, console=False):
    path = folder / f"{name}-1.0.0-py3-none-any.whl"
    dist = name + "-1.0.0.dist-info"
    files = {
        f"{module}/__init__.py": body,
        f"{dist}/METADATA": f"Metadata-Version: 2.1\nName: {name}\nVersion: 1.0.0\n"
        + (f"Requires-Dist: {requires}\n" if requires else ""),
        f"{dist}/WHEEL": "Wheel-Version: 1.0\nRoot-Is-Purelib: true\nTag: py3-none-any\n",
    }
    if name == "omnioperator":
        files.update({"omni/web_assets/index.html": "x", "omni/web_assets/ui/v2/index.html": "x"})
    if console:
        files.update(
            {
                "omni/fixture_console.py": "import json,sys\nfrom omni_fixture_dependency import VALUE\ndef main():\n print(json.dumps({'dependency':VALUE,'prefix':sys.prefix})); return 0\n",
                "omni/composition/__init__.py": "",
                "omni/composition/cli.py": "from omni.fixture_console import main\nraise SystemExit(main())\n",
                f"{dist}/entry_points.txt": "[console_scripts]\nomni-v2 = omni.fixture_console:main\n",
            }
        )
    files[f"{dist}/RECORD"] = "".join(f"{name},,\n" for name in files) + f"{dist}/RECORD,,\n"
    with zipfile.ZipFile(path, "w") as archive:
        for name, content in files.items():
            archive.writestr(name, content)
    return path


def manifest(root, wheel_paths=(), **changes):
    value = {
        "format": bundle.FORMAT,
        "platform": sys.platform,
        "python": f"{sys.version_info.major}.{sys.version_info.minor}",
        "machine": platform.machine().casefold(),
        "wheels": [
            {"file": path.name, "bytes": path.stat().st_size, "sha256": hashlib.sha256(path.read_bytes()).hexdigest()}
            for path in wheel_paths
        ],
        **changes,
    }
    raw = json.dumps(value).encode()
    (root / "manifest.json").write_bytes(raw)
    return hashlib.sha256(raw).hexdigest()


def dependency(tmp_path):
    root = tmp_path / "bundle with space"
    root.mkdir()
    path = wheel(root, "omni_fixture_dependency", "omni_fixture_dependency", 'VALUE="pinned local value"\n')
    return root, path, manifest(root, [path])


@pytest.mark.parametrize(
    "field,value",
    [
        ("format", "other"),
        ("platform", "other"),
        ("python", "0.0"),
        ("machine", "other"),
        ("wheels", {}),
        ("extra", True),
    ],
)
def test_bundle_rejects_wrong_contract_or_platform_before_capture(tmp_path, field, value):
    root, path, _ = dependency(tmp_path)
    digest = manifest(root, [path], **{field: value})
    stage = tmp_path / "stage"
    stage.mkdir()
    with pytest.raises(BusinessError) as caught:
        bundle.capture_bundle(root, digest, stage)
    assert caught.value.code == "dependency_bundle_invalid" and not list(stage.iterdir())


@pytest.mark.parametrize("change", ["path", "duplicate", "size", "digest", "missing", "hash_mismatch"])
def test_bundle_copies_only_complete_hash_verified_flat_wheels(tmp_path, change):
    root, path, digest = dependency(tmp_path)
    value = json.loads((root / "manifest.json").read_bytes())
    if change == "path":
        value["wheels"][0]["file"] = "../escape.whl"
    elif change == "duplicate":
        value["wheels"].append(dict(value["wheels"][0]))
    elif change == "size":
        value["wheels"][0]["bytes"] = True
    elif change == "digest":
        value["wheels"][0]["sha256"] = "bad"
    elif change == "missing":
        path.unlink()
    else:
        path.write_bytes(b"changed")
    raw = json.dumps(value).encode()
    (root / "manifest.json").write_bytes(raw)
    digest = hashlib.sha256(raw).hexdigest()
    stage = tmp_path / "stage"
    stage.mkdir()
    with pytest.raises(BusinessError) as caught:
        bundle.capture_bundle(root, digest, stage)
    assert caught.value.code == "dependency_bundle_invalid" and not list(stage.iterdir())


def test_captured_bundle_is_local_and_revalidates_cached_bytes_and_requirements(tmp_path):
    root, _, digest = dependency(tmp_path)
    stage = tmp_path / "stage with space"
    stage.mkdir()
    requirements = bundle.capture_bundle(root, digest, stage)
    text = requirements.read_text(encoding="utf-8")
    assert text.startswith("file:///") and "%20" in text and "--hash=sha256:" in text
    assert "https:" not in text and str(root) not in text
    shutil.rmtree(root)
    assert bundle.capture_bundle(root, digest, stage) == requirements
    requirements.write_text("https://example.invalid/remote.whl", encoding="utf-8")
    with pytest.raises(BusinessError):
        bundle.capture_bundle(root, digest, stage)


@pytest.mark.parametrize(
    "options",
    [
        {"dependency_sha256": "a" * 64},
        {"dependency_bundle": Path("missing")},
        {"dependency_bundle": Path("missing"), "dependency_sha256": "bad"},
    ],
)
def test_environment_requires_paired_dependency_inputs_without_creating_a_home(tmp_path, options):
    from omni.adapters.platform.runtime.paths import InstancePaths

    paths = InstancePaths.resolve(tmp_path / "home")
    with pytest.raises(BusinessError) as caught:
        environment.create_environment(paths, "b" * 64, **options)
    assert caught.value.code == "environment_invalid" and not paths.home.exists()


def test_running_instance_blocks_environment_creation_before_any_subprocess(tmp_path, monkeypatch):
    paths, digest = staged(tmp_path)
    calls = []
    monkeypatch.setattr(environment, "_run", lambda *args: calls.append(args))
    lock = InstanceLock(paths)
    lock.acquire()
    try:
        with pytest.raises(BusinessError) as caught:
            environment.create_environment(paths, digest)
        assert caught.value.code == "instance_busy" and not calls
    finally:
        lock.close()


@pytest.mark.parametrize("target", ["artifact", "installable"])
def test_environment_rechecks_both_staged_and_cached_installable_wheel_before_execution(tmp_path, monkeypatch, target):
    paths, digest = staged(tmp_path)
    stage = paths.home / "updates" / digest[:16]
    if target == "artifact":
        path = stage / "artifact.whl"
    else:
        (stage / "install").mkdir()
        path = stage / "install/omnioperator-0.1.0-py3-none-any.whl"
    path.write_bytes(b"tampered wheel")
    calls = []
    monkeypatch.setattr(environment, "_run", lambda *args: calls.append(args))
    with pytest.raises(BusinessError) as caught:
        environment.create_environment(paths, digest)
    assert caught.value.code == "artifact_checksum" and not calls and not (stage / "venv").exists()


def test_failed_dependency_install_cleans_only_its_environment_and_does_not_publish_receipt(tmp_path, monkeypatch):
    paths, digest = staged(tmp_path)
    root, _, dependency_sha = dependency(tmp_path)
    stage = paths.home / "updates" / digest[:16]
    calls = []

    def run(command, cwd):
        calls.append(command)
        if "venv" in command:
            python = environment._python(stage / "venv")
            python.parent.mkdir(parents=True, exist_ok=True)
            python.write_bytes(b"owned interpreter placeholder")
            (stage / "venv/pyvenv.cfg").write_text("include-system-site-packages = false\n", encoding="utf-8")
            return subprocess.CompletedProcess(command, 0, "", "")
        assert "--no-index" in command and "--require-hashes" in command
        return subprocess.CompletedProcess(command, 1, "", "PRIVATE_INSTALL_DETAIL")

    monkeypatch.setattr(environment, "_run", run)
    with pytest.raises(BusinessError) as caught:
        environment.create_environment(paths, digest, dependency_bundle=root, dependency_sha256=dependency_sha)
    assert caught.value.code == "environment_dependencies_failed" and "PRIVATE_INSTALL" not in str(caught.value)
    assert len(calls) == 2 and "--system-site-packages" not in calls[0]
    assert not (stage / "venv").exists() and not (stage / "environment.json").exists()
    assert (stage / "dependencies/manifest.json").is_file() and paths.database.is_file()


@pytest.mark.parametrize(
    "field,value",
    [
        ("python", sys.executable),
        ("venv", "elsewhere"),
        ("mode", []),
        ("stable_interpreter_path", False),
        ("wheel_sha256", "f" * 64),
        ("installed", False),
        ("format", "other"),
    ],
)
def test_bad_environment_receipt_cannot_fall_back_to_a_host_interpreter(tmp_path, field, value):
    from omni.adapters.platform.wheel_install import _environment

    stage = tmp_path / "stage"
    venv = stage / "venv"
    python = environment._python(venv)
    python.parent.mkdir(parents=True)
    python.write_bytes(b"owned placeholder, never executed")
    digest = "a" * 64
    record = {
        "format": environment.FORMAT,
        "wheel_sha256": digest,
        "installed": True,
        "stable_interpreter_path": True,
        "mode": "system-site-packages",
        "venv": str(venv),
        "python": str(python),
        field: value,
    }
    receipt = stage / "environment.json"
    receipt.write_text(json.dumps(record), encoding="utf-8")
    private_path(receipt)
    with pytest.raises(BusinessError) as caught:
        _environment(stage, digest)
    assert caught.value.code == "environment_receipt_invalid"


def test_real_isolated_venv_console_smoke_switch_and_uninstall_keep_data_and_backup(tmp_path):
    paths, _ = staged(tmp_path)
    root, _, dependency_sha = dependency(tmp_path)
    app = wheel(
        tmp_path,
        "omnioperator",
        "omni",
        "from omni_fixture_dependency import VALUE\n",
        requires="omni-fixture-dependency==1.0.0",
        console=True,
    )
    digest = hashlib.sha256(app.read_bytes()).hexdigest()
    stage_wheel(paths.home, app, digest)
    result = environment.create_environment(paths, digest, dependency_bundle=root, dependency_sha256=dependency_sha)
    assert result["mode"] == "isolated-wheelhouse" and result["stable_interpreter_path"] is True
    stage = paths.home / "updates" / digest[:16]
    assert "include-system-site-packages = false" in (stage / "venv/pyvenv.cfg").read_text().lower()
    executable = stage / "venv" / ("Scripts/omni-v2.exe" if sys.platform == "win32" else "bin/omni-v2")
    console = subprocess.run([str(executable)], capture_output=True, text=True, timeout=30, check=False)
    assert console.returncode == 0, console.stderr
    assert json.loads(console.stdout) == {"dependency": "pinned local value", "prefix": str(stage / "venv")}
    assert (
        environment.create_environment(paths, digest, dependency_bundle=root, dependency_sha256=dependency_sha)[
            "written"
        ]
        is False
    )
    prepare_wheel(paths.home, digest)
    smoke = smoke_candidate(paths, digest)
    assert smoke["mode"] == "venv" and smoke["executable"] == result["python"]
    backup = backup_candidate(paths, digest)
    assert rollback_check(paths, digest)["verified"]
    proof = stage / "smoke.json"
    saved = proof.read_bytes()
    proof.write_text(json.dumps({**json.loads(saved), "environment_sha256": "0" * 64}), encoding="utf-8")
    with pytest.raises(BusinessError) as stale:
        switch_candidate(paths, digest)
    assert stale.value.code == "switch_environment_unverified" and not (paths.home / "runtime/current.json").exists()
    proof.write_bytes(saved)
    assert switch_candidate(paths, digest)["python"] == result["python"]
    launch = subprocess.run(
        [sys.executable, str(paths.home / "runtime/launch.py")], capture_output=True, text=True, timeout=30, check=False
    )
    assert launch.returncode == 0 and json.loads(launch.stdout)["dependency"] == "pinned local value"
    before = paths.database.read_bytes()
    removed = uninstall_candidate(paths, digest)
    assert removed["data_kept"] and not stage.exists() and paths.database.read_bytes() == before
    assert Path(backup["destination"]).is_dir()


def test_missing_dependency_fails_pip_check_instead_of_inheriting_the_development_environment(tmp_path):
    paths, _ = staged(tmp_path)
    root = tmp_path / "empty bundle"
    root.mkdir()
    dependency_sha = manifest(root)
    app = wheel(tmp_path, "omnioperator", "omni", "", requires="sqlalchemy==2.0.0")
    digest = hashlib.sha256(app.read_bytes()).hexdigest()
    stage_wheel(paths.home, app, digest)
    with pytest.raises(BusinessError) as caught:
        environment.create_environment(paths, digest, dependency_bundle=root, dependency_sha256=dependency_sha)
    assert caught.value.code == "environment_dependencies_failed"
    stage = paths.home / "updates" / digest[:16]
    assert not (stage / "venv").exists() and not (stage / "environment.json").exists()


def test_cli_dependency_options_are_not_silently_ignored_for_other_operations(tmp_path, capsys):
    assert main(["--home", str(tmp_path), "installation", "verify", "--dependency-sha256", "a" * 64]) == 1
    assert json.loads(capsys.readouterr().err)["error"] == "environment_invalid"


def test_replayed_switch_preserves_the_actual_previous_candidate_for_rollback(tmp_path):
    from test_v2_wheel_install import _second_candidate, candidate

    from omni.adapters.platform.wheel_install import rollback_candidate

    paths, first, _ = candidate(tmp_path)
    switch_candidate(paths, first)
    second = _second_candidate(tmp_path, paths, "updated")
    previous = paths.home / "runtime/previous.json"
    saved = previous.read_bytes()
    assert json.loads(saved)["wheel_sha256"] == first
    assert switch_candidate(paths, second)["written"] is False
    assert previous.read_bytes() == saved
    assert rollback_candidate(paths)["to"] == first


@pytest.mark.parametrize("operation", ["switch", "rollback"])
def test_runtime_pointer_publication_keeps_instance_lock_until_complete(tmp_path, monkeypatch, operation):
    from test_v2_wheel_install import _second_candidate, candidate

    from omni.adapters.platform import wheel_install

    paths, first, _ = candidate(tmp_path)
    if operation == "rollback":
        switch_candidate(paths, first)
        _second_candidate(tmp_path, paths, "for-rollback")
    original, observed = wheel_install.durable_json, []

    def write(path, value):
        if path.parent == paths.home / "runtime":
            other = InstanceLock(paths)
            try:
                with pytest.raises(BusinessError) as caught:
                    other.acquire()
                assert caught.value.code == "instance_busy"
            finally:
                other.close()
            observed.append(path.name)
        return original(path, value)

    monkeypatch.setattr(wheel_install, "durable_json", write)
    if operation == "switch":
        switch_candidate(paths, first)
    else:
        wheel_install.rollback_candidate(paths)
    assert "current.json" in observed
