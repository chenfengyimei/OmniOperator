"""Verify a pinned local wheel's fresh offline install/launcher/uninstall in a new private directory; no services or accounts."""

from __future__ import annotations

import argparse
import asyncio
import hashlib
import json
import os
import re
import sys
from collections.abc import Mapping
from pathlib import Path
from typing import Any

from omni.adapters.platform.dependency_bundle import no_links
from omni.adapters.platform.permissions import private_path
from omni.adapters.platform.processes import OwnedProcess

MAX_OUTPUT = 2 * 1024 * 1024
BASE_PROBE = "import importlib.util,json,platform,sys; assert importlib.util.find_spec('omni') is None; print(json.dumps({'platform':sys.platform,'python':list(sys.version_info[:2]),'machine':platform.machine(),'omni_absent':True}))"
ENVIRONMENT_PROBE = "import json,site,sys,omni; from pathlib import Path; prefix=Path(sys.prefix).resolve(); assert sys.prefix!=sys.base_prefix and site.ENABLE_USER_SITE is False; assert Path(omni.__file__).resolve().is_relative_to(prefix); print(json.dumps({'prefix':str(prefix),'module':str(Path(omni.__file__).resolve()),'isolated':sys.flags.isolated,'user_site':site.ENABLE_USER_SITE}))"


def require(value: object) -> None:
    if not value:
        raise RuntimeError("candidate_installation_check_failed")


def digest(path: Path, maximum: int = 512 * 1024 * 1024) -> str:
    value, size = hashlib.sha256(), 0
    with path.open("rb") as stream:
        while block := stream.read(1024 * 1024):
            size += len(block)
            require(size <= maximum)
            value.update(block)
    return value.hexdigest()


def decode(raw: bytes) -> dict[str, Any]:
    def pairs(items: list[tuple[str, Any]]) -> dict[str, Any]:
        result: dict[str, Any] = {}
        for key, value in items:
            require(key not in result)
            result[key] = value
        return result

    def invalid(_value: str) -> None:
        raise RuntimeError("candidate_installation_check_failed")

    require(len(raw) <= MAX_OUTPUT)
    value = json.loads(raw, object_pairs_hook=pairs, parse_constant=invalid)
    require(isinstance(value, dict))
    return value


def save(path: Path, report: dict[str, Any]) -> None:
    path.write_text(json.dumps(report, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    private_path(path)


class Commands:
    """Own each real child/group and bounded pipes through timeout or cancellation."""

    def __init__(self, records: list[dict[str, Any]], report: dict[str, Any], path: Path) -> None:
        self.records, self.report, self.path = records, report, path

    async def run(self, phase: str, arguments: list[str], cwd: Path, env: Mapping[str, str]) -> dict[str, Any]:
        aborted = asyncio.Event()
        task = asyncio.create_task(self._run(phase, arguments, cwd, env, aborted))
        try:
            return await asyncio.shield(task)
        except asyncio.CancelledError:
            # Spawn/cleanup ownership stays in the shielded controller even if
            # cancellation arrives before create_subprocess_exec returns.
            aborted.set()
            while not task.done():
                try:
                    await asyncio.shield(task)
                except asyncio.CancelledError:
                    continue
                except BaseException:  # noqa: BLE001 - retrieve the owned task's failure before preserving cancellation
                    break
            if not task.cancelled():
                task.exception()
            raise

    async def _run(
        self, phase: str, arguments: list[str], cwd: Path, env: Mapping[str, str], aborted: asyncio.Event
    ) -> dict[str, Any]:
        record: dict[str, Any] = {"phase": phase, "state": "starting"}
        self.records.append(record)
        save(self.path, self.report)
        owned = await OwnedProcess.spawn(
            arguments,
            stdin=asyncio.subprocess.DEVNULL,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
            env=env,
            cwd=str(cwd),
        )
        overflow = asyncio.Event()
        output = bytearray()

        async def drain(stream: asyncio.StreamReader, name: str, capture: bool) -> None:
            size, checksum = 0, hashlib.sha256()
            while block := await stream.read(4096):
                size += len(block)
                checksum.update(block)
                if size > MAX_OUTPUT:
                    overflow.set()
                elif capture:
                    output.extend(block)
            record[name + "_bytes"], record[name + "_sha256"] = size, checksum.hexdigest()

        assert owned.process.stdout is not None and owned.process.stderr is not None
        readers = [
            asyncio.create_task(drain(owned.process.stdout, "stdout", True)),
            asyncio.create_task(drain(owned.process.stderr, "stderr", False)),
        ]

        async def wait_exit() -> None:
            record["returncode"] = await owned.process.wait()

        exited = asyncio.create_task(wait_exit())
        collected = asyncio.gather(*readers, exited)
        exceeded = asyncio.create_task(overflow.wait())
        cancelled = asyncio.create_task(aborted.wait())
        try:
            record.update(state="running", pid=owned.process.pid, created=owned.created)
            save(self.path, self.report)
            async with asyncio.timeout(600 if phase in {"install", "uninstall"} else 30):
                await asyncio.wait((collected, exceeded, cancelled), return_when=asyncio.FIRST_COMPLETED)
                require(not overflow.is_set() and not aborted.is_set())
                await asyncio.shield(collected)
                require(record["returncode"] == 0)
                result = decode(bytes(output))
                record["state"] = "verified"
                return result
        except BaseException as exc:
            record.update(state="failed", error_type=type(exc).__name__)
            raise
        finally:
            # Never let an observation timeout or waiter cancellation orphan an
            # installer/pip child. Retain the controller until actual recovery.
            completed_state = record["state"]
            while not await owned.terminate(5):
                record["state"] = "cleanup_pending"
                save(self.path, self.report)
                await asyncio.sleep(0.1)
            exceeded.cancel()
            cancelled.cancel()
            await asyncio.gather(exceeded, cancelled, collected, return_exceptions=True)
            record.update(state=completed_state, process_group_reclaimed=True)
            save(self.path, self.report)


async def check_installation(
    wheel: Path,
    wheel_sha: str,
    bundle: Path,
    bundle_sha: str,
    output: Path,
    base_python: Path,
    installer: Path,
    *,
    command_type: type[Commands] = Commands,
) -> dict[str, Any]:
    require(re.fullmatch(r"[a-f0-9]{64}", wheel_sha) and re.fullmatch(r"[a-f0-9]{64}", bundle_sha))
    require(output.is_absolute() and not output.exists())
    for path in (wheel, bundle, output, installer):
        no_links(path)
    wheel, bundle, output, base_python, installer = (
        path.resolve() for path in (wheel, bundle, output, base_python, installer)
    )
    require(
        wheel.is_file() and wheel.suffix == ".whl" and bundle.is_dir() and installer.is_file() and base_python.is_file()
    )
    require(digest(wheel) == wheel_sha and digest(bundle / "manifest.json", 262144) == bundle_sha)
    require(
        all(
            not output.is_relative_to(path) and not path.is_relative_to(output)
            for path in (wheel, bundle, base_python, installer)
        )
    )
    output.mkdir(mode=0o700)
    private_path(output, directory=True)
    report: dict[str, Any] = {
        "format": "omni-candidate-installation-check-1",
        "platform": sys.platform,
        "wheel_sha256": wheel_sha,
        "dependency_bundle_sha256": bundle_sha,
        "installer_sha256": digest(installer),
        "verified": False,
        "state": "running",
        "operations": [],
        "services_registered": False,
        "real_account_model_device_actions": False,
        "whole_v2_accepted": False,
    }
    report_path = output / "report.json"
    save(report_path, report)
    commands = command_type(report["operations"], report, report_path)
    env = {
        key: os.environ[key]
        for key in (
            "SystemRoot",
            "WINDIR",
            "PATH",
            "HOME",
            "USERPROFILE",
            "TMP",
            "TEMP",
            "LANG",
            "LC_ALL",
            "LOCALAPPDATA",
            "APPDATA",
            "OMNI_TEST_OFFLINE",
            "OMNI_TEST_VIOLATIONS",
        )
        if key in os.environ
    }
    env.update(PYTHONUTF8="1", PIP_NO_INDEX="1", OMNI_WRAPPER_EXPAND="wrong-location")
    home, manager = output / "home 用户🙂 %OMNI_WRAPPER_EXPAND% !literal! ' $(false)", output / "manager with spaces"
    try:
        base = await commands.run("base", [str(base_python), "-I", "-c", BASE_PROBE], output, env)
        require(base.get("omni_absent") is True and base.get("platform") == sys.platform)
        installed = await commands.run(
            "install",
            [
                str(base_python),
                "-I",
                str(installer),
                "--wheel",
                str(wheel),
                "--sha256",
                wheel_sha,
                "--dependency-bundle",
                str(bundle),
                "--dependency-sha256",
                bundle_sha,
                "--manager",
                str(manager),
                "--home",
                str(home),
            ],
            output,
            env,
        )
        require(
            installed.get("format") == "omni-candidate-install-1"
            and installed.get("wheel_sha256") == wheel_sha
            and installed.get("dependency_bundle_sha256") == bundle_sha
            and installed.get("home") == str(home)
            and installed.get("services_registered") is False
        )
        manager_python = manager / "venv" / ("Scripts/python.exe" if sys.platform == "win32" else "bin/python")
        pointer = decode((home / "runtime/current.json").read_bytes())
        expected_python = (
            home
            / "updates"
            / wheel_sha[:16]
            / "venv"
            / ("Scripts/python.exe" if sys.platform == "win32" else "bin/python")
        )
        require(
            installed.get("manager_python") == str(manager_python)
            and pointer.get("python") == str(expected_python)
            and pointer.get("mode") == "venv"
            and pointer.get("wheel_sha256") == wheel_sha
        )
        for phase, python, prefix in (
            ("manager", manager_python, manager / "venv"),
            ("candidate", expected_python, expected_python.parent.parent),
        ):
            proof = await commands.run(phase, [str(python), "-I", "-c", ENVIRONMENT_PROBE], output, env)
            require(
                proof.get("isolated") == 1
                and proof.get("user_site") is False
                and Path(proof["prefix"]).resolve() == prefix.resolve()
                and Path(proof["module"]).resolve().is_relative_to(prefix.resolve())
            )
        poison = output / "poison"
        poison.mkdir(mode=0o700)
        marker = output / "startup-hook-ran"
        (poison / "sitecustomize.py").write_text(
            f"from pathlib import Path\nPath({str(marker)!r}).touch()\n", encoding="utf-8"
        )
        polluted = {**env, "PYTHONPATH": str(poison)}
        if sys.platform == "win32":
            launcher = [
                str(Path(os.environ["SystemRoot"]) / "System32/cmd.exe"),
                "/d",
                "/v:on",
                "/c",
                "omni.cmd",
                "doctor",
            ]
            cwd = home
        else:
            launcher, cwd = [str(home / "omni"), "doctor"], output
        doctor = await commands.run("launcher", launcher, cwd, polluted)
        require(doctor.get("integrity") == "ok" and not marker.exists())
        cli = [str(manager_python), "-I", "-m", "omni.composition.cli", "--home", str(home)]
        current = await commands.run("current", [*cli, "installation", "current"], output, env)
        require(current.get("active") is True and current.get("wheel_sha256") == wheel_sha)
        database = home / "data/omni.db"
        before = digest(database)
        backup = Path(installed["operations"]["install"]["backup"]["destination"])
        require(backup.resolve().is_relative_to(output) and (backup / "manifest.json").is_file())
        backup_sha = digest(backup / "manifest.json")
        report["before_uninstall"] = {"data_sha256": before, "backup_manifest_sha256": backup_sha}
        save(report_path, report)
        removed = await commands.run(
            "uninstall", [*cli, "installation", "uninstall", "--sha256", wheel_sha], output, env
        )
        require(
            removed.get("removed") is True
            and removed.get("data_kept") is True
            and removed.get("backup_kept") == str(backup)
        )
        require(
            not expected_python.parent.parent.exists()
            and not (home / "runtime/current.json").exists()
            and not (home / "omni").exists()
            and not (home / "omni.cmd").exists()
        )
        require(database.is_file() and digest(database) == before and digest(backup / "manifest.json") == backup_sha)
        report.update(
            verified=True,
            state="verified",
            base_omni_absent=True,
            isolated_environments=True,
            launcher_argv_and_environment_verified=True,
            data_sha256=before,
            backup_manifest_sha256=backup_sha,
            candidate_removed=True,
            data_kept=True,
        )
        return report
    except BaseException as exc:
        report.update(state="failed", error_code="candidate_installation_check_failed", error_type=type(exc).__name__)
        raise
    finally:
        save(report_path, report)


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--wheel", type=Path, required=True)
    parser.add_argument("--sha256", required=True)
    parser.add_argument("--dependency-bundle", type=Path, required=True)
    parser.add_argument("--dependency-sha256", required=True)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--base-python", type=Path, default=Path(getattr(sys, "_base_executable", sys.executable)))
    args = parser.parse_args()
    try:
        report = asyncio.run(
            check_installation(
                args.wheel,
                args.sha256,
                args.dependency_bundle,
                args.dependency_sha256,
                args.output,
                args.base_python,
                Path(__file__).with_name("install_v2.py"),
            )
        )
        print(json.dumps({key: report[key] for key in ("format", "verified", "platform")}))
        return 0
    except (OSError, RuntimeError, ValueError, KeyError, TimeoutError):
        print(json.dumps({"error": "candidate_installation_check_failed"}), file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
