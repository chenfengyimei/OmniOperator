"""Managed long jobs: stable ids, fenced receipts, idempotent cancellation and crash recovery."""

import os
import subprocess
import sys
import time
from pathlib import Path

import pytest
from fastapi.testclient import TestClient
from omni_test_support.v2.v2_action_review_helpers import review_request
from sqlalchemy import select

from omni.adapters.persistence import schema as s
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
