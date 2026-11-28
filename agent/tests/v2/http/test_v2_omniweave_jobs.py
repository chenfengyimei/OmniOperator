"""Managed long jobs: stable ids, fenced receipts, idempotent cancellation and crash recovery."""

import os
import subprocess
import sys
import threading
import time
import uuid
from pathlib import Path

import pytest
from fastapi.testclient import TestClient
from omni_test_support.v2.v2_action_review_helpers import review_request
from sqlalchemy import select

from omni.adapters.persistence import schema as s
from omni.adapters.persistence.lease_repository import LeaseRepositories
from omni.adapters.persistence.repository import Repositories
from omni.adapters.persistence.sqlite_access import sqlite_connection
from omni.adapters.primitives import SystemClock, UUIDs
from omni.composition.http import http_app
from omni.domain.background_review import REVIEW_PROMPT
from omni.domain.contracts import BusinessError, Invocation, ModelReply
from omni.domain.routing import POLICY_DEFAULT

CRASH = 78
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
    assert selection.json()["enabled"] is True
    return selection.json()["session_id"]


def _rows(home, query, args=()):
    with sqlite_connection(home / "data/omni.db") as connection:
        return connection.execute(query, args).fetchall()


def _wait_approval(client, base):
    for _ in range(400):
        approvals = client.get(base + "/approvals").json()["items"]
        if approvals:
            return approvals
        time.sleep(0.025)
    raise AssertionError("No approval appeared")


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


def _wait_job(home, query, args=()):
    rows = []
    for _ in range(400):
        rows = _rows(home, query, args)
        if rows:
            return rows
        time.sleep(0.025)
    raise AssertionError(
        f"job never matched: {query} {rows} "
        f"states={_rows(home, 'SELECT id, state, error_code FROM owa_jobs')} "
        f"runs={_rows(home, 'SELECT id, state, error_code FROM runs ORDER BY created_at')} "
        f"work={_rows(home, 'SELECT run_id, state, claim_token IS NOT NULL, payload FROM work_items')} "
        f"audit={_rows(home, 'SELECT kind FROM audit ORDER BY rowid DESC LIMIT 12')}"
    )


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
        if current == "Status check please":
            # A plain main-conversation turn on another Agent must complete while a
            # managed job holds its execution slot.
            return ModelReply("All systems nominal", cost_micro=0)
        if current.startswith("OmniWeave Worker. Continue"):
            assert '"job.receipt"' in current, current[:200]
            self.receipts.append(current)
            return ModelReply("Receipt processed", cost_micro=0)
        if current.startswith("OmniWeave Worker."):
            assert "owa_job_start" in {tool.name for tool in tools}
            if last["role"] == "tool":
                print("JOB TOOL RESULT:", str(last["content"])[:400])
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


def test_job_runs_detached_and_delivers_receipt_followup(tmp_path):
    model = JobFlowModel()
    with TestClient(http_app(tmp_path, model=model, omniweave_mode="dispatch")) as client:
        base = _model_base(client, _authorize(client, tmp_path))
        receipt = _submit(client, base, tmp_path)
        [(job_id, run_id)] = _wait_job(tmp_path, "SELECT id, run_id FROM owa_jobs WHERE state='running'")
        [(anchor,)] = _rows(tmp_path, "SELECT run_id FROM owa_tasks WHERE task_key='build'")
        assert (tmp_path / "owa-workspaces" / anchor).is_dir()
        [(job_action,)] = _wait_job(tmp_path, "SELECT id FROM actions WHERE run_id=?", (run_id,))
        [approval] = _wait_approval(client, base)
        assert approval["action_id"] == job_action
        allowed = client.post(
            base + f"/approvals/{approval['id']}",
            json={"allowed": True, "policy_revision": approval["policy_revision"]},
        )
        assert allowed.status_code == 200, allowed.text
        _wait_job(tmp_path, "SELECT id FROM owa_jobs WHERE state='succeeded'")
        assert (tmp_path / "owa-workspaces" / anchor / "job.txt").read_text(encoding="utf-8") == "job output"
        view = _wait_tasks(client, base, receipt, {"build": "succeeded"})
        assert view["tasks"][0]["job"]["job_id"] == job_id
        assert view["tasks"][0]["job"]["state"] == "succeeded"
        _wait_job(
            tmp_path, "SELECT followup_run_id FROM owa_jobs WHERE id=? AND followup_run_id IS NOT NULL", (job_id,)
        )
        deadline = 400
        while deadline and not model.receipts:
            time.sleep(0.025)
            deadline -= 1
        assert model.receipts and '"state": "succeeded"' in model.receipts[0]
        attempts: list = []
        for _ in range(400):
            attempts = _rows(
                tmp_path,
                "SELECT r.state, a.attempt_no FROM owa_task_attempts a JOIN runs r ON r.id=a.run_id ORDER BY a.attempt_no",
            )
            if attempts == [("succeeded", 1), ("succeeded", 2)]:
                break
            time.sleep(0.025)
        assert attempts == [("succeeded", 1), ("succeeded", 2)], attempts
        assert _rows(tmp_path, "SELECT state FROM leases WHERE run_id=? AND state='held'", (run_id,)) == []


def test_job_cancel_is_idempotent_and_rejects_stale_fence(tmp_path):
    model = JobFlowModel()
    with TestClient(http_app(tmp_path, model=model, omniweave_mode="dispatch")) as client:
        agent = _authorize(client, tmp_path)
        base = _model_base(client, agent)
        receipt = _submit(client, base, tmp_path)
        [(job_id, run_id, token, fence)] = _wait_job(
            tmp_path,
            "SELECT owa_jobs.id, owa_jobs.run_id, work_items.claim_token, work_items.lease_fence "
            "FROM owa_jobs JOIN work_items ON work_items.run_id=owa_jobs.run_id WHERE owa_jobs.state='running'",
        )
        assert receipt["run_id"]
        from omni.adapters.persistence.sqlalchemy_access import database_engine

        engine = database_engine(tmp_path / "data/omni.db")
        try:
            with engine.connect() as connection:
                connection.info["omni_write"] = True
                unit = Repositories(connection, SystemClock(), UUIDs(), "stale-executor-boot")
                with pytest.raises(BusinessError, match="stale executor cannot settle") as stale:
                    unit.omniweave_job_settle(agent, run_id, token, int(fence) + 1, "succeeded", "", {"status": "ok"})
                assert stale.value.code == "owa_job_fence_stale"
                with pytest.raises(BusinessError, match="stale executor cannot settle") as stale_defer:
                    unit.omniweave_job_defer(agent, run_id, token, int(fence) + 1)
                assert stale_defer.value.code == "owa_job_fence_stale"
        finally:
            engine.dispose()
        first = client.post(base + f"/runs/{run_id}/cancel")
        assert first.status_code == 200 and first.json()["accepted"]
        [(state, error, cancelled)] = _wait_job(
            tmp_path,
            "SELECT state, error_code, cancel_requested_at IS NOT NULL FROM owa_jobs WHERE id=? AND state='cancelled'",
            (job_id,),
        )
        assert (state, error, cancelled) == ("cancelled", "owa_job_cancelled", True)
        assert _rows(tmp_path, "SELECT state FROM runs WHERE id=?", (run_id,)) == [("cancelled",)]
        second = client.post(base + f"/runs/{run_id}/cancel")
        assert second.status_code == 200
        assert _rows(tmp_path, "SELECT state, error_code FROM owa_jobs WHERE id=?", (job_id,)) == [
            ("cancelled", "owa_job_cancelled")
        ]
        assert _rows(tmp_path, "SELECT count(*) FROM actions WHERE run_id=? AND state='ok'", (run_id,)) == [(0,)]
        assert _rows(tmp_path, "SELECT state FROM leases WHERE run_id=? AND state='held'", (run_id,)) == []
        assert _rows(tmp_path, "SELECT count(*) FROM messages WHERE content LIKE '%job output%'") == [(0,)]
        with sqlite_connection(tmp_path / "data/omni.db") as connection:
            [receipt_outcome] = connection.execute(
                "SELECT json_extract(receipt,'$.outcome') FROM owa_jobs WHERE id=?", (job_id,)
            ).fetchone()
        assert receipt_outcome == "cancelled"
        engine = database_engine(tmp_path / "data/omni.db")
        try:
            with engine.connect() as connection:
                connection.info["omni_write"] = True
                unit = Repositories(connection, SystemClock(), UUIDs(), "late-executor-boot")
                settled = unit.omniweave_job_settle(agent, run_id, token, fence, "succeeded", "", {"status": "ok"})
                assert settled["state"] == "cancelled"
                assert _rows(tmp_path, "SELECT state FROM owa_jobs WHERE id=?", (job_id,)) == [("cancelled",)]
        finally:
            engine.dispose()


def crash_at(phase):
    if phase == "job-claim":
        original = Repositories.start_claimed_run

        def wrapped(self, agent_id, run_id, token, fence):
            result = original(self, agent_id, run_id, token, fence)
            if (
                self.connection.execute(select(s.owa_jobs.c.id).where(s.owa_jobs.c.run_id == run_id)).first()
                is not None
            ):
                # The claim was validated but nothing was dispatched yet.
                os._exit(CRASH)
            return result

        Repositories.start_claimed_run = wrapped
    elif phase == "job-settle":

        def wrapped(self, agent_id, run_id, token, fence, outcome, error_code, receipt):
            # The action settle already committed in its own transaction; the job
            # receipt transaction never starts.
            os._exit(CRASH)

        Repositories.omniweave_job_settle = wrapped
    elif phase == "job-finish":
        original = Repositories.finish

        def wrapped(self, agent_id, run_id, text, delivery=None):
            state = self.connection.execute(
                select(s.owa_jobs.c.state).where(s.owa_jobs.c.run_id == run_id)
            ).scalar_one_or_none()
            if state == "succeeded":
                # The receipt has been written but the Run has not reached its terminal state.
                os._exit(CRASH)
            return original(self, agent_id, run_id, text, delivery)

        Repositories.finish = wrapped
    elif phase == "job-dispatch":
        original = Repositories.settle_action

        def wrapped(self, agent_id, action_id, result):
            row = self.connection.execute(select(s.actions.c.tool).where(s.actions.c.id == action_id)).first()
            if row is not None and row.tool == "files_write":
                # The executor dispatched the write; its receipt is lost in flight.
                os._exit(CRASH)
            return original(self, agent_id, action_id, result)

        Repositories.settle_action = wrapped
    else:
        raise AssertionError(f"Unknown crash phase {phase}")


def _crash_child(home, phase, timeout):
    launcher = (
        "import runpy,sys; sys.path.insert(0,sys.argv.pop(1)); runpy.run_path(sys.argv.pop(1),run_name='__main__')"
    )
    command = [sys.executable, "-c", launcher, str(Path(__file__).resolve().parents[2]), __file__, str(home)]
    child = subprocess.run(
        [*command, phase],
        capture_output=True,
        timeout=timeout,
        check=False,
        env={**os.environ, "OMNI_TEST_OFFLINE": "1"},
    )
    assert child.returncode == CRASH, child.stderr.decode(errors="replace")


def test_job_crash_before_dispatch_requeues_and_completes_once(tmp_path):
    home = tmp_path / "home"
    _crash_child(home, "job-claim", 120)
    model = JobFlowModel()
    with TestClient(http_app(home, model=model, omniweave_mode="dispatch")) as client:
        base = _model_base(client, _authorize(client, home))
        # Recovery re-dispatches the claimed-but-undispatched job; the fresh write needs approval again.
        [approval] = _wait_approval(client, base)
        client.post(
            base + f"/approvals/{approval['id']}",
            json={"allowed": True, "policy_revision": approval["policy_revision"]},
        )
        [(job_id, run_id)] = _wait_job(home, "SELECT id, run_id FROM owa_jobs WHERE state='succeeded'")
        assert _rows(home, "SELECT resumes FROM owa_jobs WHERE id=?", (job_id,)) == [(0,)]
        _wait_job(home, "SELECT followup_run_id FROM owa_jobs WHERE id=? AND followup_run_id IS NOT NULL", (job_id,))
        deadline = 400
        while deadline and not model.receipts:
            time.sleep(0.025)
            deadline -= 1
        assert model.receipts
        assert _rows(home, "SELECT count(*) FROM actions WHERE tool='files_write'") == [(1,)]
        assert _rows(home, "SELECT state, effect FROM actions WHERE tool='files_write'") == [("ok", "applied")]
        workspaces = list((home / "owa-workspaces").rglob("job.txt"))
        assert len(workspaces) == 1 and workspaces[0].read_text(encoding="utf-8") == "job output"
        assert _rows(home, "SELECT state FROM leases WHERE run_id=? AND state='held'", (run_id,)) == []


@pytest.mark.parametrize("phase", ["job-settle", "job-finish"])
def test_job_crash_after_settle_replays_recorded_receipt_exactly_once(tmp_path, phase):
    home = tmp_path / "home"
    _crash_child(home, phase, 120)
    assert _rows(home, "SELECT count(*) FROM actions WHERE tool='files_write' AND state='ok'") == [(1,)]
    assert _rows(
        home,
        "SELECT j.state, r.state, j.receipt IS NULL FROM owa_jobs j JOIN runs r ON r.id=j.run_id",
    ) == [("running", "running", 1)]
    model = JobFlowModel()
    with TestClient(http_app(home, model=model, omniweave_mode="dispatch")) as client:
        _model_base(client, _authorize(client, home))
        [(job_id, state)] = _wait_job(home, "SELECT id, state FROM owa_jobs WHERE state!='queued' AND state!='running'")
        assert state == "succeeded"
        assert _rows(home, "SELECT resumes FROM owa_jobs WHERE id=?", (job_id,)) == [(1,)]
        assert _rows(home, "SELECT count(*) FROM run_events WHERE kind='run.resume_queued'") == [(1,)]
        _wait_job(home, "SELECT followup_run_id FROM owa_jobs WHERE id=? AND followup_run_id IS NOT NULL", (job_id,))
        deadline = 400
        while deadline and not model.receipts:
            time.sleep(0.025)
            deadline -= 1
        assert model.receipts
        assert _rows(home, "SELECT count(*) FROM actions WHERE tool='files_write'") == [(1,)]
        workspaces = list((home / "owa-workspaces").rglob("job.txt"))
        assert len(workspaces) == 1 and workspaces[0].read_text(encoding="utf-8") == "job output"


def test_job_crash_mid_dispatch_keeps_unknown_until_review(tmp_path):
    home = tmp_path / "home"
    _crash_child(home, "job-dispatch", 120)
    [(job_id, run_id)] = _rows(home, "SELECT id, run_id FROM owa_jobs")
    assert _rows(home, "SELECT count(*) FROM actions WHERE tool='files_write' AND state='executing'") == [(1,)]
    assert list((home / "owa-workspaces").rglob("job.txt"))
    model = JobFlowModel()
    with TestClient(http_app(home, model=model, omniweave_mode="dispatch")) as client:
        base = _model_base(client, _authorize(client, home))
        [(state, error)] = _wait_job(home, "SELECT state, error_code FROM owa_jobs WHERE id=?", (job_id,))
        assert (state, error) == ("interrupted", "process_interrupted"), (state, error)
        assert _rows(home, "SELECT state FROM runs WHERE id=?", (run_id,)) == [("interrupted",)]
        assert _rows(home, "SELECT state FROM leases WHERE run_id=? AND state='held'", (run_id,)) == []
        [unknown] = _rows(home, "SELECT id FROM actions WHERE state='unknown' AND tool='files_write'")
        # 核查前零重放：未知结果保留在原账本，作业不重派、不产生第二个动作。
        for _ in range(200):
            assert _rows(home, "SELECT count(*) FROM actions WHERE tool='files_write'") == [(1,)]
            assert model.receipts == []
            time.sleep(0.025)
        review_url = base + f"/actions/{unknown[0]}/review"
        detail = client.get(review_url).json()
        assert detail["eligible"] is True, detail
        confirmed = client.post(review_url, json=review_request(detail))
        assert confirmed.status_code == 200 and confirmed.json()["resolved"], confirmed.text
        assert _rows(home, "SELECT count(*) FROM actions WHERE tool='files_write'") == [(1,)]
        assert _rows(home, "SELECT state FROM owa_jobs WHERE id=?", (job_id,)) == [("interrupted",)]
        assert model.receipts == []


def test_job_public_status_and_cancel_by_job_id(tmp_path):
    """The owner path works with only the public job identity from the ticket view."""
    model = JobFlowModel()
    with TestClient(http_app(tmp_path, model=model, omniweave_mode="dispatch")) as client:
        agent = _authorize(client, tmp_path)
        base = _model_base(client, agent)
        receipt = _submit(client, base, tmp_path)
        view = _wait_tasks(client, base, receipt, {"build": "waiting_external"})
        job_id = view["tasks"][0]["job"]["job_id"]
        # waiting_external only proves the job was submitted; phase "executing"
        # additionally requires the detached Run to be claimed, so wait for it.
        _wait_job(tmp_path, "SELECT id FROM owa_jobs WHERE id=? AND state='running'", (job_id,))
        detail = client.get(base + f"/omniweave/jobs/{job_id}")
        assert detail.status_code == 200, detail.text
        body = detail.json()
        assert body["job_id"] == job_id and body["state"] == "running"
        assert body["phase"] == "executing" and body["cancel_requested"] is False
        assert body["goal_superseded"] is False and body["current_goal_version"] == 1
        assert "run_id" not in body  # the public identity must not leak the internal Run id

        cancelled = client.post(base + f"/omniweave/jobs/{job_id}/cancel")
        assert cancelled.status_code == 200, cancelled.text
        assert cancelled.json()["cancel_requested"] is True
        [(state, error)] = _wait_job(
            tmp_path, "SELECT state, error_code FROM owa_jobs WHERE id=? AND state='cancelled'", (job_id,)
        )
        assert (state, error) == ("cancelled", "owa_job_cancelled")

        repeat = client.post(base + f"/omniweave/jobs/{job_id}/cancel").json()
        again = client.post(base + f"/omniweave/jobs/{job_id}/cancel").json()
        assert repeat == again, "repeated cancellation must answer one consistent receipt"
        assert repeat["state"] == "cancelled" and repeat["receipt"]["outcome"] == "cancelled"

        final = client.get(base + f"/omniweave/jobs/{job_id}").json()
        assert final["phase"] == "cancelled" and final["cancel_requested"] is True

        other = client.post("/api/v1/agents", json={"name": "Second"}).json()
        other_base = f"/api/v1/agents/{other['id']}"
        assert client.get(other_base + f"/omniweave/jobs/{job_id}").status_code == 404
        assert client.post(other_base + f"/omniweave/jobs/{job_id}/cancel").status_code == 404
        assert client.get(base + f"/omniweave/jobs/{uuid.uuid4()}").status_code == 404
        assert client.post(base + f"/omniweave/jobs/{uuid.uuid4()}/cancel").status_code == 404

        [(run_id,)] = _rows(tmp_path, "SELECT run_id FROM owa_jobs WHERE id=?", (job_id,))
        assert _rows(tmp_path, "SELECT state FROM runs WHERE id=?", (run_id,)) == [("cancelled",)]
        assert _rows(tmp_path, "SELECT count(*) FROM leases WHERE run_id=? AND state='held'", (run_id,)) == [(0,)]
        assert _rows(tmp_path, "SELECT count(*) FROM actions WHERE run_id=? AND state='ok'", (run_id,)) == [(0,)]
        with sqlite_connection(tmp_path / "data/omni.db") as connection:
            [outcome] = connection.execute(
                "SELECT json_extract(receipt,'$.outcome') FROM owa_jobs WHERE id=?", (job_id,)
            ).fetchone()
        assert outcome == "cancelled"


def test_cancel_keeps_lease_and_claim_until_cleanup_completes(tmp_path):
    """The cancel request never releases ownership; only the finished cleanup does."""
    entered = threading.Event()
    release = threading.Event()
    target: list[str] = []
    original_defer = Repositories.omniweave_job_defer

    def gated_defer(self, agent_id, run_id, token, fence):
        if target and run_id == target[0]:
            entered.set()
            assert release.wait(timeout=10), "cleanup gate leaked"
        return original_defer(self, agent_id, run_id, token, fence)

    Repositories.omniweave_job_defer = gated_defer
    try:
        model = JobFlowModel()
        with TestClient(http_app(tmp_path, model=model, omniweave_mode="dispatch")) as client:
            agent = _authorize(client, tmp_path)
            base = _model_base(client, agent)
            receipt = _submit(client, base, tmp_path)
            view = _wait_tasks(client, base, receipt, {"build": "waiting_external"})
            job_id = view["tasks"][0]["job"]["job_id"]
            [(run_id, token, fence)] = _wait_job(
                tmp_path,
                "SELECT owa_jobs.run_id, work_items.claim_token, work_items.lease_fence "
                "FROM owa_jobs JOIN work_items ON work_items.run_id=owa_jobs.run_id "
                "WHERE owa_jobs.id=? AND owa_jobs.state='running'",
                (job_id,),
            )
            target.append(run_id)
            assert client.post(base + f"/omniweave/jobs/{job_id}/cancel").status_code == 200
            assert entered.wait(timeout=10), "the executor never reached its cleanup"

            # Inside the window the cancel receipt is on record but the executor
            # still owns the lease, the claim and therefore the execution slot.
            assert _rows(tmp_path, "SELECT state FROM owa_jobs WHERE id=?", (job_id,)) == [("cancelling",)]
            assert _rows(
                tmp_path,
                "SELECT claim_token, lease_fence, claimed_by IS NOT NULL FROM work_items WHERE run_id=?",
                (run_id,),
            ) == [(token, fence, True)]
            assert _rows(tmp_path, "SELECT count(*) FROM leases WHERE run_id=? AND state='held'", (run_id,)) == [(1,)]
            # Release the cleanup; the executor finishes its defer and everything converges.
            release.set()
            [(state, error)] = _wait_job(
                tmp_path, "SELECT state, error_code FROM owa_jobs WHERE id=? AND state='cancelled'", (job_id,)
            )
            assert (state, error) == ("cancelled", "owa_job_cancelled")
            assert _rows(
                tmp_path,
                "SELECT claim_token IS NULL, lease_fence IS NULL, state FROM work_items WHERE run_id=?",
                (run_id,),
            ) == [(True, True, "cancelled")]
            assert _rows(tmp_path, "SELECT count(*) FROM leases WHERE run_id=? AND state='held'", (run_id,)) == [(0,)]
            assert _rows(tmp_path, "SELECT state FROM runs WHERE id=?", (run_id,)) == [("cancelled",)]
    finally:
        Repositories.omniweave_job_defer = original_defer
        release.set()


def test_goal_revision_cancels_undispatched_job_without_leaks(tmp_path, monkeypatch):
    """A revision cancels a queued, undispatched job in the same transaction; nothing replays."""
    claim = LeaseRepositories.claim_runs

    def blocked_job_claims(self, limit, *, include_owa_deliveries=False, include_owa_jobs=True):
        # Hold managed-job Runs back from dispatch so the revision observes the
        # job while it is still queued, deterministically.
        return claim(self, limit, include_owa_deliveries=include_owa_deliveries, include_owa_jobs=False)

    monkeypatch.setattr(LeaseRepositories, "claim_runs", blocked_job_claims)
    model = JobFlowModel()
    with TestClient(http_app(tmp_path, model=model, omniweave_mode="dispatch")) as client:
        agent = _authorize(client, tmp_path)
        base = _model_base(client, agent)
        receipt = _submit(client, base, tmp_path)
        view = _wait_tasks(client, base, receipt, {"build": "waiting_external"})
        job_id = view["tasks"][0]["job"]["job_id"]
        ticket_id = view["ticket_id"]
        session_id = receipt["session_id"]
        detail = client.get(base + f"/omniweave/jobs/{job_id}").json()
        assert detail["phase"] == "waiting" and detail["state"] == "queued"
        [(run_id,)] = _rows(tmp_path, "SELECT run_id FROM owa_jobs WHERE id=?", (job_id,))

        revised = client.post(
            base + "/turns",
            json={
                "key": "revise-1",
                "session_id": session_id,
                "message": "Add a feature",
                "goal_update": {"ticket_id": ticket_id, "expected_goal_version": 1},
            },
        )
        assert revised.status_code == 202, revised.text
        [(state, error, cancelled_at)] = _wait_job(
            tmp_path,
            "SELECT state, error_code, cancel_requested_at IS NOT NULL FROM owa_jobs WHERE id=? AND state='cancelled'",
            (job_id,),
        )
        assert (state, error, cancelled_at) == ("cancelled", "owa_job_cancelled", True)
        assert _rows(tmp_path, "SELECT state FROM runs WHERE id=?", (run_id,)) == [("cancelled",)]
        # The cancelled Run's work item converges to the terminal work state and
        # the queued job never held a claim.
        assert _rows(
            tmp_path,
            "SELECT state, claim_token IS NULL, lease_fence IS NULL FROM work_items WHERE run_id=?",
            (run_id,),
        ) == [("cancelled", True, True)]
        assert _rows(tmp_path, "SELECT count(*) FROM leases WHERE run_id=? AND state='held'", (run_id,)) == [(0,)]
        assert _rows(tmp_path, "SELECT count(*) FROM actions WHERE run_id=?", (run_id,)) == [(0,)]

        final = client.get(base + f"/omniweave/jobs/{job_id}").json()
        assert final["phase"] == "cancelled" and final["goal_superseded"] is True
        assert final["current_goal_version"] == 2 and final["receipt"]["outcome"] == "cancelled"

        monkeypatch.setattr(LeaseRepositories, "claim_runs", claim)
        deadline = 200
        while deadline:
            assert _rows(tmp_path, "SELECT count(*) FROM actions WHERE run_id=?", (run_id,)) == [(0,)]
            time.sleep(0.025)
            deadline -= 1
        assert client.get(base + f"/omniweave/jobs/{job_id}").json()["state"] == "cancelled"


def test_plain_turns_progress_while_job_holds_executor(tmp_path):
    model = JobFlowModel()
    with TestClient(http_app(tmp_path, model=model, omniweave_mode="dispatch")) as client:
        agent = _authorize(client, tmp_path)
        base = _model_base(client, agent)
        receipt = _submit(client, base, tmp_path)
        view = _wait_tasks(client, base, receipt, {"build": "waiting_external"})
        job_id = view["tasks"][0]["job"]["job_id"]
        [(run_id,)] = _rows(tmp_path, "SELECT run_id FROM owa_jobs WHERE id=?", (job_id,))
        # The session lease exists only once the detached Run is claimed; wait
        # for the claim instead of racing the dispatcher's 0.25s wake.
        _wait_job(tmp_path, "SELECT id FROM owa_jobs WHERE id=? AND state='running'", (job_id,))
        assert _rows(tmp_path, "SELECT count(*) FROM leases WHERE run_id=? AND state='held'", (run_id,)) == [(1,)]

        other = client.post("/api/v1/agents", json={"name": "Second"}).json()
        other_base = _model_base(client, other["id"])
        plain = client.post(other_base + "/turns", json={"key": "plain-1", "message": "Status check please"}).json()
        assert plain["run_id"]
        deadline = 400
        state = ""
        while deadline:
            row = client.get(other_base + f"/runs/{plain['run_id']}").json()
            state = row["state"]
            if state in ("succeeded", "failed", "cancelled", "interrupted"):
                break
            time.sleep(0.025)
            deadline -= 1
        assert state == "succeeded", state
        messages = client.get(other_base + f"/sessions/{plain['session_id']}/messages").json()["items"]
        assert [m["content"] for m in messages if m["role"] == "assistant"] == ["All systems nominal"]
        # The job kept its execution slot and lease the whole time.
        assert _rows(tmp_path, "SELECT count(*) FROM leases WHERE run_id=? AND state='held'", (run_id,)) == [(1,)]
        assert _rows(tmp_path, "SELECT state FROM owa_jobs WHERE id=?", (job_id,)) == [("running",)]


def test_defer_releases_claim_of_terminal_job(tmp_path):
    """A terminal receipt wins: the defer path releases a leftover claim without rewriting it."""
    model = JobFlowModel()
    with TestClient(http_app(tmp_path, model=model, omniweave_mode="dispatch")) as client:
        agent = _authorize(client, tmp_path)
        base = _model_base(client, agent)
        receipt = _submit(client, base, tmp_path)
        view = _wait_tasks(client, base, receipt, {"build": "waiting_external"})
        job_id = view["tasks"][0]["job"]["job_id"]
        [(run_id, token, fence)] = _wait_job(
            tmp_path,
            "SELECT owa_jobs.run_id, work_items.claim_token, work_items.lease_fence "
            "FROM owa_jobs JOIN work_items ON work_items.run_id=owa_jobs.run_id "
            "WHERE owa_jobs.id=? AND owa_jobs.state='running'",
            (job_id,),
        )
        assert client.post(base + f"/omniweave/jobs/{job_id}/cancel").status_code == 200
        _wait_job(tmp_path, "SELECT id FROM owa_jobs WHERE id=? AND state='cancelled'", (job_id,))
    from omni.adapters.persistence.sqlalchemy_access import database_engine

    engine = database_engine(tmp_path / "data/omni.db")
    try:
        with engine.connect() as connection:
            connection.info["omni_write"] = True
            boot = str(uuid.uuid4())
            unit = Repositories(connection, SystemClock(), UUIDs(), boot)
            # Rebuild the leaked state the repair targets: a terminal job whose
            # executor claim and session lease were never released. The new boot
            # takes ownership with a fresh fence: lease_fence_monotone aborts a
            # re-held lease whose fence did not advance, and ck_leases_bounds
            # requires a full boot id.
            fresh = int(fence) + 1
            connection.execute(
                s.work_items.update()
                .where(s.work_items.c.run_id == run_id)
                .values(state="claimed", claimed_by=boot, claim_token=token, lease_fence=fresh)
            )
            connection.execute(
                s.leases.update()
                .where(s.leases.c.run_id == run_id)
                .values(state="held", token=token, boot_id=boot, fence=fresh)
            )
            unit.omniweave_job_defer(agent, run_id, token, fresh)
            assert connection.execute(
                select(s.work_items.c.state, s.work_items.c.claim_token).where(s.work_items.c.run_id == run_id)
            ).first() == ("cancelled", None)
            assert connection.execute(select(s.leases.c.state).where(s.leases.c.run_id == run_id)).first() == (
                "released",
            )
            assert (
                connection.execute(
                    select(s.owa_jobs.c.state, s.owa_jobs.c.receipt_sha256).where(s.owa_jobs.c.id == job_id)
                ).first()[0]
                == "cancelled"
            )
            # A stale fence can neither release nor rewrite anything.
            connection.execute(
                s.work_items.update()
                .where(s.work_items.c.run_id == run_id)
                .values(state="claimed", claimed_by=boot, claim_token=token, lease_fence=fresh)
            )
            with pytest.raises(BusinessError, match="stale executor"):
                unit.omniweave_job_defer(agent, run_id, token, fresh + 1)
    finally:
        engine.dispose()


if __name__ == "__main__":
    home = Path(sys.argv[1])
    phase = sys.argv[2]
    crash_at(phase)
    model = JobFlowModel()
    with TestClient(http_app(home, model=model, omniweave_mode="dispatch")) as client:
        agent = _authorize(client, home)
        base = _model_base(client, agent)
        policy = client.get(base + "/routing").json()
        saved = client.put(
            base + "/routing",
            json={
                "revision": policy["revision"],
                "value": {**POLICY_DEFAULT, "kernel": {"safe_resume": True, "background_learning": False}},
            },
        )
        assert saved.status_code == 200, saved.text
        _submit(client, base, home)
        while True:
            for approval in client.get(base + "/approvals").json()["items"]:
                if approval["state"] == "pending":
                    client.post(
                        base + f"/approvals/{approval['id']}",
                        json={"allowed": True, "policy_revision": approval["policy_revision"]},
                    )
            time.sleep(0.05)
