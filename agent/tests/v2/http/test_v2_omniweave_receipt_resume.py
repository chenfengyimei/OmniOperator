"""External actions with definite receipts resume OWA Workers from checkpoints."""

import os
import subprocess
import sys
import time
from pathlib import Path

from fastapi.testclient import TestClient

from omni.adapters.persistence.sqlite_access import sqlite_connection
from omni.application.engine import Application
from omni.composition.http import http_app
from omni.domain.background_review import REVIEW_PROMPT
from omni.domain.contracts import Invocation, ModelReply
from omni.domain.routing import POLICY_DEFAULT

CRASH = 78


def _authorize(client, home):
    client.headers["Authorization"] = "Bearer " + (home / "run/owner.token").read_text(encoding="utf-8")
    return client.get("/api/v1/agents").json()["items"][0]["id"]


def _select_main(client, base):
    selection = client.put(base + "/omniweave/main-session", json={"session_id": None})
    assert selection.status_code == 200, selection.text
    assert selection.json()["enabled"] is True
    return selection.json()["session_id"]


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


def crash_at(phase):
    if phase == "settled":
        original = Application._tool_message_content

        async def wrapped(self, agent_id, run_id, messages, result, settings, tool_name):
            if tool_name == "files_write":
                os._exit(CRASH)
            return await original(self, agent_id, run_id, messages, result, settings, tool_name)

        Application._tool_message_content = wrapped
    else:
        raise AssertionError(f"Unknown crash phase {phase}")


class WorkerModel:
    async def complete(self, messages, tools, settings):
        if messages[0]["content"] == REVIEW_PROMPT:
            return ModelReply("No reusable procedure in this test turn", cost_micro=0)
        if str(messages[0]["content"]).startswith("OMNI_CONTEXT_SUMMARY_V1"):
            return ModelReply("Campus receipt resume", cost_micro=0)
        last = messages[-1]
        current = str(next(row["content"] for row in reversed(messages) if row["role"] == "user"))
        if not current.startswith("OmniWeave Worker."):
            if last["role"] == "tool":
                return ModelReply("Plan queued in the main conversation", cost_micro=0)
            tasks = [
                {
                    "key": "writer",
                    "goal": "write the receipt",
                    "profile": "coding",
                    "tools": ["files_write", "files_read"],
                    "max_calls": 8,
                }
            ]
            return ModelReply("Plan", (Invocation("plan", "owa_plan", {"tasks": tasks}),), cost_micro=0)
        if last["role"] == "tool":
            return ModelReply("Receipt verified, continuation complete", cost_micro=0)
        return ModelReply(
            "Write",
            (Invocation("write", "files_write", {"path": "receipt.txt", "content": "receipt write"}),),
            cost_micro=0,
        )


class RecoveryModel(WorkerModel):
    async def complete(self, messages, tools, settings):
        if "previous process" in str(messages):
            if messages[-1]["role"] == "tool":
                return ModelReply("Receipt verified, continuation complete", cost_micro=0)
            return ModelReply(
                "Check",
                (Invocation("read", "files_read", {"path": "receipt.txt"}),),
                cost_micro=0,
            )
        return await WorkerModel.complete(self, messages, tools, settings)


def test_receipt_backed_checkpoint_continuation_completes_delivery(tmp_path):
    home = tmp_path / "home"
    launcher = (
        "import runpy,sys; sys.path.insert(0,sys.argv.pop(1)); runpy.run_path(sys.argv.pop(1),run_name='__main__')"
    )
    command = [sys.executable, "-c", launcher, str(Path(__file__).resolve().parents[2]), __file__, str(home)]
    child = subprocess.run(
        [*command, "settled"],
        capture_output=True,
        timeout=90,
        check=False,
        env={**os.environ, "OMNI_TEST_OFFLINE": "1"},
    )
    assert child.returncode == CRASH, child.stderr.decode(errors="replace")

    with TestClient(http_app(home, model=RecoveryModel(), omniweave_mode="dispatch")) as client:
        agent = _authorize(client, home)
        base = _model_base(client, agent)
        session_id = _select_main(client, base)
        closeout = None
        for _ in range(400):
            items = client.get(base + f"/sessions/{session_id}/messages").json()["items"]
            closeout = next(
                (
                    row
                    for row in items
                    if row["role"] == "assistant" and "OmniWeave 交付 · 第 1 版 · 已完成" in row["content"]
                ),
                closeout,
            )
            if closeout is not None:
                break
            time.sleep(0.025)
        assert closeout is not None, items
        assert closeout["omniweave_delivery"]["outcome"] == "completed"
        assert client.get(base + "/approvals").json()["items"] == []
        [write] = _rows(home, "SELECT effect FROM actions WHERE tool='files_write'")
        assert write[0] == "applied"
        assert _rows(home, "SELECT count(*) FROM actions WHERE tool='files_read'") == [(1,)]
        assert _rows(
            home,
            "SELECT t.task_key, r.state FROM owa_tasks t JOIN owa_task_attempts a ON a.task_id=t.id JOIN runs r ON r.id=a.run_id",
        ) == [("writer", "succeeded")]
        workspaces = list((home / "owa-workspaces").rglob("receipt.txt"))
        assert len(workspaces) == 1 and workspaces[0].read_text(encoding="utf-8") == "receipt write"


if __name__ == "__main__":
    home = Path(sys.argv[1])
    crash_at(sys.argv[2])
    with TestClient(http_app(home, model=WorkerModel(), omniweave_mode="dispatch")) as client:
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
        session_id = _select_main(client, base)
        client.post(
            base + "/turns", json={"key": "receipt", "session_id": session_id, "message": "Campus receipt resume"}
        )
        [approval] = _wait_approval(client, base)
        client.post(
            base + f"/approvals/{approval['id']}",
            json={"allowed": True, "policy_revision": approval["policy_revision"]},
        )
        while True:
            time.sleep(0.05)
