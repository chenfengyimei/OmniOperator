"""Offline candidate environment: venv installs the staged wheel without network and proves import."""

from omni_test_support.v2.wheel_environment import (
    DIST as DIST,
)
from omni_test_support.v2.wheel_environment import (
    BusinessError as BusinessError,
)
from omni_test_support.v2.wheel_environment import (
    InstancePaths as InstancePaths,
)
from omni_test_support.v2.wheel_environment import (
    Path as Path,
)
from omni_test_support.v2.wheel_environment import (
    _open_and_close as _open_and_close,
)
from omni_test_support.v2.wheel_environment import (
    asyncio as asyncio,
)
from omni_test_support.v2.wheel_environment import (
    backup_candidate as backup_candidate,
)
from omni_test_support.v2.wheel_environment import (
    create_environment as create_environment,
)
from omni_test_support.v2.wheel_environment import (
    hashlib as hashlib,
)
from omni_test_support.v2.wheel_environment import (
    installable_wheel as installable_wheel,
)
from omni_test_support.v2.wheel_environment import (
    json as json,
)
from omni_test_support.v2.wheel_environment import (
    main as main,
)
from omni_test_support.v2.wheel_environment import (
    open_instance as open_instance,
)
from omni_test_support.v2.wheel_environment import (
    prepare_wheel as prepare_wheel,
)
from omni_test_support.v2.wheel_environment import (
    pytest as pytest,
)
from omni_test_support.v2.wheel_environment import (
    rollback_check as rollback_check,
)
from omni_test_support.v2.wheel_environment import (
    seal_installation as seal_installation,
)
from omni_test_support.v2.wheel_environment import (
    smoke_candidate as smoke_candidate,
)
from omni_test_support.v2.wheel_environment import (
    stage_wheel as stage_wheel,
)
from omni_test_support.v2.wheel_environment import (
    staged as staged,
)
from omni_test_support.v2.wheel_environment import (
    switch_candidate as switch_candidate,
)
from omni_test_support.v2.wheel_environment import (
    sys as sys,
)
from omni_test_support.v2.wheel_environment import (
    zipfile as zipfile,
)


def test_environment_installs_offline_and_is_idempotent(tmp_path):
    paths, sha256 = staged(tmp_path)
    first = create_environment(paths, sha256)
    assert first["written"] is True and first["installed"] is True
    assert first["mode"] == "system-site-packages"
    python = (
        paths.home
        / "updates"
        / sha256[:16]
        / "venv"
        / ("Scripts/python.exe" if sys.platform == "win32" else "bin/python")
    )
    assert python.is_file()
    record = json.loads((paths.home / "updates" / sha256[:16] / "environment.json").read_text(encoding="utf-8"))
    assert record["wheel_sha256"] == sha256
    assert str(paths.home / "updates" / sha256[:16] / "venv") in record["import_path"]
    again = create_environment(paths, sha256)
    assert again["written"] is False and again["installed"] is True


def test_environment_requires_staged_wheel_and_valid_receipt(tmp_path):
    paths = InstancePaths.resolve(tmp_path / "home")
    with pytest.raises(BusinessError) as missing:
        create_environment(paths, "a" * 64)
    assert missing.value.code == "environment_not_staged"

    paths, sha256 = staged(tmp_path)
    stage = paths.home / "updates" / sha256[:16]
    (stage / "staged.json").write_text(json.dumps({"format": "other", "wheel_sha256": sha256}), encoding="utf-8")
    with pytest.raises(BusinessError) as corrupt:
        create_environment(paths, sha256)
    assert corrupt.value.code == "environment_receipt_invalid"


def test_cli_environment_emits_json(tmp_path, monkeypatch, capsys):
    paths, sha256 = staged(tmp_path)
    monkeypatch.setattr(
        sys, "argv", ["omni", "--home", str(paths.home), "installation", "environment", "--sha256", sha256]
    )
    assert main() == 0
    payload = json.loads(capsys.readouterr().out)
    assert payload["format"] == "omni-environment-1" and payload["installed"] is True


def test_switch_prefers_the_installed_environment(tmp_path):
    paths, sha256 = staged(tmp_path)
    environment = create_environment(paths, sha256)
    prepare_wheel(paths.home, sha256)
    smoke_candidate(paths, sha256)
    backup_candidate(paths, sha256)
    rollback_check(paths, sha256)
    switched = switch_candidate(paths, sha256)
    assert switched["mode"] == "venv" and switched["python"] == environment["python"]
    relative = Path(environment["python"]).relative_to(paths.home)
    assert f'"%~dp0{relative}" -I -X utf8 ' in (paths.home / "omni.cmd").read_text(encoding="utf-8")
    assert environment["python"] in (paths.home / "omni").read_text(encoding="utf-8")
    launch = (paths.home / "runtime" / "launch.py").read_text(encoding="utf-8")
    assert environment["python"] not in launch and 'pointer.get("mode") != "venv"' in launch


def test_environment_import_proof_preserves_non_legacy_unicode_path(tmp_path):
    paths, sha256 = staged(tmp_path / "用户🙂")
    result = create_environment(paths, sha256)
    assert result["installed"] is True
    assert Path(result["import_path"]).is_relative_to(paths.home / "updates" / sha256[:16] / "venv")
    assert "🙂" in result["import_path"]
