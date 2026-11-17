"""Exercise install-check contracts with isolated peers and real bounded child pipes; never installs into a user home."""

import asyncio
import hashlib
import importlib.util
import json
import shutil
import subprocess
import sys
import zipfile
from pathlib import Path
from types import SimpleNamespace

import pytest

SCRIPT = Path(__file__).resolve().parents[1] / "scripts/check_v2_installation.py"
spec = importlib.util.spec_from_file_location("installation_check", SCRIPT)
assert spec is not None and spec.loader is not None
check = importlib.util.module_from_spec(spec)
spec.loader.exec_module(check)


def inputs(tmp_path):
    wheel = tmp_path / "omnioperator-1.0-py3-none-any.whl"
    wheel.write_bytes(b"pinned fixture wheel")
    bundle = tmp_path / "bundle"
    bundle.mkdir()
    (bundle / "manifest.json").write_bytes(b"{}")
    return (
        wheel,
        check.digest(wheel),
        bundle,
        check.digest(bundle / "manifest.json"),
        tmp_path / "result",
        Path(sys.executable),
        SCRIPT,
    )


def peer(fault=None):
    class Peer(check.Commands):
        async def run(self, phase, arguments, cwd, env):
            self.records.append({"phase": phase})
            assert env["PIP_NO_INDEX"] == "1"
            assert "--serve" not in arguments and "--no-index" not in arguments
            assert "-I" in arguments or phase == "launcher"
            if phase == "base":
                return {"omni_absent": fault != "base", "platform": sys.platform}
            if phase == "install":
                self.home = Path(arguments[arguments.index("--home") + 1])
                self.manager = Path(arguments[arguments.index("--manager") + 1])
                self.sha = arguments[arguments.index("--sha256") + 1]
                self.stage = self.home / "updates" / self.sha[:16]
                self.python = self.stage / "venv" / ("Scripts/python.exe" if sys.platform == "win32" else "bin/python")
                self.python.parent.mkdir(parents=True)
                self.python.touch()
                self.backup = self.home.with_name(self.home.name + "-backup-" + self.sha[:16])
                self.backup.mkdir()
                (self.backup / "manifest.json").write_bytes(b"retained backup manifest")
                (self.home / "data").mkdir()
                (self.home / "data/omni.db").write_bytes(b"retained isolated database")
                (self.home / "runtime").mkdir()
                (self.home / "runtime/current.json").write_text(
                    json.dumps(
                        {
                            "mode": "venv",
                            "python": str(self.python) if fault != "pointer" else sys.executable,
                            "wheel_sha256": self.sha,
                        }
                    )
                )
                for name in ("omni", "omni.cmd"):
                    (self.home / name).touch()
                return {
                    "format": "omni-candidate-install-1",
                    "wheel_sha256": self.sha,
                    "dependency_bundle_sha256": arguments[arguments.index("--dependency-sha256") + 1],
                    "home": str(self.home),
                    "services_registered": fault == "services",
                    "manager_python": str(
                        self.manager / "venv" / ("Scripts/python.exe" if sys.platform == "win32" else "bin/python")
                    ),
                    "operations": {"install": {"backup": {"destination": str(self.backup)}}},
                }
            if phase in ("manager", "candidate"):
                prefix = Path(arguments[0]).parent.parent
                return {
                    "isolated": 0 if fault == "isolation" else 1,
                    "user_site": False,
                    "prefix": str(prefix),
                    "module": str(prefix / "lib/omni/__init__.py") if fault != "module" else __file__,
                }
            if phase == "launcher":
                assert env["PYTHONPATH"] == str(cwd.parent / "poison" if sys.platform == "win32" else cwd / "poison")
                if fault == "startup":
                    (self.home.parent / "startup-hook-ran").touch()
                return {"integrity": "corrupt" if fault == "doctor" else "ok"}
            if phase == "current":
                return {"active": fault != "inactive", "wheel_sha256": self.sha}
            assert phase == "uninstall" and arguments[-1] == self.sha
            if fault == "database":
                (self.home / "data/omni.db").write_bytes(b"changed")
            if fault == "backup":
                (self.backup / "manifest.json").write_bytes(b"changed")
            if fault != "cleanup":
                assert self.stage.is_relative_to(self.home)
                shutil.rmtree(self.stage)
                for name in ("runtime/current.json", "omni", "omni.cmd"):
                    (self.home / name).unlink()
            return {"removed": True, "data_kept": True, "backup_kept": str(self.backup)}

    return Peer


def test_isolated_install_check_preserves_data_and_records_no_whole_acceptance(tmp_path):
    args = inputs(tmp_path)
    result = asyncio.run(check.check_installation(*args, command_type=peer()))
    assert result["verified"] and result["candidate_removed"] and result["data_kept"]
    assert result["before_uninstall"] == {key: result[key] for key in ("data_sha256", "backup_manifest_sha256")}
    assert not result["whole_v2_accepted"] and not result["services_registered"]
    assert [record["phase"] for record in result["operations"]] == [
        "base",
        "install",
        "manager",
        "candidate",
        "launcher",
        "current",
        "uninstall",
    ]
    assert json.loads((args[4] / "report.json").read_text()) == result


@pytest.mark.parametrize(
    "fault",
    [
        "base",
        "pointer",
        "services",
        "isolation",
        "module",
        "startup",
        "doctor",
        "inactive",
        "database",
        "backup",
        "cleanup",
    ],
)
def test_check_rejects_false_success_and_retains_failed_evidence(tmp_path, fault):
    args = inputs(tmp_path)
    with pytest.raises(RuntimeError, match="candidate_installation_check_failed"):
        asyncio.run(check.check_installation(*args, command_type=peer(fault)))
    report = json.loads((args[4] / "report.json").read_text())
    assert report["state"] == "failed" and report["verified"] is False
    assert not report["whole_v2_accepted"] and args[2].is_dir()
    if fault != "base":
        assert list(args[4].glob("home*/data/omni.db"))
    if fault in {"database", "backup", "cleanup"}:
        assert report["before_uninstall"]["data_sha256"] == hashlib.sha256(b"retained isolated database").hexdigest()


@pytest.mark.parametrize("fault", ["existing", "wheel_pin", "bundle_pin", "overlap"])
def test_invalid_inputs_do_not_start_processes_or_replace_existing_data(tmp_path, fault):
    args = list(inputs(tmp_path))
    if fault == "existing":
        args[4].mkdir()
        (args[4] / "original").write_bytes(b"preserve")
    elif fault == "wheel_pin":
        args[1] = "a" * 64
    elif fault == "bundle_pin":
        args[3] = "a" * 64
    else:
        args[4] = args[2] / "nested-result"
    with pytest.raises(RuntimeError):
        asyncio.run(check.check_installation(*args, command_type=peer()))
    assert not (args[4] / "report.json").exists()
    if fault == "existing":
        assert (args[4] / "original").read_bytes() == b"preserve"


@pytest.mark.parametrize("payload", [b'{"x":1,"x":2}', b'{"x":NaN}', b"[]"])
def test_report_decoder_rejects_ambiguous_receipts(payload):
    with pytest.raises((RuntimeError, ValueError)):
        check.decode(payload)


def controller(tmp_path):
    report = {"operations": []}
    path = tmp_path / "report.json"
    return check.Commands(report["operations"], report, path), report


def test_real_child_pipes_are_bounded_and_do_not_copy_stderr_into_report(tmp_path):
    commands, report = controller(tmp_path)
    code = "import json,sys; sys.stderr.write('private canary'); print(json.dumps({'ok':True}))"
    result = asyncio.run(commands.run("probe", [sys.executable, "-I", "-c", code], tmp_path, {}))
    assert result == {"ok": True}
    record = report["operations"][0]
    assert record["state"] == "verified" and record["returncode"] == 0
    assert record["stderr_sha256"] == hashlib.sha256(b"private canary").hexdigest()
    assert "private canary" not in json.dumps(report)


def test_output_overflow_fails_and_reclaims_owned_child(tmp_path, monkeypatch):
    commands, report = controller(tmp_path)
    monkeypatch.setattr(check, "MAX_OUTPUT", 64)
    code = "import sys,time; sys.stderr.write('x'*8192); sys.stderr.flush(); time.sleep(60)"
    with pytest.raises(RuntimeError):
        asyncio.run(commands.run("probe", [sys.executable, "-I", "-c", code], tmp_path, {}))
    assert report["operations"][0]["state"] == "failed"
    assert report["operations"][0]["stderr_bytes"] > 64


def test_cancellation_keeps_late_spawn_and_actual_cleanup_owned(tmp_path, monkeypatch):
    async def scenario():
        commands, report = controller(tmp_path)
        spawning, allow_spawn, cleaning, allow_cleanup, exited = (asyncio.Event() for _ in range(5))
        stdout, stderr = asyncio.StreamReader(), asyncio.StreamReader()

        async def wait():
            await exited.wait()
            return 0

        async def terminate(_timeout):
            cleaning.set()
            await allow_cleanup.wait()
            stdout.feed_eof()
            stderr.feed_eof()
            exited.set()
            return True

        owned = SimpleNamespace(
            process=SimpleNamespace(pid=123, stdout=stdout, stderr=stderr, wait=wait), created=1.0, terminate=terminate
        )

        async def spawn(*_args, **_kwargs):
            spawning.set()
            await allow_spawn.wait()
            return owned

        monkeypatch.setattr(check, "OwnedProcess", SimpleNamespace(spawn=spawn))
        task = asyncio.create_task(commands.run("probe", ["injected"], tmp_path, {}))
        await spawning.wait()
        task.cancel()
        await asyncio.sleep(0)
        assert not task.done()
        allow_spawn.set()
        await cleaning.wait()
        task.cancel()
        await asyncio.sleep(0)
        assert not task.done()
        allow_cleanup.set()
        with pytest.raises(asyncio.CancelledError):
            await task
        assert exited.is_set() and report["operations"][0]["state"] == "failed"

    asyncio.run(scenario())


def test_standalone_installer_json_preserves_non_legacy_unicode_under_isolated_python(tmp_path):
    app = tmp_path / "omnioperator-1.0-py3-none-any.whl"
    with zipfile.ZipFile(app, "w") as archive:
        for name in ("omni", "omni/adapters", "omni/adapters/platform", "omni/domain"):
            archive.writestr(name + "/__init__.py", "")
        archive.writestr(
            "omni/adapters/platform/candidate_installer.py",
            "def install_candidate(wheel,sha,bundle,bundle_sha,manager,home):\n return {'home':str(home)}\n",
        )
        archive.writestr("omni/domain/contracts.py", "class BusinessError(Exception): pass\n")
    home = tmp_path / "用户🙂"
    args = [
        sys.executable,
        "-I",
        str(SCRIPT.with_name("install_v2.py")),
        "--wheel",
        str(app),
        "--sha256",
        check.digest(app),
        "--dependency-bundle",
        str(tmp_path / "bundle"),
        "--dependency-sha256",
        "a" * 64,
        "--manager",
        str(tmp_path / "manager"),
        "--home",
        str(home),
    ]
    result = subprocess.run(args, capture_output=True, timeout=30, check=False)
    assert result.returncode == 0, result.stderr
    assert json.loads(result.stdout) == {"home": str(home)}
    assert result.stdout.isascii() and not home.exists()
