"""Fresh-install bootstrap rejects existing data and starts from pinned wheels without host dependencies."""

import hashlib
import json
import subprocess
import sys
import zipfile
from pathlib import Path

import pytest
from test_v2_isolated_environment import dependency, wheel

from omni.adapters.platform.candidate_installer import install_candidate
from omni.domain.contracts import BusinessError

ROOT = Path(__file__).resolve().parents[2]


@pytest.mark.parametrize(
    "conflict", ["existing_home", "existing_manager", "nested_manager", "nested_home", "dot_segments", "bad_hash"]
)
def test_invalid_or_overlapping_installation_inputs_never_run_an_installer(tmp_path, conflict, monkeypatch):
    from omni.adapters.platform import candidate_installer

    bundle, _, bundle_sha = dependency(tmp_path)
    app = wheel(tmp_path, "omnioperator", "omni", "")
    digest = hashlib.sha256(app.read_bytes()).hexdigest()
    manager, home = tmp_path / "manager", tmp_path / "home"
    if conflict == "existing_home":
        home.mkdir()
        (home / "original").write_bytes(b"preserve")
    elif conflict == "existing_manager":
        manager.mkdir()
    elif conflict == "nested_manager":
        manager = home / "manager"
    elif conflict == "nested_home":
        home = manager / "home"
    elif conflict == "dot_segments":
        manager = tmp_path / "unused/../home/manager"
    else:
        digest = "f" * 64
    calls = []
    monkeypatch.setattr(candidate_installer, "_run", lambda *args: calls.append(args))
    with pytest.raises(BusinessError):
        install_candidate(app, digest, bundle, bundle_sha, manager, home)
    assert not calls and not (manager / "bootstrap.json").exists()
    if conflict == "existing_home":
        assert (home / "original").read_bytes() == b"preserve"


def test_real_manager_is_isolated_and_a_failed_init_does_not_publish_success(tmp_path):
    bundle, _, bundle_sha = dependency(tmp_path)
    app = wheel(
        tmp_path,
        "omnioperator",
        "omni",
        "from omni_fixture_dependency import VALUE\n",
        requires="omni-fixture-dependency==1.0.0",
    )
    digest = hashlib.sha256(app.read_bytes()).hexdigest()
    manager, home = tmp_path / "manager with spaces", tmp_path / "new home"
    with pytest.raises(BusinessError) as caught:
        install_candidate(app, digest, bundle, bundle_sha, manager, home)
    assert caught.value.code == "candidate_install_failed"
    python = manager / "venv" / ("Scripts/python.exe" if sys.platform == "win32" else "bin/python")
    code = (
        "import omni,sys,site,json; from pathlib import Path; root=Path(sys.prefix).resolve(); "
        "assert site.ENABLE_USER_SITE is False and sys.prefix!=sys.base_prefix; "
        "assert all(Path(p).resolve().is_relative_to(root) for p in sys.path if Path(p).name=='site-packages'); "
        "print(json.dumps({'value':omni.VALUE,'module':omni.__file__}))"
    )
    result = subprocess.run([str(python), "-I", "-c", code], capture_output=True, text=True, timeout=30, check=False)
    assert result.returncode == 0, result.stderr
    data = json.loads(result.stdout)
    assert data["value"] == "pinned local value" and Path(data["module"]).is_relative_to(manager)
    assert not (manager / "bootstrap.json").exists() and not home.exists()


def test_standalone_installer_checks_hash_before_any_application_import(tmp_path):
    app = tmp_path / "omnioperator-1.0.0-py3-none-any.whl"
    marker = tmp_path / "executed"
    with zipfile.ZipFile(app, "w") as archive:
        archive.writestr("omni/__init__.py", f"from pathlib import Path\nPath({str(marker)!r}).touch()\n")
    command = [
        sys.executable,
        "-I",
        str(ROOT / "agent/scripts/install_v2.py"),
        "--wheel",
        str(app),
        "--sha256",
        "f" * 64,
        "--dependency-bundle",
        str(tmp_path / "missing"),
        "--dependency-sha256",
        "a" * 64,
        "--manager",
        str(tmp_path / "manager"),
        "--home",
        str(tmp_path / "home"),
    ]
    result = subprocess.run(command, cwd=tmp_path, capture_output=True, text=True, timeout=30, check=False)
    assert result.returncode == 1 and json.loads(result.stderr)["error"] == "candidate_install_failed"
    assert not marker.exists() and not (tmp_path / "manager").exists() and not (tmp_path / "home").exists()
