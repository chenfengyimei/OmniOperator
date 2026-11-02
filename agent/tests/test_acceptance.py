import copy
import hashlib
import json
import os
from pathlib import Path
import sqlite3

import pytest

from omni.config import DATA_DIR, RuntimePaths, STATE_SCHEMA_VERSION
from omni.memory.state import StateStore, _MIGRATIONS
from omni.memory.store import MemoryStore
from omni.system import soak
from omni.system.process_identity import (
    ProcessIdentity,
    inspect_process,
    sample_process,
    process_parents,
    ProcessIdentityError,
)
from omni.system.provenance import source_fingerprint
from omni.system.release_gate import assess, REQUIRED_CASES


SOURCE = "a" * 64
DATA_ID = soak.data_identity(DATA_DIR)
LIMITS = dict(
    max_private_bytes=2_000_000_000,
    max_handles=10000,
    max_private_growth_bytes=500_000_000,
    max_cost_micro=1_000_000,
    max_log_bytes=100_000_000,
)


def sample(index, interval=60):
    return {
        "at": "2026-10-28T00:00:00+00:00",
        "wall": 1700000000 + index * interval,
        "monotonic": 10000 + index * interval,
        "source_matches": True,
        "schema": STATE_SCHEMA_VERSION,
        "integrity": True,
        "errors": [],
        "data_id": DATA_ID,
        "log_bytes": 100,
        "services": {
            name: {
                "verified": True,
                "pid": pid,
                "created": "123",
                "private_bytes": 1000000,
                "handles": 10,
                "children": [],
            }
            for pid, name in enumerate(("omni-qq", "omni-heartbeat"), 1)
        },
        "counts": {
            "runs": index,
            "executions": index,
            "cost_micro": 0,
            "unpriced_calls": 0,
            "outbox_unknown": 0,
            "publication_unknown": 0,
        },
    }


@pytest.fixture
def campaign(tmp_path):
    warmup = tmp_path / "warmup"
    soak.start(warmup, LIMITS, source=SOURCE)
    for index in range(31):
        soak.append(warmup, sample(index))
    calibrated = soak.calibrate(warmup, tmp_path / "limits.json")
    folder = tmp_path / "campaign"
    manifest = soak.start(folder, calibrated, source=SOURCE)
    return folder, manifest


def rows_for_hours(hours=72):
    return [{"sample": sample(index), "sha256": str(index)} for index in range(hours * 60 + 1)]


def test_72h_requires_actual_elapsed_valid_baseline_and_activity(campaign):
    folder, manifest = campaign
    assert not soak.summarize(manifest, rows_for_hours(71))["soak_passed"]
    assert soak.summarize(manifest, rows_for_hours())["soak_passed"]  # 仅模拟时钟的单测，非实际验收证据。
    idle = rows_for_hours()
    for row in idle:
        row["sample"]["counts"]["runs"] = 0
    assert not soak.summarize(manifest, idle)["soak_passed"]


@pytest.mark.parametrize(
    "fault",
    [
        "gap",
        "clock",
        "identity",
        "source",
        "schema",
        "memory",
        "handles",
        "unknown",
        "cost",
        "counter",
        "nan",
        "data",
        "bad_time",
    ],
)
def test_soak_failure_cannot_become_release_success(campaign, fault):
    _, manifest = campaign
    rows = rows_for_hours()
    final = rows[-1]["sample"]
    if fault == "gap":
        rows.pop(5)
        rows.pop(5)
    elif fault == "clock":
        final["wall"] += 1000
    elif fault == "identity":
        final["services"]["omni-qq"]["verified"] = False
    elif fault == "source":
        final["source_matches"] = False
    elif fault == "schema":
        final["schema"] = 9
    elif fault == "memory":
        final["services"]["omni-qq"]["private_bytes"] = 3_000_000_000
    elif fault == "handles":
        final["services"]["omni-qq"]["handles"] = 10001
    elif fault == "unknown":
        final["counts"]["outbox_unknown"] = 1
    elif fault == "cost":
        final["counts"]["cost_micro"] = 2_000_000
    elif fault == "counter":
        final["counts"]["runs"] = 1
    elif fault == "data":
        final["data_id"] = "b" * 64
    elif fault == "bad_time":
        final["monotonic"] = "not-a-clock"
    else:
        final["monotonic"] = float("nan")
    assert not soak.summarize(manifest, rows)["soak_passed"]


def test_evidence_hash_chain_detects_changes_and_short_run_stays_pending(campaign):
    folder, _ = campaign
    soak.append(folder, sample(0))
    report = soak.append(folder, sample(1))
    assert not report["soak_passed"] and report["elapsed_hours"] == 1 / 60
    path = folder / "samples.jsonl"
    path.write_text(
        path.read_text(encoding="utf-8").replace('"private_bytes":1000000', '"private_bytes":0'), encoding="utf-8"
    )
    with pytest.raises(ValueError, match="链"):
        soak.read(folder)


def test_calibration_cannot_be_faked_by_setting_a_flag(tmp_path):
    manifest = soak.start(
        tmp_path / "campaign", dict(LIMITS, calibration_sha256="f" * 64, calibration_seconds=9999), source=SOURCE
    )
    assert not soak.summarize(manifest, rows_for_hours())["soak_passed"]


def test_sampler_reads_only_verified_identity_and_redacted_counts(tmp_path):
    paths = RuntimePaths.create(
        tmp_path / "data", config_path=tmp_path / "config.yaml", skills_root=tmp_path / "skills"
    )
    state = StateStore(paths.state)
    state.close()
    paths.run.mkdir()
    identity = ProcessIdentity(11, "100", str(Path("C:/test/python.exe")))
    record = {
        "version": 1,
        "service": "omni-qq",
        "pid": 11,
        "created": "100",
        "executable": identity.executable,
        "source_fingerprint": SOURCE,
        "launch_id": "1" * 32,
        "source_confirmed": True,
    }
    (paths.run / "omni-qq.pid").write_text(json.dumps(record), encoding="utf-8")
    before = hashlib.sha256(paths.state.read_bytes()).hexdigest()
    report = soak.collect(
        paths,
        SOURCE,
        ["omni-qq"],
        inspector=lambda pid: identity,
        parents=lambda: {},
        sampler=lambda identity: dict(private_bytes=1, working_set=1, handles=1, cpu_seconds=0),
        fingerprint=lambda: SOURCE,
    )
    assert report["services"]["omni-qq"]["verified"] and not report["errors"]
    assert hashlib.sha256(paths.state.read_bytes()).hexdigest() == before
    called = []
    refused = soak.collect(
        paths,
        SOURCE,
        ["omni-qq"],
        inspector=lambda pid: None,
        parents=lambda: {},
        sampler=lambda x: called.append(x),
        fingerprint=lambda: SOURCE,
    )
    assert not called and not refused["services"]["omni-qq"]["verified"]
    # 旧服务重启后遗留的已观察子进程，不能从资源统计中静默消失。
    report["services"]["omni-qq"]["children"] = [{"pid": 12, "created": "101"}]
    orphan = ProcessIdentity(12, "101", identity.executable)
    check = soak.collect(
        paths,
        SOURCE,
        ["omni-qq"],
        inspector=lambda pid: identity if pid == 11 else orphan,
        parents=lambda: {},
        sampler=lambda identity: dict(private_bytes=1, working_set=1, handles=1, cpu_seconds=0),
        fingerprint=lambda: SOURCE,
        previous=report,
    )
    assert "previous_service_process_still_alive" in check["errors"]
    record["source_confirmed"] = False
    (paths.run / "omni-qq.pid").write_text(json.dumps(record), encoding="utf-8")
    check = soak.collect(
        paths,
        SOURCE,
        ["omni-qq"],
        inspector=lambda pid: identity,
        parents=lambda: {},
        sampler=lambda x: called.append(x),
        fingerprint=lambda: SOURCE,
    )
    assert not called and not check["services"]["omni-qq"]["verified"]


def test_calibration_rejects_gaps_and_different_data_or_services(campaign):
    folder, manifest = campaign
    limits = manifest["limits"]
    assert soak.valid_calibration(limits, manifest)
    assert not soak.valid_calibration(limits, dict(manifest, data_id="b" * 64))
    warmup = Path(limits["calibration_directory"])
    soak.append(warmup, sample(45))
    with pytest.raises(ValueError, match="采样失败"):
        soak.calibrate(warmup, folder / "invalid-limits.json")


@pytest.mark.parametrize("fault", ["hash", "escape", "date", "source", "blocking"])
def test_release_evidence_is_bound_to_content_scope_and_version(campaign, tmp_path, fault):
    folder, _ = campaign
    evidence = tmp_path / "result.txt"
    evidence.write_text("人工监督的验收记录", encoding="utf-8")
    row = dict(
        id="desktop_notepad",
        status="passed",
        confirmed_real=True,
        verified_by="test-owner",
        source=SOURCE,
        observed_at="2020-01-01T00:00:00+00:00",
        evidence="result.txt",
        sha256=hashlib.sha256(evidence.read_bytes()).hexdigest(),
    )
    manifest = dict(schema=1, scope="extended-local-agent", source=SOURCE, blocking_issues=[], cases=[row])
    if fault == "hash":
        row["sha256"] = "b" * 64
    elif fault == "escape":
        row["evidence"] = "../outside.txt"
    elif fault == "date":
        row["observed_at"] = "2099-01-01T00:00:00+00:00"
    elif fault == "source":
        row["source"] = "b" * 64
    else:
        manifest["blocking_issues"] = ["unresolved-data-loss"]
    path = tmp_path / "acceptance.json"
    path.write_text(json.dumps(manifest), encoding="utf-8")
    report = assess(path, folder, expected_source=SOURCE)
    assert not report["ready_for_human_review"] and not report["publishing_authorized"]
    if fault != "blocking":
        assert "desktop_notepad" not in report["accepted"]
    else:
        assert any("阻断项" in reason for reason in report["blockers"])


@pytest.mark.skipif(os.name != "nt", reason="Windows 资源采样后端")
def test_native_resource_sampling_does_not_accept_reused_pid():
    assert os.getpid() in process_parents()
    identity = inspect_process(os.getpid())
    result = sample_process(identity)
    assert result["private_bytes"] > 0 and result["handles"] > 0 and result["working_set"] > 0
    with pytest.raises(ProcessIdentityError):
        sample_process(ProcessIdentity(identity.pid, str(int(identity.created) + 1), identity.executable))


def test_source_fingerprint_excludes_data_and_tracks_code(tmp_path):
    source = tmp_path / "src/omni"
    source.mkdir(parents=True)
    (source / "a.py").write_text("a=1")
    before = source_fingerprint(tmp_path)
    (tmp_path / "config.yaml").write_text("password: secret")
    assert source_fingerprint(tmp_path) == before
    (source / "a.py").write_text("a=2")
    assert source_fingerprint(tmp_path) != before


def test_release_gate_never_confuses_templates_with_real_evidence(campaign, tmp_path):
    folder, _ = campaign
    soak.append(folder, sample(0))
    manifest = tmp_path / "acceptance.json"
    manifest.write_text(
        json.dumps(
            {
                "schema": 1,
                "scope": "extended-local-agent",
                "source": SOURCE,
                "cases": [{"id": name, "status": "pending"} for name in REQUIRED_CASES],
            }
        ),
        encoding="utf-8",
    )
    report = assess(manifest, folder, expected_source=SOURCE)
    assert not report["ready_for_human_review"] and not report["publishing_authorized"]
    assert len(report["accepted"]) == 0 and len(report["blockers"]) >= len(REQUIRED_CASES)


def test_real_v9_migration_rehearsal_only_changes_copy(tmp_path):
    import runpy

    rehearse = runpy.run_path(str(Path(__file__).resolve().parents[1] / "scripts/rehearse_upgrade.py"))["rehearse"]
    data = tmp_path / "live"
    data.mkdir()
    with sqlite3.connect(data / "state.db") as conn:
        for version in range(1, 10):
            conn.executescript(_MIGRATIONS[version])
        conn.execute("CREATE TABLE meta(key TEXT PRIMARY KEY,value TEXT NOT NULL)")
        conn.execute("INSERT INTO meta VALUES('schema_version','9')")
        conn.execute(
            "INSERT INTO jobs(name,prompt,every_minutes,updated_at) VALUES('canary','不能丢失的任务',30,'2026-10-27')"
        )
        conn.execute(
            "INSERT INTO executions(job,started_at,duration_s,ok,result) VALUES('canary','2026-10-27',1,1,'旧执行证据')"
        )
        conn.execute("INSERT INTO budgets(scope,day,used) VALUES('browser_write','2026-10-27',7)")
    memory = MemoryStore(data / "memory.db")
    memory.new_session("测试会话")
    memory.close()
    config = tmp_path / "config.yaml"
    config.write_text("{}", encoding="utf-8")
    state_hash = hashlib.sha256((data / "state.db").read_bytes()).hexdigest()
    report = rehearse(RuntimePaths.create(data, config_path=config, skills_root=tmp_path / "skills"), tmp_path / "copy")
    assert report["passed"] and report["original_schema"] == 9
    assert all(entry["schema"] == STATE_SCHEMA_VERSION for entry in report["opens"])
    state_tables = {entry["table"]: entry for entry in report["tables"] if entry["database"] == "state.db"}
    assert {"jobs", "executions", "budgets"} <= state_tables.keys()
    for table, entry in state_tables.items():
        expected = 1 if table in {"jobs", "executions", "budgets"} else 0
        assert entry["before_rows"] == entry["after_rows"] == expected, table
        assert entry["preserved"], table
    assert hashlib.sha256((data / "state.db").read_bytes()).hexdigest() == state_hash
