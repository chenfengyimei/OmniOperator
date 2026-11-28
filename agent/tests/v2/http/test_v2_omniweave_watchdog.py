"""Watchdog supervision: startup reconciliation barrier, bounded requeues, stall alerts and modes."""

import os
import subprocess
import sys
import threading
import time
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import text

from omni.adapters.persistence.lease_repository import LeaseRepositories
from omni.adapters.persistence.repository import Repositories
from omni.adapters.persistence.sqlalchemy_access import database_engine
from omni.adapters.persistence.sqlite_access import sqlite_connection
from omni.adapters.primitives import SystemClock, UUIDs
from omni.composition.http import http_app
from omni.domain.background_review import REVIEW_PROMPT
from omni.domain.contracts import Invocation, ModelReply

JOB_REQUEST = {
    "key": "assemble-1",
    "tool": "files_write",
    "arguments": {"path": "job.txt", "content": "job output"},
    "deadline_seconds": 120,
}


def _authorize(client, home):
    client.headers["Authorization"] = "Bearer " + (home / "run/owner.token").read_text(encoding="utf-8")
    return client.get("/api/v1/agents").json()["items"][0]["id"]


def _model_base(client, agent):
    base = f"/api/v1/agents/{agent}"
    settings = client.get(base + "/settings").json()
    url = base + "/configurations/" + settings["value"]["model_profile_id"]
    profile = client.get(url).json()
    response = client.put(
        url,
        json={
            "name": profile["name"],
            "enabled": True,
            "revision": profile["revision"],
            "value": {**profile["value"], "model": "fixture"},
        },
    )
    assert response.status_code == 200, response.text
    return base


def _select_main(client, base):
    selection = client.put(base + "/omniweave/main-session", json={"session_id": None})
    assert selection.status_code == 200, selection.text
    return selection.json()["session_id"]


def _rows(home, query, args=()):
    with sqlite_connection(home / "data/omni.db") as connection:
        return connection.execute(query, args).fetchall()


def _wait_rows(home, query, args=(), expected=None):
    rows = []
    for _ in range(400):
        rows = _rows(home, query, args)
        if rows and (expected is None or list(rows) == list(expected)):
            return rows
        time.sleep(0.025)
    raise AssertionError(f"never matched {expected or 'any row'}: {rows}")


def _wait_tasks(client, base, receipt, expected):
    view = None
    for _ in range(400):
        response = client.get(
            base + f"/omniweave/sessions/{receipt['session_id']}/tickets", params={"run_id": receipt["run_id"]}
        )
        assert response.status_code == 200, response.text
        view = response.json()
        if {task["key"]: task["state"] for task in view["tasks"]} == expected:
            return view
        time.sleep(0.025)
    raise AssertionError(view)


def _submit(client, base, home):
    session_id = _select_main(client, base)
    receipt = client.post(
        base + "/turns", json={"key": "jobs", "session_id": session_id, "message": "Run a managed job"}
    ).json()
    _wait_tasks(client, base, receipt, {"build": "waiting_external"})
    return receipt


class JobFlowModel:
    """Plans one coding task with jobs enabled; the worker submits one managed job."""

    def __init__(self):
        self.receipts: list[str] = []

    async def complete(self, messages, tools, settings):
        if messages[0]["content"] == REVIEW_PROMPT:
            return ModelReply("No reusable procedure in this test turn", cost_micro=0)
        if str(messages[0]["content"]).startswith("OMNI_CONTEXT_SUMMARY_V1"):
            return ModelReply("Managed job scenario", cost_micro=0)
        current = str(next(row["content"] for row in reversed(messages) if row["role"] == "user"))
        last = messages[-1]
        if current.startswith("OmniWeave Worker."):
            if last["role"] == "tool":
                return ModelReply("Job submitted; ending at a safe boundary", cost_micro=0)
            return ModelReply("Submit", (Invocation("job", "owa_job_start", dict(JOB_REQUEST)),), cost_micro=0)
        if last["role"] == "tool":
            return ModelReply("Plan queued", cost_micro=0)
        tasks = [
            {
                "key": "build",
                "goal": "submit job",
                "profile": "coding",
                "tools": ["files_write"],
                "max_calls": 4,
                "jobs": True,
            }
        ]
        return ModelReply("Plan", (Invocation("plan", "owa_plan", {"tasks": tasks}),), cost_micro=0)


def _write(home, statements):
    engine = database_engine(home / "data/omni.db")
    try:
        with engine.connect() as connection:
            connection.info["omni_write"] = True
            for statement, args in statements:
                connection.execute(text(statement), args)
            connection.commit()
    finally:
        engine.dispose()


def _fail_run(home, run_id):
    """Simulate an executor that died mid-flight while the job is still live."""
    _write(
        home,
        [
            (
                "UPDATE runs SET state='failed', error_code='executor_gone', ended_at=:ended WHERE id=:run",
                {"ended": datetime.now(timezone.utc).isoformat(), "run": run_id},
            ),
            ("UPDATE work_items SET state='failed' WHERE run_id=:run AND kind='turn'", {"run": run_id}),
        ],
    )


def _age_run(home, run_id, seconds):
    """Shift the run's recorded activity backwards to age backoff and stall timers."""
    shifted = (datetime.now(timezone.utc) - timedelta(seconds=seconds)).isoformat()
    engine = database_engine(home / "data/omni.db")
    try:
        with engine.connect() as connection:
            connection.info["omni_write"] = True
            rows = connection.execute(
                text("SELECT sequence, created_at FROM run_events WHERE run_id=:run"), {"run": run_id}
            ).all()
            for sequence, created in rows:
                moved = (datetime.fromisoformat(str(created)) - timedelta(seconds=seconds)).isoformat()
                connection.execute(
                    text("UPDATE run_events SET created_at=:moved WHERE sequence=:seq"),
                    {"moved": moved, "seq": sequence},
                )
            connection.execute(
                text("UPDATE runs SET ended_at=:ended WHERE id=:run AND ended_at IS NOT NULL"),
                {"ended": shifted, "run": run_id},
            )
            connection.commit()
    finally:
        engine.dispose()


def _scan(home, boot="watchdog-probe"):
    """Run exactly one supervision pass in its own committed transaction."""
    engine = database_engine(home / "data/omni.db")
    try:
        with engine.connect() as connection:
            connection.info["omni_write"] = True
            unit = Repositories(connection, SystemClock(), UUIDs(), boot)
            result = unit.omniweave_watchdog_scan()
            connection.commit()
            return result
    finally:
        engine.dispose()


def _crash_home(home, phase):
    """Produce a crashed-executor home with the jobs suite's real fault injection."""
    jobs_file = Path(__file__).resolve().parent / "test_v2_omniweave_jobs.py"
    launcher = (
        "import runpy,sys; sys.path.insert(0,sys.argv.pop(1)); runpy.run_path(sys.argv.pop(1),run_name='__main__')"
    )
    command = [sys.executable, "-c", launcher, str(jobs_file.parents[2]), str(jobs_file), str(home)]
    child = subprocess.run(
        [*command, phase], capture_output=True, timeout=120, check=False, env={**os.environ, "OMNI_TEST_OFFLINE": "1"}
    )
    assert child.returncode == 78, child.stderr.decode(errors="replace")


def test_startup_reconciles_owa_before_opening_claims(tmp_path):
    """Claims stay closed until initialize/recover and the OWA reconciliation finish."""
    model = JobFlowModel()
    original_claim = LeaseRepositories.claim_runs

    def blocked_job_claims(self, limit, *, include_owa_deliveries=False, include_owa_jobs=True):
        return original_claim(self, limit, include_owa_deliveries=include_owa_deliveries, include_owa_jobs=False)

    with pytest.MonkeyPatch.context() as patch:
        patch.setattr(LeaseRepositories, "claim_runs", blocked_job_claims)
        with TestClient(http_app(tmp_path, model=model, omniweave_mode="dispatch")) as client:
            agent = _authorize(client, tmp_path)
            base = _model_base(client, agent)
            receipt = _submit(client, base, tmp_path)
            view = _wait_tasks(client, base, receipt, {"build": "waiting_external"})
            job_id = view["tasks"][0]["job"]["job_id"]
            [(run_id,)] = _rows(tmp_path, "SELECT run_id FROM owa_jobs WHERE id=?", (job_id,))
    assert _rows(tmp_path, "SELECT state FROM runs WHERE id=?", (run_id,)) == [("queued",)]
    assert _rows(tmp_path, "SELECT state FROM work_items WHERE run_id=? AND kind='turn'", (run_id,)) == [("pending",)]

    entered = threading.Event()
    release = threading.Event()
    ready = threading.Event()
    finish = threading.Event()
    original_recover = Repositories.omniweave_job_recover

    def gated_recover(self):
        entered.set()
        assert release.wait(timeout=20), "reconciliation gate leaked"
        return original_recover(self)

    Repositories.omniweave_job_recover = gated_recover
    try:

        def boot():
            with TestClient(http_app(tmp_path, model=model, omniweave_mode="dispatch")):
                ready.set()
                assert finish.wait(timeout=30), "shutdown gate leaked"

        thread = threading.Thread(target=boot)
        thread.start()
        assert entered.wait(timeout=20), "reconciliation never started"
        # The reconciliation gate is held: the queued job must not be claimed.
        assert _rows(tmp_path, "SELECT state FROM runs WHERE id=?", (run_id,)) == [("queued",)]
        assert _rows(tmp_path, "SELECT state FROM work_items WHERE run_id=? AND kind='turn'", (run_id,)) == [
            ("pending",)
        ]
        assert _rows(tmp_path, "SELECT count(*) FROM leases WHERE run_id=? AND state='held'", (run_id,)) == [(0,)]
        release.set()
        assert ready.wait(timeout=30), "startup never finished"
        _wait_rows(tmp_path, "SELECT state FROM owa_jobs WHERE id=?", (job_id,), expected=[("running",)])
    finally:
        Repositories.omniweave_job_recover = original_recover
        release.set()
        finish.set()
        thread.join(timeout=30)


def test_watchdog_scan_requeues_proven_undispatched_failures_with_backoff(tmp_path):
    """The requeue ladder: 5s backoff, 30s backoff, then an exhausted recorded receipt."""
    model = JobFlowModel()
    original_claim = LeaseRepositories.claim_runs

    def blocked_job_claims(self, limit, *, include_owa_deliveries=False, include_owa_jobs=True):
        return original_claim(self, limit, include_owa_deliveries=include_owa_deliveries, include_owa_jobs=False)

    with pytest.MonkeyPatch.context() as patch:
        patch.setattr(LeaseRepositories, "claim_runs", blocked_job_claims)
        with TestClient(http_app(tmp_path, model=model, omniweave_mode="dispatch")) as client:
            agent = _authorize(client, tmp_path)
            base = _model_base(client, agent)
            receipt = _submit(client, base, tmp_path)
            view = _wait_tasks(client, base, receipt, {"build": "waiting_external"})
            job_id = view["tasks"][0]["job"]["job_id"]
            [(run_id,)] = _rows(tmp_path, "SELECT run_id FROM owa_jobs WHERE id=?", (job_id,))
    assert _rows(tmp_path, "SELECT state FROM runs WHERE id=?", (run_id,)) == [("queued",)]

    _fail_run(tmp_path, run_id)
    # A fresh failure waits out the first 5s backoff before anything moves.
    assert _scan(tmp_path) == {"alerts": [], "requeues": [], "exhausted": []}
    assert _rows(tmp_path, "SELECT state FROM runs WHERE id=?", (run_id,)) == [("failed",)]
    _age_run(tmp_path, run_id, seconds=6)
    assert _scan(tmp_path)["requeues"] == [job_id]
    assert _rows(tmp_path, "SELECT state FROM runs WHERE id=?", (run_id,)) == [("queued",)]
    assert _rows(tmp_path, "SELECT state FROM work_items WHERE run_id=? AND kind='turn'", (run_id,)) == [("pending",)]
    assert _rows(tmp_path, "SELECT resumes FROM owa_jobs WHERE id=?", (job_id,)) == [(1,)]
    assert _rows(
        tmp_path,
        "SELECT count(*) FROM run_events WHERE run_id=? AND kind='run.resume_queued' AND json_extract(data,'$.watchdog')",
        (run_id,),
    ) == [(1,)]
    assert _rows(tmp_path, "SELECT count(*) FROM audit WHERE kind='owa_job.watchdog_requeued'") == [(1,)]

    _fail_run(tmp_path, run_id)
    # The second attempt waits for the 30s backoff measured from the last attempt.
    assert _scan(tmp_path)["requeues"] == []
    _age_run(tmp_path, run_id, seconds=31)
    assert _scan(tmp_path)["requeues"] == [job_id]
    assert _rows(tmp_path, "SELECT resumes FROM owa_jobs WHERE id=?", (job_id,)) == [(2,)]

    _fail_run(tmp_path, run_id)
    assert _scan(tmp_path)["exhausted"] == [job_id]
    assert _rows(tmp_path, "SELECT state, error_code FROM owa_jobs WHERE id=?", (job_id,)) == [
        ("interrupted", "owa_job_watchdog_exhausted")
    ]
    assert _rows(tmp_path, "SELECT json_extract(receipt,'$.outcome') FROM owa_jobs WHERE id=?", (job_id,)) == [
        ("interrupted",)
    ]
    assert _rows(tmp_path, "SELECT resumes FROM owa_jobs WHERE id=?", (job_id,)) == [(2,)]
    _age_run(tmp_path, run_id, seconds=60)
    assert _scan(tmp_path) == {"alerts": [], "requeues": [], "exhausted": []}


def test_watchdog_scan_skips_unresolved_side_effects(tmp_path):
    """Executing or unknown actions block every automatic move; review resolves them."""
    home = tmp_path / "home"
    _crash_home(home, "job-dispatch")
    [(job_id, run_id)] = _rows(home, "SELECT id, run_id FROM owa_jobs")
    assert _rows(home, "SELECT count(*) FROM actions WHERE run_id=? AND state='executing'", (run_id,)) == [(1,)]
    _fail_run(home, run_id)

    assert _scan(home) == {"alerts": [], "requeues": [], "exhausted": []}
    assert _rows(home, "SELECT state FROM owa_jobs WHERE id=?", (job_id,)) == [("running",)]
    assert _rows(home, "SELECT resumes FROM owa_jobs WHERE id=?", (job_id,)) == [(0,)]
    assert _rows(home, "SELECT count(*) FROM run_events WHERE run_id=? AND kind='run.resume_queued'", (run_id,)) == [
        (0,)
    ]

    _write(home, [("UPDATE actions SET state='unknown' WHERE run_id=:run", {"run": run_id})])
    assert _scan(home) == {"alerts": [], "requeues": [], "exhausted": []}
    assert _rows(home, "SELECT state FROM owa_jobs WHERE id=?", (job_id,)) == [("running",)]


def test_watchdog_scan_converges_cancel_requested_job_without_executor(tmp_path):
    """A cancel that was requested before the executor died settles as cancelled."""
    model = JobFlowModel()
    original_claim = LeaseRepositories.claim_runs

    def blocked_job_claims(self, limit, *, include_owa_deliveries=False, include_owa_jobs=True):
        return original_claim(self, limit, include_owa_deliveries=include_owa_deliveries, include_owa_jobs=False)

    with pytest.MonkeyPatch.context() as patch:
        patch.setattr(LeaseRepositories, "claim_runs", blocked_job_claims)
        with TestClient(http_app(tmp_path, model=model, omniweave_mode="dispatch")) as client:
            agent = _authorize(client, tmp_path)
            base = _model_base(client, agent)
            receipt = _submit(client, base, tmp_path)
            view = _wait_tasks(client, base, receipt, {"build": "waiting_external"})
            job_id = view["tasks"][0]["job"]["job_id"]
            [(run_id,)] = _rows(tmp_path, "SELECT run_id FROM owa_jobs WHERE id=?", (job_id,))

    _fail_run(tmp_path, run_id)
    _write(
        tmp_path,
        [
            (
                "UPDATE owa_jobs SET state='cancelling', cancel_requested_at=:now WHERE id=:job",
                {"now": datetime.now(timezone.utc).isoformat(), "job": job_id},
            ),
        ],
    )
    assert _scan(tmp_path)["requeues"] == []
    assert _rows(tmp_path, "SELECT state, error_code FROM owa_jobs WHERE id=?", (job_id,)) == [
        ("cancelled", "owa_job_cancelled")
    ]
    assert _rows(tmp_path, "SELECT json_extract(receipt,'$.outcome') FROM owa_jobs WHERE id=?", (job_id,)) == [
        ("cancelled",)
    ]
    assert _scan(tmp_path) == {"alerts": [], "requeues": [], "exhausted": []}


def test_watchdog_scan_alerts_stalled_running_jobs_only_once(tmp_path):
    """Running jobs idle past the stall threshold are alerted once per episode; approval waits are not."""
    model = JobFlowModel()
    original_claim = LeaseRepositories.claim_runs

    def blocked_job_claims(self, limit, *, include_owa_deliveries=False, include_owa_jobs=True):
        return original_claim(self, limit, include_owa_deliveries=include_owa_deliveries, include_owa_jobs=False)

    with pytest.MonkeyPatch.context() as patch:
        patch.setattr(LeaseRepositories, "claim_runs", blocked_job_claims)
        with TestClient(http_app(tmp_path, model=model, omniweave_mode="dispatch")) as client:
            agent = _authorize(client, tmp_path)
            base = _model_base(client, agent)
            receipt = _submit(client, base, tmp_path)
            view = _wait_tasks(client, base, receipt, {"build": "waiting_external"})
            job_id = view["tasks"][0]["job"]["job_id"]
            [(run_id,)] = _rows(tmp_path, "SELECT run_id FROM owa_jobs WHERE id=?", (job_id,))
            [(job_agent,)] = _rows(tmp_path, "SELECT agent_id FROM owa_jobs WHERE id=?", (job_id,))

    _age_run(tmp_path, run_id, seconds=400)
    _write(tmp_path, [("UPDATE runs SET state='waiting_approval' WHERE id=:run", {"run": run_id})])
    assert _scan(tmp_path)["alerts"] == []

    _write(tmp_path, [("UPDATE runs SET state='running' WHERE id=:run", {"run": run_id})])
    assert _scan(tmp_path)["alerts"] == [job_id]
    assert _rows(
        tmp_path, "SELECT count(*) FROM run_events WHERE run_id=? AND kind='run.watchdog_stalled'", (run_id,)
    ) == [(1,)]
    assert _rows(tmp_path, "SELECT count(*) FROM audit WHERE kind='owa_job.watchdog_stalled'") == [(1,)]
    assert _scan(tmp_path)["alerts"] == []
    assert _rows(
        tmp_path, "SELECT count(*) FROM run_events WHERE run_id=? AND kind='run.watchdog_stalled'", (run_id,)
    ) == [(1,)]
    assert job_agent


def test_watchdog_loop_requeues_and_dispatcher_reclaims(tmp_path, monkeypatch):
    """The real loop requeues inside the injected backoff; the dispatcher then reclaims the run."""
    monkeypatch.setattr("omni.application.watchdog.WATCHDOG_SCAN_S", 0.05)
    monkeypatch.setattr("omni.adapters.persistence.omniweave_jobs.WATCHDOG_RETRY_DELAYS_S", (0.05, 0.1))
    original_claim = LeaseRepositories.claim_runs

    def blocked_job_claims(self, limit, *, include_owa_deliveries=False, include_owa_jobs=True):
        return original_claim(self, limit, include_owa_deliveries=include_owa_deliveries, include_owa_jobs=False)

    monkeypatch.setattr(LeaseRepositories, "claim_runs", blocked_job_claims)
    model = JobFlowModel()
    with TestClient(http_app(tmp_path, model=model, omniweave_mode="dispatch")) as client:
        agent = _authorize(client, tmp_path)
        base = _model_base(client, agent)
        receipt = _submit(client, base, tmp_path)
        view = _wait_tasks(client, base, receipt, {"build": "waiting_external"})
        job_id = view["tasks"][0]["job"]["job_id"]
        [(run_id,)] = _rows(tmp_path, "SELECT run_id FROM owa_jobs WHERE id=?", (job_id,))
        _fail_run(tmp_path, run_id)
        _wait_rows(tmp_path, "SELECT state, resumes FROM owa_jobs WHERE id=?", (job_id,), expected=[("queued", 1)])
        assert _rows(tmp_path, "SELECT state FROM runs WHERE id=?", (run_id,)) == [("queued",)]
        assert _rows(tmp_path, "SELECT state FROM work_items WHERE run_id=? AND kind='turn'", (run_id,)) == [
            ("pending",)
        ]
        # Reopen claiming: the same run is reclaimed and the job dispatches again.
        monkeypatch.setattr(LeaseRepositories, "claim_runs", original_claim)
        _wait_rows(tmp_path, "SELECT state FROM owa_jobs WHERE id=?", (job_id,), expected=[("running",)])
        assert _rows(
            tmp_path, "SELECT count(*) FROM run_events WHERE run_id=? AND kind='run.resume_queued'", (run_id,)
        ) == [(1,)]


def test_watchdog_loop_only_runs_in_dispatch_mode(tmp_path, monkeypatch):
    """Observe and off modes never start the supervision loop; dispatch does."""
    calls: list[int] = []
    original_scan = Repositories.omniweave_watchdog_scan

    def recording_scan(self):
        calls.append(1)
        return original_scan(self)

    monkeypatch.setattr(Repositories, "omniweave_watchdog_scan", recording_scan)
    model = JobFlowModel()
    with TestClient(http_app(tmp_path / "observe", model=model, omniweave_mode="observe")) as client:
        _authorize(client, tmp_path / "observe")
        time.sleep(0.3)
    with TestClient(http_app(tmp_path / "off", model=model, omniweave_mode="off")) as client:
        _authorize(client, tmp_path / "off")
        time.sleep(0.3)
    assert calls == []

    monkeypatch.setattr("omni.application.watchdog.WATCHDOG_SCAN_S", 0.05)
    with TestClient(http_app(tmp_path / "dispatch", model=model, omniweave_mode="dispatch")) as client:
        _authorize(client, tmp_path / "dispatch")
        deadline = 200
        while deadline and not calls:
            time.sleep(0.025)
            deadline -= 1
    assert calls, "dispatch mode never started the supervision loop"
