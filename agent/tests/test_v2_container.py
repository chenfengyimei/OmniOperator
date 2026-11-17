"""Container delivery orchestration with injected Docker; real bounded local command capture, no image/native-platform claim."""

import builtins
import contextlib
import io
import json
import runpy
import subprocess
import sys
import uuid
from pathlib import Path
from types import SimpleNamespace
from urllib.error import HTTPError

import pytest
import yaml
from fastapi.testclient import TestClient

from omni.composition.http import http_app

ROOT = Path(__file__).resolve().parents[2]


@pytest.fixture
def checker():
    entry = runpy.run_path(str(ROOT / "scripts/check_v2_container.py"))
    return entry["check"].__globals__


class Docker:
    def __init__(self, error, *, fail=None, changed=False, stale=False):
        self.error, self.fail, self.changed = error, fail, changed
        self.stale = stale
        self.calls = []
        self.probes = self.stops = 0
        self.agent = str(uuid.uuid4())
        self.instance, self.boot = str(uuid.uuid4()), str(uuid.uuid4())

    def __call__(self, args, *, timeout):
        assert args[0] == "docker"
        command = args[1:]
        self.calls.append(command)
        stage = command[0] if command[0] != "volume" else "volume_" + command[1]
        if stage == self.fail:
            raise self.error("fixture_command_failure")
        if command[:2] == ["image", "inspect"]:
            return json.dumps({"id": "sha256:" + "a" * 64, "user": "10001:10001"})
        if command[0] == "inspect":
            return "true" if command[2] == "{{.State.Running}}" else "0"
        if command[0] == "create":
            assert "none" in command and "--read-only" in command and "--init" in command
            assert "--pull" in command and command[command.index("--pull") + 1] == "never"
            assert "--cap-drop" in command and "ALL" in command
            assert command[-5:] == ["sha256:" + "a" * 64, "serve", "--host", "127.0.0.1", "--stdout-logs"]
            assert not any(value.startswith(("type=bind", "--privileged", "--publish")) for value in command)
        if command[0] == "exec":
            self.probes += 1
            return json.dumps(
                {
                    "agent_id": str(uuid.uuid4()) if self.changed and self.probes == 2 else self.agent,
                    "instance_id": self.instance,
                    "boot_id": self.boot if self.stale else str(uuid.uuid4()),
                    "schema_version": "v2_0038",
                    "build_id": "b" * 64,
                    "nonroot": True,
                    "private_home": True,
                    "owner_auth": True,
                    "headless": True,
                }
            )
        if command[0] == "stop":
            assert timeout == 50 and command[1:3] == ["--time", "45"]
            self.stops += 1
        return "owned"


def test_core_check_pins_the_image_keeps_the_same_agent_and_removes_only_new_random_resources(
    tmp_path, monkeypatch, checker
):
    docker = Docker(checker["CheckError"])
    monkeypatch.setitem(checker, "command", docker)
    output = tmp_path / "proof.json"
    result = checker["check"]("local:built", output)
    assert result["passed"] and result["cleanup_complete"] and docker.stops == 2
    assert [row["stage"] for row in result["observations"]] == ["fresh", "reopened"]
    assert {row["agent_id"] for row in result["observations"]} == {docker.agent}
    assert result["container"] == result["volume"] and result["container"].startswith("omni-v2-check-")
    assert docker.calls[-2:] == [["rm", "--force", result["container"]], ["volume", "rm", result["volume"]]]
    assert json.loads(output.read_text()) == result and not result["whole_v2_accepted"]
    assert not any("owner.token" in str(row) for row in result["observations"])


@pytest.mark.parametrize("stage", ["image", "volume_create", "create", "start", "stop", "rm", "volume_rm"])
def test_failed_or_unknown_commands_preserve_failure_and_cannot_fabricate_clean_proof(
    tmp_path, monkeypatch, checker, stage
):
    docker = Docker(checker["CheckError"], fail=stage)
    monkeypatch.setitem(checker, "command", docker)
    result = checker["check"]("local:built", tmp_path / "proof.json")
    expected = "container_cleanup_pending" if stage in {"rm", "volume_rm"} else "fixture_command_failure"
    assert not result["passed"] and result["error_code"] == expected
    assert result["cleanup_complete"] is (stage in {"image", "start", "stop"})
    assert all("local:built" not in call for call in docker.calls if call[0] in {"rm", "stop"})
    if stage == "volume_create":
        assert result["volume_create_started"] and not result["container_create_started"]
        assert not any(call[0] == "rm" or call[:2] == ["volume", "rm"] for call in docker.calls)
    if stage == "create":
        assert result["container_create_started"]
        assert not any(call[0] == "rm" for call in docker.calls)


def test_reopening_a_fresh_agent_instead_of_retained_data_fails_even_when_all_http_probes_succeed(
    tmp_path, monkeypatch, checker
):
    docker = Docker(checker["CheckError"], changed=True)
    monkeypatch.setitem(checker, "command", docker)
    result = checker["check"]("local:built", tmp_path / "proof.json")
    assert not result["passed"] and result["error_code"] == "container_data_not_preserved"
    assert result["cleanup_complete"] and len(result["observations"]) == 2


def test_reused_boot_cannot_substitute_for_actual_container_restart(tmp_path, monkeypatch, checker):
    docker = Docker(checker["CheckError"], stale=True)
    monkeypatch.setitem(checker, "command", docker)
    result = checker["check"]("local:built", tmp_path / "proof.json")
    assert not result["passed"] and result["error_code"] == "container_boot_not_renewed"
    assert result["cleanup_complete"]


def test_existing_report_and_bad_image_never_reach_the_docker_daemon(tmp_path, monkeypatch, checker):
    calls = []
    monkeypatch.setitem(checker, "command", lambda *args, **kwargs: calls.append(args))
    output = tmp_path / "proof.json"
    output.write_text("preserved")
    with pytest.raises(FileExistsError):
        checker["check"]("local:built", output)
    for image in ("--pull=always", "local image", "", "a" * 257):
        with pytest.raises(checker["CheckError"]):
            checker["check"](image, tmp_path / "new.json")
    assert output.read_text() == "preserved" and not calls


def test_actual_command_has_bounded_capture_no_diagnostic_body_and_preserves_timeout_budget(checker, monkeypatch):
    command, error = checker["command"], checker["CheckError"]
    assert command([sys.executable, "-c", "print('real-local-process')"]) == "real-local-process"
    for program, expected in (
        ("import sys;sys.stdout.write('x'*65537)", "container_output_limit"),
        ("import sys;sys.stderr.write('private-provider-body');sys.exit(7)", "container_command_failed"),
    ):
        with pytest.raises(error) as caught:
            command([sys.executable, "-c", program])
        assert str(caught.value) == expected and "private-provider-body" not in str(caught.value)
    original = subprocess.run
    observed = []

    def run(*args, **kwargs):
        observed.append(kwargs["timeout"])
        raise subprocess.TimeoutExpired(args[0], kwargs["timeout"])

    monkeypatch.setattr(subprocess, "run", run)
    with pytest.raises(error, match="container_command_unavailable"):
        command(["injected-timeout"], timeout=15)
    assert observed == [15]
    monkeypatch.setattr(subprocess, "run", original)


def test_compose_keeps_remote_origin_explicit_and_image_has_nonroot_private_volume_seed():
    dockerfile = (ROOT / "deploy/v2/Dockerfile").read_text()
    assert "install -d -m 0700 -o omni -g omni" in dockerfile
    assert "USER 10001:10001" in dockerfile and "HOME=/home/omni" in dockerfile
    assert 'CMD ["serve", "--host", "127.0.0.1", "--stdout-logs"]' in dockerfile
    service = yaml.safe_load((ROOT / "deploy/v2/compose.yaml").read_text())["services"]["omni"]
    assert service["read_only"] is True and service["init"] is True
    assert service["cap_drop"] == ["ALL"] and service["security_opt"] == ["no-new-privileges:true"]
    assert service["stop_grace_period"] == "45s" and service["ports"] == ["127.0.0.1:8766:8766"]
    assert service["command"][service["command"].index("--external-url") + 1].startswith("${OMNI_EXTERNAL_URL:?")
    assert service["volumes"] == ["instance:/home/omni/.local/share/OmniOperator/v2"]


@pytest.mark.parametrize("uid", [10001, 0])
def test_exact_in_container_probe_uses_real_owner_api_without_exporting_token_but_requires_nonroot_metadata(
    tmp_path, checker, uid
):
    home = tmp_path / "isolated-home"
    calls = []

    # Current Windows cannot prove Linux stat/UID. Inject only that metadata;
    # execute the exact probe against real private SQLite/HTTP owner routes.
    class ProbePath:
        def __init__(self, value):
            self.value = Path(value)

        def __truediv__(self, value):
            return ProbePath(self.value / value)

        def stat(self):
            assert self.value.exists()
            return SimpleNamespace(st_uid=10001, st_mode=0o700 if self.value == home else 0o600)

        def is_symlink(self):
            return self.value.is_symlink()

        def read_text(self):
            return self.value.read_text(encoding="utf-8")

    with TestClient(http_app(home)) as client:

        class Response:
            def __init__(self, response):
                self.status, self.data = response.status_code, response.content

            def __enter__(self):
                return self

            def __exit__(self, *args):
                return False

            def read(self, size):
                assert size == 65537
                return self.data[:size]

        class Opener:
            def open(self, request, *, timeout):
                assert timeout == 3 and request.full_url.startswith("http://127.0.0.1:8766/")
                path = request.full_url.removeprefix("http://127.0.0.1:8766")
                calls.append(path)
                response = client.get(path, headers=dict(request.headers))
                if response.status_code != 200:
                    raise HTTPError(request.full_url, response.status_code, "probe", None, None)
                return Response(response)

        original = builtins.__import__

        def imports(name, globals=None, locals=None, fromlist=(), level=0):
            if name == "os":
                return SimpleNamespace(getuid=lambda: uid, environ={"OMNI_HOME": str(home)})
            if name == "pathlib":
                return SimpleNamespace(Path=ProbePath)
            if name in {"urllib.request", "urllib.error"}:
                return SimpleNamespace(
                    request=SimpleNamespace(
                        build_opener=lambda handlers: Opener(),
                        ProxyHandler=lambda proxies: proxies,
                        Request=original("urllib.request", fromlist=["Request"]).Request,
                    ),
                    error=SimpleNamespace(HTTPError=HTTPError),
                )
            return original(name, globals, locals, fromlist, level)

        output = io.StringIO()
        with contextlib.redirect_stdout(output):
            # Optimization must not disable the production probe's checks.
            program = compile(checker["PROBE"], "<container-probe>", "exec", optimize=2)
            if uid == 0:
                with pytest.raises(RuntimeError, match="container probe failed"):
                    exec(program, {"__builtins__": {**vars(builtins), "__import__": imports}})
                assert not calls
                return
            exec(program, {"__builtins__": {**vars(builtins), "__import__": imports}})
        proof = json.loads(output.getvalue())
        assert proof["owner_auth"] and proof["nonroot"] and proof["headless"]
        assert calls == ["/health/ready", "/api/v1/agents", "/api/v1/agents", "/api/v1/version"]
        assert (home / "run/owner.token").read_text() not in output.getvalue()
