"""Private deployment artifacts and fingerprint fences; all program roots and publications are isolated."""

from __future__ import annotations

import copy
import datetime as dt
import hashlib
import json
import os
import platform
import sys
from pathlib import Path

import pytest
from test_v2_private_permissions import _windows_acl

from omni.adapters.platform import deployment_files as d
from omni.domain.contracts import BusinessError
from omni.domain.service_deployment import make_plan, validate_runtime


@pytest.fixture
def installation(tmp_path, monkeypatch):
    package = tmp_path / "program" / "omni"
    package.mkdir(parents=True)
    (package / "__init__.py").write_text("__version__='isolated'\n", encoding="utf-8")
    for layer in d.LAYERS:
        folder = package / layer
        folder.mkdir()
        (folder / "isolated.py").write_text("VALUE='isolated'\n", encoding="utf-8")
    assets = package / "web_assets"
    (assets / "ui/v2").mkdir(parents=True)
    (assets / "ui/v2/index.html").write_text("<html>isolated</html>", encoding="utf-8")
    prefix = tmp_path / "environment"
    prefix.mkdir()
    (prefix / "bin").mkdir()
    binary = prefix / "bin" / "python.exe"
    binary.write_bytes(b"isolated interpreter identity; never executed")
    config = prefix / "pyvenv.cfg"
    config.write_text("home = isolated-environment\n", encoding="utf-8")
    principal = {
        "name": "owner",
        "id": "S-1-5-21-1000" if sys.platform == "win32" else "1000",
        "home": str(tmp_path / "owner"),
    }
    monkeypatch.setattr(d, "_installation", lambda: (package, assets, False))
    monkeypatch.setattr(d, "_principal", lambda: dict(principal))
    monkeypatch.setattr(d.importlib.metadata, "distributions", lambda: [])

    def interpreter():
        content = d._read(binary, d.MAX_BYTES)
        cfg = d._read(config, d.MAX_BYTES)
        return {
            "python": str(binary),
            "python_realpath": str(binary),
            "python_prefix": str(prefix),
            "python_links": [],
            "python_sha256": hashlib.sha256(content).hexdigest(),
            "process_python": str(binary),
            "process_python_sha256": hashlib.sha256(content).hexdigest(),
            "python_version": sys.version.replace("\r\n", " ").replace("\n", " "),
            "venv_config": {"path": str(config), "sha256": hashlib.sha256(cfg).hexdigest(), "bytes": len(cfg)},
        }

    monkeypatch.setattr(d, "_python", interpreter)
    return d.CurrentDeploymentFiles(), package, assets, binary, config


def plan_for(files, tmp_path, *, runtime=None, bundle="bundle"):
    runtime = files.capture() if runtime is None else runtime
    target = {"win32": "windows-user", "darwin": "macos-user", "linux": "linux-user"}[runtime["platform"]]
    request = {
        "format": "omni-service-request-1",
        "target": target,
        "instance_id": str(__import__("uuid").uuid4()),
        "home": str(tmp_path / "instance"),
        "working_directory": str(tmp_path / "workspace"),
        "bundle_dir": str(tmp_path / bundle),
        "principal": runtime["principal"],
        "host": "127.0.0.1",
        "port": 8766,
        "external_url": None,
        "master_key_file": None,
        "stdout_logs": True,
    }
    return make_plan(
        request,
        runtime,
        str(__import__("uuid").uuid4()),
        dt.datetime.now(dt.timezone.utc).isoformat(timespec="microseconds"),
    )


def test_capture_is_bounded_complete_metadata_and_build_hash_matches_four_layer_algorithm(installation):
    files, package, _, _, config = installation
    runtime = files.capture()
    assert runtime == validate_runtime(runtime) and not runtime["editable"]
    assert runtime["platform"] == sys.platform and runtime["architecture"] == platform.machine()
    expected = hashlib.sha256()
    for path in sorted(p for layer in d.LAYERS for p in (package / layer).rglob("*.py")):
        expected.update(path.relative_to(package).as_posix().encode())
        expected.update(path.read_bytes())
    assert runtime["build_id"] == expected.hexdigest()
    assert len(runtime["files"]) == 6 and runtime["distributions"] == []
    assert runtime["venv_config"]["sha256"] == hashlib.sha256(config.read_bytes()).hexdigest()
    assert all(set(row) == {"path", "sha256", "bytes"} for row in runtime["files"])
    assert "isolated interpreter identity" not in json.dumps(runtime)


def test_interpreter_process_image_is_bound_separately_from_venv_launcher(installation, tmp_path, monkeypatch):
    files, _, _, binary, config = installation
    actual = tmp_path / "base-python.exe"
    actual.write_bytes(b"actual Python process image, never executed")
    monkeypatch.setattr(
        d,
        "_python",
        lambda: {**d.python_files(binary, config.parent, actual), "python_version": sys.version.replace("\n", " ")},
    )
    runtime = files.capture()
    assert runtime["process_python"] == str(actual) and runtime["python_realpath"] == str(binary)
    assert runtime["process_python_sha256"] != runtime["python_sha256"]
    deployment = plan_for(files, tmp_path)
    files.materialize(deployment, tmp_path / "bundle")
    assert files.verify(tmp_path / "bundle")["eligible"] is True
    actual.write_bytes(b"changed actual image with unchanged launcher")
    report = files.verify(tmp_path / "bundle")
    assert report["valid"] is False and "deployment_program_changed" in report["issues"]


@pytest.mark.parametrize("suffix", [".cs", ".ps1", ".swift", ".c", ".h"])
def test_native_helper_sources_change_the_captured_runtime_identity(installation, suffix):
    files, package, _, _, _ = installation
    before = files.capture()
    helper = package / "adapters" / ("isolated_native" + suffix)
    helper.write_text("// isolated native implementation\n", encoding="utf-8")
    changed = files.capture()
    assert before["build_id"] != changed["build_id"]
    assert "adapters/isolated_native" + suffix in {r["path"] for r in changed["files"]}
    helper.write_text("// changed native implementation\n", encoding="utf-8")
    assert files.capture()["build_id"] != changed["build_id"]


def test_private_runtime_and_plan_publication_refuse_overwrite_and_unsafe_roots(installation, tmp_path):
    files, package, _, _, _ = installation
    runtime, plan = files.capture(), plan_for(files, tmp_path)
    capture = files.write_capture(runtime, str(tmp_path / "runtime.json"))
    assert capture["captured"] and files.read(capture["path"]) == runtime
    written = files.write_plan(plan, str(tmp_path / "plan.json"))
    assert not written["installed"] and files.read(written["path"]) == plan
    for method, value, target in (
        (files.write_capture, runtime, tmp_path / "runtime.json"),
        (files.write_plan, plan, tmp_path / "plan.json"),
        (files.write_capture, runtime, package / "capture.json"),
        (files.write_plan, plan, tmp_path / "instance" / "plan.json"),
    ):
        with pytest.raises(BusinessError):
            method(value, target)
    assert not (tmp_path / "instance").exists()
    if os.name != "nt":
        assert not (tmp_path / "plan.json").stat().st_mode & 0o077


def test_materialize_is_not_installation_and_editable_preview_is_valid_but_ineligible(
    installation, tmp_path, monkeypatch
):
    files, package, assets, _, _ = installation
    monkeypatch.setattr(d, "_installation", lambda: (package, assets, True))
    plan = plan_for(files, tmp_path)
    result = files.materialize(plan, str(tmp_path / "bundle"))
    assert result["installed"] is False and not (tmp_path / "instance").exists()
    report = files.verify(str(tmp_path / "bundle"))
    assert report["valid"] and not report["eligible"] and report["plan"] == plan
    assert report["issues"] == ["editable_installation"]
    manifest = files.read(tmp_path / "bundle" / d.MANIFEST)
    assert manifest["plan_id"] == plan["id"] and manifest["plan_sha256"] == plan["sha256"]
    assert {item["path"] for item in manifest["files"]} == {p.name for p in (tmp_path / "bundle").iterdir()} - {
        d.MANIFEST
    }
    assert all(p.stat().st_nlink == 1 for p in (tmp_path / "bundle").iterdir())
    with pytest.raises(BusinessError):
        files.materialize(plan, tmp_path / "bundle")


def test_public_bundle_acl_blocks_execution_without_repairing_permissions_or_changing_content(installation, tmp_path):
    files, _, _, _, _ = installation
    plan = plan_for(files, tmp_path)
    root = tmp_path / "bundle"
    files.materialize(plan, root)
    assert files.verify(root)["eligible"]
    artifacts = sorted(root.iterdir())
    content = {path.name: path.read_bytes() for path in artifacts}
    for exposed in (root, *artifacts):
        if os.name == "nt":
            _windows_acl(exposed, public=True)
        else:
            exposed.chmod(0o755 if exposed.is_dir() else 0o644)
        report = files.verify(root)
        assert report["valid"] and not report["eligible"]
        assert report["issues"] == ["deployment_permissions_unsafe"]
        with pytest.raises(OSError):
            d.assert_private(exposed)
        assert {path.name: path.read_bytes() for path in artifacts} == content
        d.private_path(exposed, directory=exposed.is_dir())
        assert files.verify(root)["eligible"]


@pytest.mark.parametrize("kind", ["program", "assets", "python", "environment"])
def test_verify_detects_runtime_content_or_environment_drift(installation, tmp_path, kind):
    files, package, assets, binary, config = installation
    plan = plan_for(files, tmp_path)
    files.materialize(plan, tmp_path / "bundle")
    assert files.verify(tmp_path / "bundle")["eligible"]
    changed = {
        "program": package / "domain/isolated.py",
        "assets": assets / "ui/v2/index.html",
        "python": binary,
        "environment": config,
    }[kind]
    changed.write_bytes(changed.read_bytes() + b"\nchanged")
    result = files.verify(tmp_path / "bundle")
    assert not result["valid"] and not result["eligible"] and "deployment_program_changed" in result["issues"]


def test_verify_rejects_artifact_changes_extra_files_and_copied_bundle_locations(installation, tmp_path):
    files, _, _, _, _ = installation
    plan = plan_for(files, tmp_path)
    files.materialize(plan, tmp_path / "bundle")
    root = tmp_path / "bundle"
    startup = root / "startup.json"
    original = startup.read_bytes()
    startup.write_bytes(original + b" ")
    assert not files.verify(root)["valid"]
    startup.write_bytes(original)
    (root / "unexpected.txt").write_text("extra", encoding="utf-8")
    assert files.verify(root)["issues"] == ["deployment_extra_files"]
    (root / "unexpected.txt").unlink()
    moved = tmp_path / "moved"
    root.rename(moved)
    report = files.verify(moved)
    assert report["valid"] and not report["eligible"] and "deployment_location_mismatch" in report["issues"]


def test_strict_json_and_single_link_bundle_files_cannot_be_substituted(installation, tmp_path):
    files, _, _, _, _ = installation
    malformed = tmp_path / "invalid.json"
    for content in (b'{"a":1,"a":2}', b'{"a":NaN}', b'{"a":1e999}', b"[]", b"x" * (d.MAX_JSON + 1)):
        malformed.write_bytes(content)
        with pytest.raises(BusinessError):
            files.read(malformed)
    plan = plan_for(files, tmp_path)
    files.materialize(plan, tmp_path / "bundle")
    os.link(tmp_path / "bundle" / "startup.json", tmp_path / "linked-startup.json")
    assert not files.verify(tmp_path / "bundle")["valid"]


def test_program_cache_hardlinks_are_hashed_but_reported_links_and_startup_paths_are_refused(
    installation, tmp_path, monkeypatch
):
    files, package, _, _, _ = installation
    program = package / "domain/isolated.py"
    os.link(program, tmp_path / "cache-link.py")
    runtime = files.capture()
    proof = next(row for row in runtime["files"] if row["path"] == "domain/isolated.py")
    assert proof["sha256"] == hashlib.sha256(program.read_bytes()).hexdigest()
    original = Path.is_symlink
    # Inject a filesystem-reported link without requiring Windows developer-mode
    # privileges; both traversal and descriptor reads must honor the same guard.
    with monkeypatch.context() as patch:
        patch.setattr(Path, "is_symlink", lambda path: path == program or original(path))
        with pytest.raises(BusinessError) as linked:
            files.capture()
        assert linked.value.code == "deployment_path_unsafe"
    plan = plan_for(files, tmp_path, bundle="startup")
    monkeypatch.setattr(d, "_startup_locations", lambda: [tmp_path / "startup"])
    with pytest.raises(BusinessError) as startup:
        files.materialize(plan, tmp_path / "startup")
    assert startup.value.code == "deployment_destination_unsafe" and not (tmp_path / "startup").exists()


def test_partial_and_competing_publication_leave_no_fake_complete_bundle(installation, tmp_path, monkeypatch):
    files, _, _, _, _ = installation
    plan = plan_for(files, tmp_path)

    def interrupted(_source, _destination):
        raise OSError("isolated publication interruption")

    with monkeypatch.context() as patch:
        patch.setattr(d, "publish_directory", interrupted)
        with pytest.raises(OSError, match="isolated publication"):
            files.materialize(plan, tmp_path / "bundle")
    assert not (tmp_path / "bundle").exists()
    partials = list(tmp_path.glob(".*.deployment.partial"))
    assert len(partials) == 1 and not files.verify(partials[0])["eligible"]
    original_publish = d.publish_directory

    def competitor(source, destination):
        destination.mkdir()
        (destination / "other-owner.txt").write_text("preserved", encoding="utf-8")
        original_publish(source, destination)

    with monkeypatch.context() as patch:
        patch.setattr(d, "publish_directory", competitor)
        with pytest.raises(OSError):
            files.materialize(plan, tmp_path / "bundle")
    assert (tmp_path / "bundle" / "other-owner.txt").read_text(encoding="utf-8") == "preserved"
    assert not (tmp_path / "bundle" / d.MANIFEST).exists()


@pytest.mark.parametrize("target", ["macos-user", "linux-user"] if sys.platform == "win32" else ["windows-user"])
def test_other_platform_package_can_be_materialized_for_preview_without_execution(installation, tmp_path, target):
    files, _, _, _, _ = installation
    runtime = copy.deepcopy(files.capture())
    windows = target == "windows-user"
    runtime.update(
        platform="win32" if windows else ("darwin" if target.startswith("macos") else "linux"),
        python="C:\\Omni\\python.exe" if windows else "/opt/omni/python",
        python_realpath="C:\\Omni\\python.exe" if windows else "/opt/omni/python",
        process_python="C:\\Omni\\python.exe" if windows else "/opt/omni/python",
        python_prefix="C:\\Omni\\venv" if windows else "/opt/omni/venv",
        package_root="C:\\Omni\\package" if windows else "/opt/omni/package",
        venv_config=None,
        principal={
            "name": "owner",
            "id": "S-1-5-21-1000" if windows else "1000",
            "home": "C:\\Users\\owner" if windows else "/home/owner",
        },
    )
    plan = plan_for(files, tmp_path)
    request = {
        **plan["request"],
        "target": target,
        "principal": runtime["principal"],
        "home": "C:\\Omni\\instance" if windows else "/srv/omni/instance",
        "working_directory": "C:\\Omni\\workspace" if windows else "/srv/omni/workspace",
        "bundle_dir": "C:\\Omni\\bundle" if windows else "/srv/omni/bundle",
    }
    plan = make_plan(request, runtime, plan["id"], plan["created_at"])
    destination = tmp_path / "preview"
    assert files.materialize(plan, destination)["installed"] is False
    assert files.read(destination / "deployment.json") == plan
    report = files.verify(destination)
    assert not report["eligible"] and "deployment_platform_mismatch" in report["issues"]
