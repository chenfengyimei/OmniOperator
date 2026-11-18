"""V2 soak uses the signed local boot, bounded private evidence and real elapsed coverage; no actual 72h claim."""

import json
import os
import runpy
import uuid

import httpx
import pytest
from omni_test_support.paths import REPOSITORY_ROOT as REPOSITORY_ROOT
from omni_test_support.v2.event_stream import server

from omni.adapters.platform.runtime.permissions import private_path
from omni.adapters.platform.runtime.runtime_identity import canonical_proof
from omni.adapters.soak import LocalSampler, SoakFiles, origin
from omni.domain.contracts import BusinessError
from omni.domain.soak import COUNTERS, LIMIT_KEYS, MINIMUM_SECONDS, RESOURCES, assess, limits


def policy():
    return {key: 1000 for key in LIMIT_KEYS}


def sample():
    return {"ok": True, "resources": dict.fromkeys(RESOURCES, 1), "counters": dict.fromkeys(COUNTERS, 0), "code": None}


def rows():
    for index in range(865):
        yield {"sequence": index + 1, "elapsed_s": index * 300, "wall_elapsed_s": index * 300, **sample()}


def manifest():
    return {
        "limits": policy(),
        "interval_s": 300,
        "sampler_mode": "native_v2_hmac_http",
        "clock_mode": "native_monotonic_wall",
    }


def test_synthetic_policy_requires_a_full_72h_window_and_never_promotes_to_product_acceptance():
    report = assess(manifest(), rows())
    assert report["observational_soak_passed"] and report["observed_seconds"] == MINIMUM_SECONDS
    assert not report["whole_v2_complete"] and not report["production_release_ready"]
    assert not assess(manifest(), list(rows())[:-1])["observational_soak_passed"]
    injected = assess({**manifest(), "sampler_mode": "injected", "clock_mode": "injected"}, rows())
    assert not injected["observational_soak_passed"]
    assert {"injected_sampler", "injected_clock"} <= set(injected["blockers"])


def test_sample_failures_gaps_clock_jumps_and_counter_regression_remain_blockers():
    for change, blocker in [
        (lambda values: values[3].update(ok=False), "sample_failed"),
        (lambda values: values[3].update(wall_elapsed_s=0), "clock_not_increasing"),
        (lambda values: values[3].update(wall_elapsed_s=6000), "sample_gap"),
        (lambda values: values[3]["resources"].update(main_rss_bytes=1001), "limit_main_rss_bytes"),
    ]:
        values = list(rows())
        change(values)
        report = assess(manifest(), values)
        assert not report["observational_soak_passed"] and blocker in report["blockers"]
    values = list(rows())
    values[1]["counters"]["failed_runs"] = 100
    assert "counter_regressed_failed_runs" in assess(manifest(), values)["blockers"]
    for value in ({}, {**policy(), "main_rss_bytes": True}, {**policy(), "unknown": 1}):
        with pytest.raises(BusinessError):
            limits(value)


def identity():
    return {
        "instance_id": str(uuid.uuid4()),
        "boot_id": str(uuid.uuid4()),
        "pid": 123,
        "process_created": 123.0,
        "build_id": "a" * 64,
        "api_version": 1,
        "schema_version": "v2_0038",
        "service_plan_sha256": None,
    }


def protected(path, value):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(value)
    private_path(path)


def test_sampler_uses_only_get_headers_and_hmac_bound_metadata_and_rejects_restarted_boot(tmp_path):
    home = tmp_path / "home"
    current = identity()
    key, token = b"x" * 32, "private-owner-token-that-must-not-be-exported"
    protected(
        home / "run/runtime.json",
        json.dumps({"format": "omni-runtime-identity-1", "state": "running", **current}).encode(),
    )
    protected(home / "run/readiness.key", key)
    protected(home / "run/owner.token", token.encode())
    seen = []
    metrics = {
        "host": {"boot_id": current["boot_id"], "build_id": current["build_id"]},
        "runtime": {
            "ready": True,
            "closing": False,
            **dict.fromkeys(
                (
                    "extension_cleanup_pending",
                    "mcp_cleanup_pending",
                    "desktop_portal_cleanup_pending",
                    "desktop_control_cleanup_pending",
                    "desktop_cleanup_pending",
                    "terminal_cleanup_pending",
                    "shutdown_residual_count",
                ),
                0,
            ),
        },
        "persisted": {"database": {"schema_version": "v2_0038"}, "runs": {"failed": 2}, "model_calls": {"unknown": 1}},
        "private-injected-extra-field": "not-exported",
    }

    def transport(request):
        seen.append(request)
        assert request.method == "GET" and token not in str(request.url)
        if request.url.path == "/health/identity":
            return httpx.Response(
                200,
                stream=httpx.ByteStream(
                    json.dumps(
                        canonical_proof({**current, "ready": True}, request.url.params["challenge"], key)
                    ).encode()
                ),
            )
        assert request.url.path == "/api/v1/system/metrics"
        assert request.headers["Authorization"] == "Bearer " + token
        return httpx.Response(200, stream=httpx.ByteStream(json.dumps(metrics).encode()))

    with httpx.Client(transport=httpx.MockTransport(transport)) as client:
        sampler = LocalSampler(
            home,
            "http://127.0.0.1:8766",
            client=client,
            resources=lambda _value: {key: 1 for key in RESOURCES if key != "cleanup_pending"},
        )
        observed = sampler.sample()
        assert observed["ok"] and len(seen) == 2 and sampler.mode == "injected"
        assert observed["counters"] == {"failed_runs": 2, "unknown_model_calls": 1}
        metrics["runtime"]["desktop_control_cleanup_pending"] = 2
        assert sampler.sample()["resources"]["cleanup_pending"] == 2
        assert token not in json.dumps(observed) and "not-exported" not in json.dumps(observed)
        current["boot_id"] = str(uuid.uuid4())
        protected(
            home / "run/runtime.json",
            json.dumps({"format": "omni-runtime-identity-1", "state": "running", **current}).encode(),
        )
        assert sampler.sample() == {"ok": False, "resources": None, "counters": None, "code": "v2_soak_sample_failed"}


def test_origin_requires_an_explicit_plain_loopback_address_without_redirect_targets_or_credentials():
    assert origin("http://127.0.0.1:8766/") == "http://127.0.0.1:8766"
    assert origin("http://[::1]:8766") == "http://[::1]:8766"
    for value in [
        "https://127.0.0.1:8766",
        "http://localhost:8766",
        "http://example.com:8766",
        "http://user:secret@127.0.0.1:8766",
        "http://127.0.0.1:8766/?token=secret",
        "http://127.0.0.1:8766/path",
    ]:
        with pytest.raises(BusinessError):
            origin(value)


class Clock:
    value = 1000000.0

    def now(self):
        return self.value


class InjectedSampler:
    mode = "injected"
    url = "http://127.0.0.1:8766"

    def __init__(self, home):
        self.identity = identity()
        self.home = home.absolute()

    def sample(self):
        return sample()


def fixture(tmp_path, interval=60):
    clock = Clock()
    files = SoakFiles(tmp_path / "evidence", clock=clock.now, wall=clock.now)
    value = files.create(tmp_path / "home", InjectedSampler(tmp_path / "home"), policy(), interval)
    return clock, files, value


def test_append_resume_validates_the_existing_chain_and_never_truncates_partial_or_changed_rows(tmp_path):
    clock, files, value = fixture(tmp_path)
    clock.value += 60
    assert files.append(value, sample()) == 2
    resumed = SoakFiles(files.folder, clock=clock.now, wall=clock.now)
    clock.value += 60
    assert resumed.append(value, sample()) == 3
    clock.value += 60
    assert files.append(value, sample()) == 4
    report = resumed.report()
    assert report["samples"] == 4 and not report["observational_soak_passed"]
    path = files.folder / "samples.jsonl"
    original = path.read_bytes()
    path.write_bytes(original + b'{"partial":')
    before = path.read_bytes()
    with pytest.raises(BusinessError):
        resumed.append(value, sample())
    assert path.read_bytes() == before


def test_a_long_fake_clock_or_sampler_never_becomes_real_72h_evidence(tmp_path):
    clock, files, value = fixture(tmp_path, interval=3600)
    for _ in range(72):
        clock.value += 3600
        files.append(value, sample())
    report = files.report()
    assert report["observed_seconds"] >= MINIMUM_SECONDS
    assert not report["observational_soak_passed"]
    assert {"injected_sampler", "injected_clock"} == set(report["blockers"])


def test_evidence_rejects_linked_files_and_unknown_payload_fields_without_mutating_existing_data(tmp_path):
    _, files, value = fixture(tmp_path)
    path = files.folder / "samples.jsonl"
    before = path.read_bytes()
    with pytest.raises(BusinessError):
        files.append(value, {**sample(), "private": "do-not-store"})
    assert path.read_bytes() == before
    with pytest.raises(FileExistsError):
        files.create(tmp_path / "home", InjectedSampler(tmp_path / "home"), policy(), 60)
    assert path.read_bytes() == before
    with pytest.raises(BusinessError):
        SoakFiles(tmp_path / "home/../evidence")
    os.link(path, tmp_path / "alias.jsonl")
    with pytest.raises(BusinessError):
        files.report()
    assert path.read_bytes() == before


def test_report_cli_returns_a_non_success_status_for_short_or_injected_runs(tmp_path, capsys):
    _, files, _ = fixture(tmp_path)
    root = REPOSITORY_ROOT
    main = runpy.run_path(str(root / "agent/scripts/v2_soak.py"))["main"]
    assert main(["report", "--output", str(files.folder)]) == 2
    report = json.loads(capsys.readouterr().out)
    assert not report["observational_soak_passed"] and not report["whole_v2_complete"]


def test_actual_local_v2_boot_and_process_can_be_observed_without_invoking_the_model_or_claiming_72h(tmp_path, capsys):
    home = tmp_path / "home"
    with server(home) as (_app, url):
        sampler = LocalSampler(home, url)
        try:
            observed = sampler.sample()
            assert observed["ok"] is True and sampler.mode == "native_v2_hmac_http"
            assert observed["resources"]["main_rss_bytes"] > 0
            files = SoakFiles(tmp_path / "evidence")
            pinned = files.create(home, sampler, dict.fromkeys(LIMIT_KEYS, 10**12), 60)
            assert pinned["sampler_mode"] == "native_v2_hmac_http"
            assert pinned["clock_mode"] == "native_monotonic_wall"
            files.append(pinned, sampler.sample())
            report = files.report()
            assert report["samples"] == 2 and report["failed_samples"] == 0
            assert report["blockers"] == ["duration_below_72h"]
            owner_token = (home / "run/owner.token").read_text(encoding="utf-8")
            assert owner_token not in (files.folder / "manifest.json").read_text(encoding="utf-8")
            assert owner_token not in (files.folder / "samples.jsonl").read_text(encoding="utf-8")
            assert not report["observational_soak_passed"]
            root = REPOSITORY_ROOT
            main = runpy.run_path(str(root / "agent/scripts/v2_soak.py"))["main"]
            approved = tmp_path / "limits.json"
            approved.write_text(json.dumps(dict.fromkeys(LIMIT_KEYS, 10**12)), encoding="utf-8")
            assert (
                main(
                    [
                        "run",
                        "--home",
                        str(home),
                        "--url",
                        url,
                        "--output",
                        str(tmp_path / "short-run"),
                        "--limits",
                        str(approved),
                        "--hours",
                        "0.000001",
                    ]
                )
                == 2
            )
            assert '"observational_soak_passed": false' in capsys.readouterr().out
        finally:
            sampler.close()
