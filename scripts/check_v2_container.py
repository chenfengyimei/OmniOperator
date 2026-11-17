"""Check an already built V2 image in a new offline container/volume; never build, pull or start Docker."""

from __future__ import annotations

import argparse
import json
import os
import re
import subprocess
import tempfile
import time
import uuid
from pathlib import Path

HOME = "/home/omni/.local/share/OmniOperator/v2"
PROBE = r"""
import json, os, re, stat, urllib.error, urllib.request, uuid
from pathlib import Path
def require(value):
    if not value:
        raise RuntimeError("container probe failed")
home = Path(os.environ["OMNI_HOME"])
require(os.getuid() == 10001 and home.stat().st_uid == os.getuid())
require(stat.S_IMODE(home.stat().st_mode) == 0o700)
token = home / "run/owner.token"
require(not token.is_symlink() and stat.S_IMODE(token.stat().st_mode) == 0o600)
require(not any(os.environ.get(key) for key in ("DISPLAY", "WAYLAND_DISPLAY", "DBUS_SESSION_BUS_ADDRESS")))
opener = urllib.request.build_opener(urllib.request.ProxyHandler({}))
def get(path, authorization=None):
    headers = {} if authorization is None else {"Authorization": "Bearer " + authorization}
    request = urllib.request.Request("http://127.0.0.1:8766" + path, headers=headers)
    with opener.open(request, timeout=3) as response:
        raw = response.read(65537)
        require(len(raw) <= 65536 and response.status == 200)
        return json.loads(raw)
require(get("/health/ready")["ready"] is True)
try:
    get("/api/v1/agents")
except urllib.error.HTTPError as error:
    require(error.code == 401)
else:
    raise AssertionError("owner authentication missing")
agents = get("/api/v1/agents", token.read_text().strip())["items"]
require(len(agents) == 1)
agent = agents[0]["id"]
version = get("/api/v1/version", token.read_text().strip())
require(type(version["api_version"]) is int and version["api_version"] == 1)
for value in (agent, version["instance_id"], version["boot_id"]):
    require(str(uuid.UUID(value)) == value)
require(re.fullmatch(r"v2_[0-9]{4}", version["schema_version"]) is not None)
require(re.fullmatch(r"[a-f0-9]{64}", version["build_id"]) is not None)
print(json.dumps({"agent_id": agent, "instance_id": version["instance_id"], "boot_id": version["boot_id"],
    "schema_version": version["schema_version"], "build_id": version["build_id"],
    "nonroot": True, "private_home": True, "owner_auth": True, "headless": True}))
"""


class CheckError(Exception):
    pass


def command(arguments: list[str], *, timeout: float = 15) -> str:
    # File-backed capture keeps Docker diagnostics out of memory and reports.
    with tempfile.TemporaryFile() as output, tempfile.TemporaryFile() as errors:
        try:
            result = subprocess.run(
                arguments, stdout=output, stderr=errors, timeout=timeout, check=False
            )
        except (OSError, subprocess.TimeoutExpired) as exc:
            raise CheckError("container_command_unavailable") from exc
        if result.returncode:
            raise CheckError("container_command_failed")
        output.seek(0)
        data = output.read(65537)
        if len(data) > 65536:
            raise CheckError("container_output_limit")
        try:
            return data.decode("utf-8").strip()
        except UnicodeError as exc:
            raise CheckError("container_output_invalid") from exc


def check(image: str, output: Path, *, docker: str = "docker") -> dict:
    if (
        not image
        or image.startswith("-")
        or len(image) > 256
        or any(c.isspace() for c in image)
    ):
        raise CheckError("container_image_invalid")
    # Exclusive creation happens before any Docker mutation. Refuse to overwrite old proof.
    output.parent.mkdir(parents=True, exist_ok=True)
    descriptor = os.open(output, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
    os.close(descriptor)
    name = "omni-v2-check-" + uuid.uuid4().hex
    receipt = {
        "format": "omni-v2-container-check-1",
        "mode": "native_docker_image",
        "image_id": None,
        "container": name,
        "volume": name,
        "passed": False,
        "cleanup_complete": False,
        "error_code": None,
        "observations": [],
        "volume_create_started": False,
        "container_create_started": False,
        "full_suite_run": False,
        "whole_v2_accepted": False,
    }
    container_created = volume_created = False

    def publish():
        output.write_text(
            json.dumps(receipt, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
        )

    def run(*args: str, timeout: float = 15):
        return command([docker, *args], timeout=timeout)

    def observe():
        deadline = time.monotonic() + 60
        while True:
            if run("inspect", "--format", "{{.State.Running}}", name) != "true":
                raise CheckError("container_not_running")
            try:
                value = json.loads(run("exec", name, "python", "-c", PROBE, timeout=10))
                if (
                    not isinstance(value, dict)
                    or set(value)
                    != {
                        "agent_id",
                        "instance_id",
                        "boot_id",
                        "schema_version",
                        "build_id",
                        "nonroot",
                        "private_home",
                        "owner_auth",
                        "headless",
                    }
                    or any(
                        value[key] is not True
                        for key in ("nonroot", "private_home", "owner_auth", "headless")
                    )
                    or any(
                        str(uuid.UUID(value[key])) != value[key]
                        for key in ("agent_id", "instance_id", "boot_id")
                    )
                    or re.fullmatch(r"v2_[0-9]{4}", value["schema_version"]) is None
                    or re.fullmatch(r"[a-f0-9]{64}", value["build_id"]) is None
                ):
                    raise CheckError("container_probe_invalid")
                return value
            except CheckError:
                if time.monotonic() >= deadline:
                    raise
                time.sleep(0.25)
            except (ValueError, KeyError, TypeError, AttributeError) as exc:
                raise CheckError("container_probe_invalid") from exc

    def stop():
        run("stop", "--time", "45", name, timeout=50)
        if run("inspect", "--format", "{{.State.ExitCode}}", name) != "0":
            raise CheckError("container_shutdown_incomplete")

    try:
        publish()
        identity = json.loads(
            run(
                "image",
                "inspect",
                "--format",
                '{"id":{{json .Id}},"user":{{json .Config.User}}}',
                image,
            )
        )
        if (
            not isinstance(identity, dict)
            or re.fullmatch(r"sha256:[a-f0-9]{64}", identity.get("id", "")) is None
            or identity.get("user") != "10001:10001"
        ):
            raise CheckError("container_image_identity_invalid")
        receipt["image_id"] = identity["id"]
        publish()
        receipt["volume_create_started"] = True
        publish()
        run("volume", "create", "--label", f"org.omnioperator.check={name}", name)
        volume_created = True
        receipt["container_create_started"] = True
        publish()
        run(
            "create",
            "--pull",
            "never",
            "--name",
            name,
            "--label",
            f"org.omnioperator.check={name}",
            "--network",
            "none",
            "--read-only",
            "--cap-drop",
            "ALL",
            "--security-opt",
            "no-new-privileges",
            "--pids-limit",
            "128",
            "--init",
            "--tmpfs",
            "/tmp:rw,noexec,nosuid,size=134217728,mode=1777",
            "--mount",
            f"type=volume,source={name},target={HOME}",
            identity["id"],
            "serve",
            "--host",
            "127.0.0.1",
            "--stdout-logs",
        )
        container_created = True
        run("start", name)
        first = observe()
        receipt["observations"].append({"stage": "fresh", **first})
        publish()
        stop()
        run("start", name)
        second = observe()
        receipt["observations"].append({"stage": "reopened", **second})
        if any(
            first[key] != second[key]
            for key in ("agent_id", "instance_id", "build_id", "schema_version")
        ):
            raise CheckError("container_data_not_preserved")
        if first["boot_id"] == second["boot_id"]:
            raise CheckError("container_boot_not_renewed")
        stop()
        receipt["passed"] = True
    except (CheckError, ValueError, TypeError) as exc:
        receipt["error_code"] = (
            str(exc) if isinstance(exc, CheckError) else "container_metadata_invalid"
        )
    finally:
        try:
            # Only the random resources created by this invocation; never user names or host paths.
            if container_created:
                run("rm", "--force", name, timeout=50)
            if volume_created:
                run("volume", "rm", name)
            # A failed create may have reached the daemon. Keep its identity and
            # uncertainty in the receipt; never claim absent or delete an unproved owner.
            receipt["cleanup_complete"] = bool(
                (not receipt["volume_create_started"] or volume_created)
                and (not receipt["container_create_started"] or container_created)
            )
        except CheckError:
            receipt["passed"] = False
            receipt["error_code"] = receipt["error_code"] or "container_cleanup_pending"
        publish()
    return receipt


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--image",
        required=True,
        help="Already built local tag or image SHA; never pulled",
    )
    parser.add_argument(
        "--output",
        required=True,
        type=Path,
        help="New JSON report; existing files are refused",
    )
    args = parser.parse_args()
    try:
        result = check(args.image, args.output)
    except (OSError, CheckError):
        print(json.dumps({"error_code": "container_check_not_started"}))
        return 1
    print(
        json.dumps(
            {key: result[key] for key in ("passed", "cleanup_complete", "error_code")}
        )
    )
    return 0 if result["passed"] and result["cleanup_complete"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
